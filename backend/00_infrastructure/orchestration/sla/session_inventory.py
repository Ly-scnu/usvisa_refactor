from __future__ import annotations

from importlib import import_module
from typing import Any

from .models import SessionInventoryStats

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy
SessionHealthClassifier = import_module("00_infrastructure.orchestration.session_health.classifier").SessionHealthClassifier


PRE_LOGIN_STAGES = {
    "proxy_acquire",
    "proxy_acquired",
    "cf_gate",
    "waiting_room",
    "real_one_dragon",
}
LOGIN_STANDBY_STAGES = {
    "login",
    "security_questions",
}
TERMINAL_TEXT = ("1015", "access_denied", "proxy_banned", "browser_crashed", "blocked")
UNHEALTHY_PAGES = {
    "cf_challenge",
    "login",
    "security_questions",
    "idp_loading",
    "waiting_room",
    "rate_limit_1015",
    "rate_limit_429",
    "access_denied",
    "network_error",
    "login_failed",
    "callback_not_found",
    "page_not_found",
    "blank",
}
QUERY_READY_PAGES = {"home", "schedule", "site"}


def _live_key(slot_id: str, slot: dict[str, Any]) -> str:
    try:
        round_no = int(slot.get("round") or 0)
    except Exception:
        round_no = 0
    started_at = str(slot.get("round_started_at") or "")
    if round_no > 0 and started_at:
        return f"{slot_id}/round_{round_no:04d}/{started_at}"
    return ""


def _future(value: Any) -> bool:
    return clock.seconds_until(value) > 0


class SessionInventoryAnalyzer:
    """Classify live slots into operational inventory pools.

    This is the user's visible standard for stable cadence, separate from the
    lower-level SLA math:
      - hot query pool: logged-in/schedule sessions that already queried days;
      - login standby pool: sessions at login/KBA boundary, ready to promote;
      - candidate pool: sessions still producing through proxy/CF/waiting room.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)
        self.health_classifier = SessionHealthClassifier()

    def _targets(self) -> tuple[int, int, int]:
        hot = int(getattr(self.cfg, "target_hot_query_sessions", 4) or 4)
        login = int(getattr(self.cfg, "target_login_standby_sessions", 2) or 2)
        candidate = int(getattr(self.cfg, "target_candidate_sessions", 2) or 2)
        try:
            peak = PeakWindowPolicy(self.config).current(clock.now_dt())
            if peak.mode in {"prewarm", "peak"}:
                hot = max(hot, int(getattr(self.cfg, "peak_target_hot_query_sessions", 5) or 5), int(peak.min_hot_sessions or 0))
                candidate = max(candidate, int(getattr(self.cfg, "peak_target_candidate_sessions", 3) or 3))
        except Exception:
            pass
        max_slots = max(2, min(10, int(getattr(self.cfg, "max_slots", 10) or 10)))
        return min(max_slots, hot), min(max_slots, login), min(max_slots, candidate)

    def snapshot(self, *, active_count: int | None = None) -> SessionInventoryStats:
        enabled = bool(getattr(self.cfg, "inventory_enabled", True))
        hot_target, login_target, candidate_target = self._targets()
        max_slots = max(2, min(10, int(getattr(self.cfg, "max_slots", 10) or 10)))
        stats = SessionInventoryStats(
            enabled=enabled,
            target_hot_query_sessions=hot_target,
            target_login_standby_sessions=login_target,
            target_candidate_sessions=candidate_target,
        )
        if not enabled or not self.store:
            stats.reason = "库存水位未启用"
            return stats
        slots = self.store.read_slots()
        scheduler_state = self.store.scheduler_state()
        sessions = scheduler_state.get("sessions") if isinstance(scheduler_state.get("sessions"), dict) else {}
        active_query = scheduler_state.get("active_query") if isinstance(scheduler_state.get("active_query"), dict) else None
        active_query_slot = str(active_query.get("slot_id") or "") if active_query else ""
        stats.active_slots = int(active_count) if active_count is not None else sum(1 for s in slots.values() if isinstance(s, dict) and s.get("state") == "running")

        for slot_id, slot in slots.items():
            if not isinstance(slot, dict) or slot.get("state") != "running":
                continue
            stage = str(slot.get("stage") or "")
            page = str(slot.get("live_page_stage") or "")
            smart_state = str(slot.get("smart_query_state") or "")
            reason_text = " ".join(str(slot.get(k) or "") for k in ["last_reason", "last_reason_zh", "recovery_error_type", "recovery_action", "live_page_stage", "live_page_reason", "smart_query_wait_reason"]).lower()
            key = _live_key(slot_id, slot)
            sess = sessions.get(key) if key else None
            success_count = int(sess.get("success_count") or 0) if isinstance(sess, dict) else int(slot.get("session_successful_queries") or 0)
            session_state = str(sess.get("state") or "") if isinstance(sess, dict) else ""
            next_query_at = str(sess.get("next_query_at") or "") if isinstance(sess, dict) else ""
            health = self.health_classifier.classify(slot, sess if isinstance(sess, dict) else None)
            label = "candidate"
            if health.state == "terminal_risk" or any(x in reason_text for x in TERMINAL_TEXT):
                stats.terminal_risk_sessions += 1
                if success_count > 0:
                    stats.terminal_success_sessions += 1
                label = "terminal_risk"
            elif success_count > 0 and (
                session_state == "recovering"
                or smart_state == "preflight_blocked"
                or health.state in {"recoverable_cf", "recoverable_login", "waiting_room", "rate_limited", "network_bad", "not_ready"}
            ):
                # A session that has queried successfully before is valuable,
                # but if the latest scheduler state is recovering (login/CF/
                # auth_or_cf/failed fetch), it must not be counted as healthy
                # hot capacity.  Otherwise the dashboard says "hot pool 4/4"
                # while all four are actually blocked and repeatedly stealing
                # the query gate.
                stats.recovering_sessions += 1
                stats.recovering_success_sessions += 1
                label = "hot_recovering"
            elif success_count > 0:
                if health.state not in {"ready_query", "cooling", "querying"} and page and page not in QUERY_READY_PAGES and stage != "business_query":
                    stats.recovering_sessions += 1
                    stats.recovering_success_sessions += 1
                    label = "hot_page_not_ready"
                else:
                    stats.hot_query_sessions += 1
                    if health.state == "querying" or session_state == "querying" or slot_id == active_query_slot:
                        stats.ready_query_sessions += 1
                        label = "hot_querying"
                    elif health.state == "cooling" or (next_query_at and _future(next_query_at)):
                        stats.cooling_success_sessions += 1
                        label = "hot_cooling"
                    else:
                        stats.ready_query_sessions += 1
                        label = "hot_ready"
            elif health.state == "recoverable_login" or page in {"login", "security_questions"} or stage in LOGIN_STANDBY_STAGES or "密保" in reason_text or "登录" in reason_text or "login" in reason_text:
                stats.login_standby_sessions += 1
                label = "login_standby"
            elif health.state in {"recoverable_cf", "waiting_room"} or page in {"cf_challenge", "waiting_room", "idp_loading"} or stage in PRE_LOGIN_STAGES:
                stats.candidate_sessions += 1
                label = "candidate"
            elif health.state in {"rate_limited", "network_bad"} or page in {"rate_limit_1015", "rate_limit_429", "access_denied", "network_error", "login_failed", "callback_not_found", "page_not_found", "blank"}:
                stats.recovering_sessions += 1
                label = "recovering_unhealthy_page"
            elif stage == "round_recycle" and "slot_start_stagger" in reason_text:
                stats.candidate_sessions += 1
                label = "candidate_queued"
            elif stage in {"round_recycle", "direct_only_recycle"}:
                stats.recovering_sessions += 1
                label = "recovering"
            else:
                stats.candidate_sessions += 1
                label = "candidate_unknown"
            stats.slots[slot_id] = {
                "pool": label,
                "stage": stage,
                "page": page,
                "smart_query_state": smart_state,
                "session_state": session_state,
                "health_state": health.state,
                "health_reason": health.reason,
                "success_count": success_count,
                "next_query_at": next_query_at,
                "reason": str(slot.get("last_reason_zh") or slot.get("last_reason") or ""),
            }

        stats.hot_deficit = max(0, hot_target - stats.hot_query_sessions)
        stats.login_standby_deficit = max(0, login_target - stats.login_standby_sessions)
        stats.candidate_deficit = max(0, candidate_target - stats.candidate_sessions)
        stats.total_deficit = stats.hot_deficit + stats.login_standby_deficit + stats.candidate_deficit
        # The target is a waterline, not a guarantee to launch more than max_slots.
        stats.desired_inventory_slots = min(max_slots, max(2, hot_target + login_target + candidate_target))
        if stats.hot_deficit:
            stats.health_level = "hot_low"
            stats.reason = f"热查询池不足：{stats.hot_query_sessions}/{hot_target}，60秒轮转容易断档"
        elif stats.login_standby_deficit:
            stats.health_level = "login_standby_low"
            stats.reason = f"登录备用池不足：{stats.login_standby_sessions}/{login_target}，热会话失效后补位慢"
        elif stats.candidate_deficit:
            stats.health_level = "candidate_low"
            stats.reason = f"候选生产池不足：{stats.candidate_sessions}/{candidate_target}，连续失败时替补不够"
        else:
            stats.health_level = "healthy"
            stats.reason = f"库存水位达标：热查询 {stats.hot_query_sessions}/{hot_target}，登录备用 {stats.login_standby_sessions}/{login_target}，候选 {stats.candidate_sessions}/{candidate_target}"
        return stats
