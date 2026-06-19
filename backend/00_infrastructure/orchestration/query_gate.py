from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from datetime import timedelta
import uuid
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
QueryLatencyEstimator = import_module("00_infrastructure.orchestration.sla.latency_estimator").QueryLatencyEstimator
PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy
QueryCadencePolicy = import_module("00_infrastructure.orchestration.query_dispatcher.cadence_policy").QueryCadencePolicy
CandidateQueue = import_module("00_infrastructure.orchestration.query_dispatcher.candidate_queue").CandidateQueue
DynamicCooldownPolicy = import_module("00_infrastructure.orchestration.query_dispatcher.cooldown_policy").DynamicCooldownPolicy
QueryPreflight = import_module("00_infrastructure.orchestration.session_health.query_preflight").QueryPreflight
HandoffBuilder = import_module("00_infrastructure.orchestration.query_dispatcher.handoff_signal").HandoffBuilder
latency_metrics = import_module("00_infrastructure.orchestration.query_dispatcher.latency_metrics")
ReuseHealthScorer = import_module("00_infrastructure.orchestration.session_reuse.reuse_health").ReuseHealthScorer


@dataclass
class QueryGateDecision:
    ok: bool
    lease_id: str = ""
    wait_seconds: float = 0.0
    reason: str = ""
    next_allowed_at: str = ""


class SmartQueryGate:
    """Global business-query coordinator.

    This is intentionally narrow: it only coordinates official business date
    queries.  CF, login, waiting room and browser production are not globally
    serialized here because the user wants those stages to stay flexible.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "enabled", True)) and self.store is not None

    @property
    def global_gap_s(self) -> float:
        try:
            return max(1.0, float(QueryCadencePolicy(self.config).snapshot().interval_seconds))
        except Exception:
            base = max(
                1.0,
                float(
                    getattr(self.cfg, "target_success_interval_seconds", 0)
                    or getattr(self.cfg, "target_global_query_interval_seconds", 30.0)
                    or 30.0
                ),
            )
            try:
                return max(1.0, float(PeakWindowPolicy(self.config).effective_interval(base)))
            except Exception:
                return base

    @property
    def per_session_gap_s(self) -> float:
        try:
            return max(0.0, float(QueryCadencePolicy(self.config).snapshot().per_session_gap_seconds))
        except Exception:
            return max(1.0, float(getattr(self.cfg, "per_session_min_query_interval_seconds", 180.0) or 180.0))

    @property
    def failure_gap_s(self) -> float:
        try:
            return max(0.0, float(QueryCadencePolicy(self.config).snapshot().failure_gap_seconds))
        except Exception:
            return max(1.0, float(getattr(self.cfg, "query_failure_cooldown_seconds", 60.0) or 60.0))

    @property
    def lease_s(self) -> float:
        try:
            return max(10.0, float(QueryCadencePolicy(self.config).snapshot().active_query_lease_seconds))
        except Exception:
            return max(30.0, float(getattr(self.cfg, "active_query_lease_seconds", 90.0) or 90.0))

    def cadence(self) -> dict[str, Any]:
        try:
            return QueryCadencePolicy(self.config).snapshot().__dict__
        except Exception as exc:
            return {"mode": "normal", "interval_seconds": self.global_gap_s, "error": repr(exc)}

    def latency(self) -> dict[str, Any]:
        try:
            return QueryLatencyEstimator(self.store, self.config).estimate()
        except Exception as exc:
            default = float(getattr(self.cfg, "query_launch_lead_seconds", 12.0) or 12.0)
            return {"sample_count": 0, "lead_seconds": default, "source": "fallback", "error": repr(exc)}

    def peak_mode(self) -> dict[str, Any]:
        try:
            return PeakWindowPolicy(self.config).current(clock.now_dt()).to_dict()
        except Exception as exc:
            return {"mode": "normal", "reason": "高峰策略不可用", "error": repr(exc)}

    def _ready_hot_session_candidates(self, state: dict[str, Any], slots_snapshot: dict[str, Any], *, exclude_key: str = "") -> list[dict[str, Any]]:
        sessions = state.get("sessions") if isinstance(state.get("sessions"), dict) else {}
        scorer = ReuseHealthScorer()
        out: list[dict[str, Any]] = []
        for key, sess in sessions.items():
            if key == exclude_key or not isinstance(sess, dict):
                continue
            if int(sess.get("success_count") or 0) <= 0:
                continue
            if str(sess.get("state") or "") in {"recovering", "terminal", "querying"}:
                continue
            mode = str(sess.get("last_cooldown_mode") or "")
            if any(x in mode for x in ("failure", "rate_limited", "failed_fetch", "auth_or_cf", "page_view_blocked", "terminal")):
                continue
            slot_id = str(sess.get("slot_id") or "")
            slot = (slots_snapshot or {}).get(slot_id, {}) if slot_id else {}
            if not isinstance(slot, dict) or str(slot.get("state") or "") != "running":
                continue
            # Scheduler state is append-only across many producer restarts.
            # Without binding a session to the currently running slot
            # generation, an old successful key such as
            # slot_07/round_xxxx/yesterday can inherit today's live page state
            # from slot_07 and become a false hot candidate.  That makes cold
            # sessions defer even though the supposed hot pool is stale.
            round_started_at = str(slot.get("round_started_at") or "")
            if round_started_at and round_started_at not in str(key):
                continue
            if bool(slot.get("drain_requested")):
                continue
            # Important: a session that once succeeded is not a real hot
            # candidate if the *current browser page* has fallen back to CF,
            # login, waiting room, 1015, network error, etc.  The 14:00-14:14
            # gap showed exactly this false-hot-pool pattern: cold sessions
            # deferred while all "hot" sessions were actually unrecoverable or
            # still recovering.  ReuseHealthScorer binds scheduler session
            # evidence to the live slot page before we allow this deferral.
            health = scorer.score(slot if isinstance(slot, dict) else {}, sess)
            if bool(health.query_eligible) and health.pool_role == "hot_query":
                out.append(
                    {
                        "session_key": key,
                        "slot_id": slot_id,
                        "pool_role": health.pool_role,
                        "query_eligible": health.query_eligible,
                        "reuse_score": health.reuse_score,
                        "next_query_eta_seconds": health.next_query_eta_seconds,
                        "live_page_stage": slot.get("live_page_stage") if isinstance(slot, dict) else "",
                        "live_page_reason": slot.get("live_page_reason") if isinstance(slot, dict) else "",
                        "stage": slot.get("stage") if isinstance(slot, dict) else "",
                        "success_count": int(sess.get("success_count") or 0),
                        "last_success_at": sess.get("last_success_at") or "",
                    }
                )
        return sorted(out, key=lambda x: int(x.get("reuse_score") or 0), reverse=True)

    def _ready_hot_session_exists(self, state: dict[str, Any], slots_snapshot: dict[str, Any], *, exclude_key: str = "") -> bool:
        return bool(self._ready_hot_session_candidates(state, slots_snapshot, exclude_key=exclude_key))

    def _launch_due(self, last_success: Any, lead_seconds: float) -> tuple[str, str]:
        dt = clock.parse_ts(last_success)
        if not dt:
            return "", ""
        interval = self.global_gap_s
        tolerance = 0.0 if interval <= 2.0 else max(0.0, float(getattr(self.cfg, "early_success_tolerance_seconds", 5.0) or 5.0))
        target_dt = dt + timedelta(seconds=interval)
        # The launch time is intentionally earlier than the target success time,
        # but never earlier than last_success + tolerance.  That keeps the API
        # cadence centered on the SLA instead of starting only after the SLA
        # has already expired.
        raw_launch_dt = target_dt - timedelta(seconds=max(0.0, float(lead_seconds or 0.0)))
        min_launch_dt = dt + timedelta(seconds=tolerance)
        launch_dt = max(min_launch_dt, raw_launch_dt)
        return target_dt.isoformat(timespec="seconds"), launch_dt.isoformat(timespec="seconds")

    def _latest_success_from_history(self) -> str:
        try:
            rows = self.store.ticket_history(200)
            best = ""
            for row in rows:
                ts = str(row.get("queried_at") or row.get("ts") or "")
                days = row.get("normalized_days") or row.get("days") or []
                valid = bool(row.get("valid_query_success")) if "valid_query_success" in row else bool(len(days) > 0)
                if valid and ts > best:
                    best = ts
            return best
        except Exception:
            return ""

    def state(self) -> dict[str, Any]:
        data = self.store.scheduler_state() if self.store else {}
        if data.get("last_success_at"):
            return data
        latest = self._latest_success_from_history()
        if latest:
            data["last_success_at"] = latest
        return data

    def reserve(self, *, slot_id: str, round_id: str, session_key: str, live_round: int | None = None) -> QueryGateDecision:
        if not self.enabled:
            return QueryGateDecision(ok=True, lease_id=f"disabled-{uuid.uuid4().hex}")

        now = clock.now_dt()
        now_s = now.isoformat(timespec="seconds")
        lease_id = uuid.uuid4().hex
        slots_snapshot = self.store.read_slots() if self.store else {}

        def mutate(state: dict[str, Any]) -> QueryGateDecision:
            state.setdefault("sessions", {})
            sessions = state["sessions"]
            sess = sessions.setdefault(session_key, {"session_key": session_key, "slot_id": slot_id, "round_id": round_id})
            preflight = QueryPreflight().check((slots_snapshot or {}).get(slot_id, {}), sess)
            if not preflight.allowed:
                sess["state"] = "preflight_blocked"
                sess["last_preflight_state"] = preflight.health.state
                sess["last_preflight_reason"] = preflight.reason
                sess["last_preflight_at"] = now_s
                state["last_decision"] = {
                    "ok": False,
                    "reason": preflight.reason,
                    "slot_id": slot_id,
                    "round_id": round_id,
                    "session_key": session_key,
                    "health": preflight.health.to_dict(),
                    "at": now_s,
                }
                return QueryGateDecision(
                    ok=False,
                    wait_seconds=preflight.wait_seconds,
                    reason=preflight.reason,
                    next_allowed_at=preflight.next_allowed_at,
                )

            reuse_health = ReuseHealthScorer().score((slots_snapshot or {}).get(slot_id, {}), sess).to_dict()
            sess["last_reuse_score"] = reuse_health.get("reuse_score")
            sess["last_pool_role"] = reuse_health.get("pool_role")
            sess["last_scheduler_status"] = reuse_health.get("scheduler_status")
            sess["last_scheduler_reason"] = reuse_health.get("reason")

            latest = state.get("last_success_at") or self._latest_success_from_history()
            if latest and not state.get("last_success_at"):
                state["last_success_at"] = latest

            active = state.get("active_query") if isinstance(state.get("active_query"), dict) else None
            if active:
                exp = clock.parse_ts(active.get("expires_at"))
                if exp and exp > now:
                    cadence = self.cadence()
                    poll_cap = float(cadence.get("wait_poll_seconds") or getattr(self.cfg, "wait_poll_seconds", 2.0) or 2.0)
                    return QueryGateDecision(
                        ok=False,
                        wait_seconds=min(max(0.1, poll_cap), max(0.1, (exp - now).total_seconds())),
                        reason="business_api_gate_busy",
                        next_allowed_at=active.get("expires_at") or "",
                    )
                state["active_query"] = None

            cadence = self.cadence()
            if cadence.get("mode") == "release_burst" and bool(cadence.get("same_second_guard", True)):
                last_reserved = str(state.get("last_reservation_at") or "")
                if last_reserved[:19] == now_s[:19]:
                    return QueryGateDecision(False, wait_seconds=float(cadence.get("wait_poll_seconds") or 0.3), reason="same_second_guard", next_allowed_at=clock.add_seconds(now, 1.0))

            last_success_raw = state.get("last_success_at")
            last_success = clock.parse_ts(last_success_raw)
            if last_success:
                lead = float((self.latency() or {}).get("lead_seconds") or getattr(self.cfg, "query_launch_lead_seconds", 12.0) or 12.0)
                cadence = self.cadence()
                if cadence.get("mode") == "release_burst":
                    lead = 0.0
                target_success_at, launch_at = self._launch_due(last_success_raw, lead)
                global_due = launch_at if bool(getattr(self.cfg, "allow_early_query_launch", True)) else target_success_at
                wait = clock.seconds_until(global_due)
                if wait > 0:
                    state["last_decision"] = {
                        "ok": False,
                        "reason": "global_success_gap",
                        "slot_id": slot_id,
                        "round_id": round_id,
                        "session_key": session_key,
                        "next_allowed_at": global_due,
                        "next_target_success_at": target_success_at,
                        "next_query_launch_at": launch_at,
                        "query_launch_lead_seconds": lead,
                        "at": now_s,
                    }
                    return QueryGateDecision(False, wait_seconds=wait, reason="global_success_gap", next_allowed_at=global_due)

            next_session_at = sess.get("next_query_at") or ""
            wait = clock.seconds_until(next_session_at)
            if wait > 0:
                # Normal 30s freshness cannot be achieved if fewer than six
                # hot sessions survive the 180s per-session cooldown.  When
                # the global query window is already due and this session has
                # rested for at least the configured emergency threshold, allow
                # it to cut the remaining per-session cooldown.  This is the
                # "no blank window" fallback; release_burst already uses a
                # 1-second per-session gap.
                bypass_after = max(0.0, float(getattr(self.cfg, "normal_session_cooldown_bypass_after_seconds", 30.0) or 30.0))
                sess_last = clock.parse_ts(sess.get("last_success_at"))
                rested = (now - sess_last).total_seconds() if sess_last else 0.0
                cooldown_mode = str(sess.get("last_cooldown_mode") or "")
                session_state = str(sess.get("state") or "")
                failure_cooldown = any(x in cooldown_mode for x in ("failure", "rate_limited", "failed_fetch", "auth_or_cf", "page_view_blocked", "terminal"))
                can_bypass = (
                    cadence.get("mode") != "release_burst"
                    and int(sess.get("success_count") or 0) > 0
                    and bool(last_success)
                    and rested >= bypass_after
                    and session_state not in {"recovering", "terminal"}
                    and not failure_cooldown
                )
                if can_bypass:
                    state["last_decision"] = {
                        "ok": True,
                        "reason": "session_cooldown_bypassed_for_sla",
                        "slot_id": slot_id,
                        "round_id": round_id,
                        "session_key": session_key,
                        "original_next_allowed_at": next_session_at,
                        "rested_seconds": round(rested, 1),
                        "at": now_s,
                    }
                else:
                    state["last_decision"] = {
                        "ok": False,
                        "reason": "session_cooldown",
                        "slot_id": slot_id,
                        "round_id": round_id,
                        "session_key": session_key,
                        "next_allowed_at": next_session_at,
                        "at": now_s,
                    }
                    return QueryGateDecision(False, wait_seconds=wait, reason="session_cooldown", next_allowed_at=next_session_at)

            # If a cold session has never produced a successful days result,
            # do not let it steal the single business API gate when a proven
            # hot session is immediately ready.  This directly targets the
            # observed long gaps where many cold/recovering sessions produced
            # auth_or_cf/page_view_failed while hot reuse opportunities waited.
            ready_hot_candidates = self._ready_hot_session_candidates(state, slots_snapshot, exclude_key=session_key)
            if int(sess.get("success_count") or 0) <= 0 and int(sess.get("failure_count") or 0) > 0 and ready_hot_candidates:
                state["last_decision"] = {
                    "ok": False,
                    "reason": "cold_session_deferred_for_hot_pool",
                    "slot_id": slot_id,
                    "round_id": round_id,
                    "session_key": session_key,
                    "reuse_health": reuse_health,
                    "ready_hot_candidates": ready_hot_candidates[:5],
                    "ready_hot_candidate_count": len(ready_hot_candidates),
                    "at": now_s,
                }
                return QueryGateDecision(False, wait_seconds=0.5, reason="cold_session_deferred_for_hot_pool", next_allowed_at=now_s)

            expires_at = clock.add_seconds(now, self.lease_s)
            state["active_query"] = {
                "lease_id": lease_id,
                "slot_id": slot_id,
                "round_id": round_id,
                "session_key": session_key,
                "live_round": live_round,
                "started_at": now_s,
                "expires_at": expires_at,
            }
            sess["state"] = "querying"
            sess["last_reserved_at"] = now_s
            state["last_reservation_at"] = now_s
            reserve_latency = latency_metrics.build_reserve_latency(state, now_s=now_s, slot_id=slot_id, session_key=session_key)
            if reserve_latency:
                state["last_reserve_latency"] = reserve_latency
            state["last_decision"] = {
                "ok": True,
                "reason": "reserved",
                "slot_id": slot_id,
                "round_id": round_id,
                "session_key": session_key,
                "lease_id": lease_id,
                "reuse_health": reuse_health,
                "latency": reserve_latency,
                "at": now_s,
            }
            return QueryGateDecision(True, lease_id=lease_id, reason="reserved")

        decision = self.store.mutate_scheduler_state(mutate)
        if not decision.ok and str(decision.reason or "").startswith("preflight_"):
            try:
                self.store.update_slot(
                    slot_id,
                    smart_query_state="preflight_blocked",
                    last_reason=f"smart_query_wait:{decision.reason}",
                    last_reason_zh=f"查询前健康预检未通过：{decision.reason}，不占用业务 API 闸门",
                    smart_query_wait_reason=decision.reason,
                    smart_query_next_allowed_at=decision.next_allowed_at,
                    smart_query_wait_seconds=round(float(decision.wait_seconds or 0), 1),
                )
            except Exception:
                pass
        return decision

    def complete(self, lease_id: str, *, success: bool, slot_id: str, round_id: str, session_key: str, result: dict[str, Any] | None = None, message: str = "") -> dict[str, Any]:
        if not self.enabled or not lease_id:
            return {}
        result = result or {}
        now = clock.now_dt()
        now_s = now.isoformat(timespec="seconds")
        days = result.get("normalized_days") or result.get("days") or []
        nearest = sorted([str(x)[:10] for x in days if str(x or "").strip()])[:1]
        jitter = clock.jitter_seconds(getattr(self.cfg, "per_session_query_jitter_seconds", [0, 30]), (0, 30))
        failure_jitter = clock.jitter_seconds(getattr(self.cfg, "query_failure_jitter_seconds", [0, 30]), (0, 30))
        is_burst = self.cadence().get("mode") == "release_burst"
        cooldown_policy = DynamicCooldownPolicy(self.config, self.store)
        quality = result.get("result_quality") if isinstance(result.get("result_quality"), dict) else {}
        completed_empty = bool(quality.get("completed") and quality.get("empty"))
        failure_kind = " ".join(
            str(x or "")
            for x in (
                message,
                result.get("needs_recover"),
                result.get("result_class"),
                result.get("error_type"),
                result.get("reason"),
            )
        ).lower()

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            state.setdefault("sessions", {})
            sessions = state["sessions"]
            sess = sessions.setdefault(session_key, {"session_key": session_key, "slot_id": slot_id, "round_id": round_id})
            active = state.get("active_query") if isinstance(state.get("active_query"), dict) else None
            completion_latency = latency_metrics.build_completion_latency(active, now_s=now_s)
            if active and active.get("lease_id") == lease_id:
                state["active_query"] = None
            sess["slot_id"] = slot_id
            sess["round_id"] = round_id
            sess["last_completed_at"] = now_s
            sess["last_message"] = message
            if success:
                cool = cooldown_policy.after_success(now=now, base_gap_seconds=self.per_session_gap_s, jitter_seconds=0 if is_burst else jitter)
                next_at = cool.next_query_at
                state["last_success_at"] = now_s
                state["last_success_slot"] = slot_id
                state["last_success_round"] = round_id
                state["next_global_query_at"] = clock.add_seconds(now, self.global_gap_s)
                sess["last_success_at"] = now_s
                sess["next_query_at"] = next_at
                sess["success_count"] = int(sess.get("success_count") or 0) + 1
                sess["last_nearest_date"] = nearest[0] if nearest else ""
                sess["last_days_count"] = len(days)
                sess["last_cooldown_mode"] = cool.mode
                sess["last_cooldown_reason"] = cool.reason
                sess["last_cooldown_seconds"] = round(cool.cooldown_seconds, 1)
                sess["last_release_anchor_at"] = cool.anchor_at
                sess["state"] = "ready_warm"
            elif completed_empty:
                # Official API returned but yielded no dates/token.  This is
                # not a useful success and must not refresh last_success_at, but
                # the browser session itself may still be healthy.  Keep it warm
                # with a short dynamic cooldown rather than marking it as a
                # broken/recovering session for 180s.
                cool = cooldown_policy.after_success(now=now, base_gap_seconds=max(30.0, self.global_gap_s), jitter_seconds=0)
                sess["next_query_at"] = cool.next_query_at
                sess["last_empty_at"] = now_s
                sess["empty_count"] = int(sess.get("empty_count") or 0) + 1
                sess["last_days_count"] = 0
                sess["state"] = "ready_warm"
                sess["last_cooldown_mode"] = "empty_" + cool.mode
                sess["last_cooldown_reason"] = "空日期返回：不刷新全局成功 SLA，短冷却后复查"
                sess["last_cooldown_seconds"] = round(cool.cooldown_seconds, 1)
            else:
                # A failed probe (CF/login/auth_or_cf/network/JS fetch error)
                # must also release the gate and cool down this session.  If we
                # leave next_query_at empty, recovering sessions can re-acquire
                # the single business API gate every few seconds and starve the
                # healthy hot pool.
                try:
                    cool = cooldown_policy.after_failure_kind(now=now, base_gap_seconds=self.failure_gap_s, jitter_seconds=0 if is_burst else failure_jitter, failure_kind=failure_kind)
                except AttributeError:
                    cool = cooldown_policy.after_failure(now=now, base_gap_seconds=self.failure_gap_s, jitter_seconds=0 if is_burst else failure_jitter)
                next_at = cool.next_query_at
                sess["failure_count"] = int(sess.get("failure_count") or 0) + 1
                sess["next_query_at"] = next_at
                sess["state"] = "recovering"
                sess["last_failure_at"] = now_s
                sess["last_failure_cooldown_seconds"] = round(cool.cooldown_seconds, 1)
                sess["last_cooldown_mode"] = cool.mode
                sess["last_cooldown_reason"] = cool.reason
            state["last_completion"] = {
                "success": success,
                "slot_id": slot_id,
                "round_id": round_id,
                "session_key": session_key,
                "message": message,
                "at": now_s,
                "latency": completion_latency,
                "fast_handoff": bool(result.get("fast_handoff") or not success),
            }
            state["last_completion_latency"] = completion_latency
            return dict(state)

        state_after = self.store.mutate_scheduler_state(mutate)
        try:
            cadence = self.cadence()
            sessions = state_after.get("sessions") if isinstance(state_after.get("sessions"), dict) else {}
            slots = self.store.read_slots() if self.store else {}
            candidates = CandidateQueue(int(cadence.get("candidate_count") or 3)).build(slots, sessions)
            prefer_slots = [str(x.get("slot_id")) for x in candidates if x.get("slot_id") and str(x.get("slot_id")) != slot_id]
            reason = "query_success_handoff" if success else ("query_failed_fast_handoff" if bool(result.get("fast_handoff")) else "query_failed_handoff")
            previous = self.store.query_handoff()
            signal = HandoffBuilder().build(
                previous,
                reason=reason,
                source_slot=slot_id,
                source_session=session_key,
                success=success,
                prefer_slots=prefer_slots,
                payload={
                    "message": message[:300],
                    "quality": result.get("result_quality") if isinstance(result.get("result_quality"), dict) else {},
                    "days_count": len(days),
                    "nearest": nearest[0] if nearest else "",
                    "latency": state_after.get("last_completion_latency") or {},
                },
            )
            self.store.write_query_handoff(signal.to_dict())
        except Exception:
            pass
        return state_after

    def snapshot(self, *, include_sessions: bool = True) -> dict[str, Any]:
        state = self.state()
        sessions = state.get("sessions") if isinstance(state.get("sessions"), dict) else {}
        next_global = state.get("next_global_query_at") or ""
        latency = self.latency()
        cadence = self.cadence()
        peak = cadence.get("peak_mode") or self.peak_mode()
        next_launch = ""
        last_success_value = state.get("last_success_at") or self._latest_success_from_history()
        if last_success_value:
            target_success_at, launch_at = self._launch_due(last_success_value, float(latency.get("lead_seconds") or 0))
            next_global = next_global or target_success_at
            next_launch = launch_at
        payload = {
            "enabled": self.enabled,
            "target_success_interval_seconds": self.global_gap_s,
            "target_global_query_interval_seconds": self.global_gap_s,
            "per_session_min_query_interval_seconds": self.per_session_gap_s,
            "last_success_at": last_success_value,
            "next_global_query_at": next_global,
            "next_target_success_at": next_global,
            "next_query_launch_at": next_launch,
            "next_global_due_in_seconds": round(clock.seconds_until(next_global), 1),
            "next_query_launch_due_in_seconds": round(clock.seconds_until(next_launch), 1),
            "query_launch_lead_seconds": latency.get("lead_seconds"),
            "latency": latency,
            "cadence": cadence,
            "peak_mode": peak,
            "active_query": state.get("active_query") or None,
            "sessions_count": len(sessions),
            "last_decision": state.get("last_decision") or {},
            "last_completion": state.get("last_completion") or {},
            "last_reserve_latency": state.get("last_reserve_latency") or {},
            "last_completion_latency": state.get("last_completion_latency") or {},
            "updated_at": state.get("updated_at") or "",
        }
        try:
            slots = self.store.read_slots() if self.store else {}
            payload["candidate_queue"] = CandidateQueue(int(cadence.get("candidate_count") or 3)).build(slots, sessions)
        except Exception as exc:
            payload["candidate_queue"] = []
            payload["candidate_queue_error"] = repr(exc)
        if include_sessions:
            payload["sessions"] = sessions
        return payload
