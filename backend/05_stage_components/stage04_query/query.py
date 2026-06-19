from __future__ import annotations

import time
from datetime import datetime
from importlib import import_module
from typing import Any

from ..base import StageResult
from .app_context import resolve_application_context
from .date_collector import collect_dates
from .date_policy import decide_dates
from .events import emit, slot_patch
from .models import BLOCKING_STAGES
from .navigation import ensure_schedule_context
from .page_recovery import reset_for_next_probe, retry_schedule_entry
from .post_selector import select_target_post
from .protocol import (
    BUSINESS_APPD,
    browser_fetch,
    choose_post,
    entry_available,
    endpoint_url,
    hard_status,
    in_target_window,
    normalize_date_s,
    status_reason,
    transport_failed,
)
from .result_quality import judge_query_result
from .slot_collector import collect_slots_for_accepted_date

SmartQueryGate = import_module("00_infrastructure.orchestration.query_gate").SmartQueryGate
SessionReusePolicy = import_module("00_infrastructure.orchestration.session_reuse.reuse_policy").SessionReusePolicy
TerminationPolicy = import_module("00_infrastructure.orchestration.session_health.termination_policy").TerminationPolicy
classify_page = import_module("03_browser_management.page_classifier").classify_page
record_route_stage_result = import_module("00_infrastructure.orchestration.route_health.event_recorder").record_stage_result

# Backward-compatible public helper for old imports/tests.
__all__ = [
    "BUSINESS_APPD",
    "BusinessQueryStage",
    "browser_fetch",
    "choose_post",
    "entry_available",
    "endpoint_url",
    "hard_status",
    "in_target_window",
    "normalize_date_s",
    "status_reason",
    "transport_failed",
]


class BusinessQueryStage:
    stage_name = "business_query"
    _PRE_GATE_BLOCKING_STAGES = {
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

    def _url(self, cfg: Any, path: str, params: dict[str, str] | None = None) -> str:
        return endpoint_url(cfg, path, params)

    def _attach_proxy_metadata(self, ctx: Any, payload: dict[str, Any]) -> None:
        """Persist proxy/route fields on each successful ticket query record.

        The ticket-pool table used to infer proxy info by replaying
        proxy_acquire events for the same slot/round.  That breaks whenever
        the event tail no longer contains the early proxy_acquire event or a
        same slot/round has been reused after restart.  The business query
        already has the authoritative SessionContext, so persist the proxy
        material directly with the query result.
        """
        material = getattr(ctx, "proxy_material", None)
        if material is None:
            return
        country = str(getattr(material, "country", "") or "")
        asn = str(getattr(material, "asn", "") or "")
        session_id = str(getattr(material, "session_id", "") or "")
        proxy_type = str(getattr(material, "proxy_type", "") or "")
        route = "/".join([country, asn]).strip("/")
        payload.setdefault("proxy_display", f"{country}/{asn or '-'}:{session_id}" if country or session_id else "")
        payload.setdefault("proxy_session", session_id)
        payload.setdefault("route", route)
        payload.setdefault("route_key", f"{country.upper()}:{proxy_type.lower()}:{asn.upper()}" if country or proxy_type or asn else "")
        payload.setdefault(
            "proxy",
            {
                "provider": getattr(material, "provider", ""),
                "proxy_type": proxy_type,
                "country": country,
                "asn": asn,
                "session_id": session_id,
                "host": getattr(material, "host", ""),
                "port": getattr(material, "port", ""),
            },
        )

    def _write_probe_state(self, ctx: Any, result: StageResult, *, live_round: int | None = None) -> None:
        payload = result.payload or {}
        if result.ok and isinstance(payload, dict):
            ctx.last_business_result = payload
        store = getattr(ctx, "store", None)
        if not store:
            return
        try:
            if isinstance(payload, dict) and (payload.get("days") is not None or payload.get("matched_slots") is not None):
                quality = judge_query_result(payload, ok=result.ok)
                payload.setdefault("result_quality", quality.to_dict())
                payload.setdefault("valid_query_success", quality.valid_success)
                payload.setdefault("queried_at", datetime.now().astimezone().isoformat(timespec="seconds"))
                payload.setdefault("slot_id", getattr(ctx, "slot_id", ""))
                payload.setdefault("round_id", getattr(ctx, "round_id", ""))
                payload.setdefault("round_started_at", getattr(ctx, "round_started_at", ""))
                payload.setdefault("live_ticket_id", payload.get("live_ticket_id") or getattr(ctx, "live_ticket_id", ""))
                self._attach_proxy_metadata(ctx, payload)
                unique_session = payload.get("live_ticket_id") or f"{getattr(ctx, 'slot_id', '-')}/{getattr(ctx, 'round_id', '-')}/{getattr(ctx, 'round_started_at', '')}"
                payload.setdefault("ticket_query_id", f"{unique_session}/q{live_round or payload.get('live_round') or 1}/{int(time.time() * 1000)}")
                store.write_latest_ticket(payload)
                days = payload.get("days") or []
                slots = payload.get("slots") or []
                matched = payload.get("matched_slots") or []
                store.write_availability_text(
                    "\n".join(
                        [
                            f"updated_at={datetime.now().astimezone().isoformat(timespec='seconds')}",
                            f"slot={ctx.slot_id} round={ctx.round_id} live_round={live_round or payload.get('live_round') or '-'}",
                            f"post={payload.get('post_name') or '-'} postId={payload.get('post_id') or '-'}",
                            f"days={days}",
                            f"acceptable_dates={payload.get('acceptable_dates') or []}",
                            f"rejected_dates={payload.get('rejected_dates') or []}",
                            f"slots={slots}",
                            f"matched={matched}",
                            f"target_hit={bool(payload.get('target_hit'))}",
                            f"clicked_date={payload.get('clicked_date')}",
                        ]
                    )
                    + "\n"
                )
                store.update_slot(
                    ctx.slot_id,
                    availability_days=len(days),
                    availability_slots=len(slots),
                    matched_slots=len(matched),
                    target_hit=bool(payload.get("target_hit")),
                    last_reason="target_hit" if payload.get("target_hit") else ("business_query_ok" if quality.valid_success else "business_query_empty"),
                    last_reason_zh=(
                        "命中目标日期，准备提交"
                        if payload.get("target_hit")
                        else ("业务查询成功，未点击不合适日期，继续轮询" if quality.valid_success else "官方接口返回空日期：记录诊断，不刷新有效查询 SLA")
                    ),
                )
            elif not result.ok:
                store.update_slot(
                    ctx.slot_id,
                    last_reason=result.message,
                    last_reason_zh=result.message,
                    result_stage=result.stage,
                )
        except Exception:
            pass

    def _record_route_query_success(self, ctx: Any, payload: dict[str, Any], *, live_round: int | None = None) -> dict[str, Any] | None:
        """Feed successful days queries back into route health immediately.

        `OneDragonPipeline.run_stage()` records route health only when the
        business_query stage exits.  In live-loop mode a session can produce
        several valid days queries and later exit with CF/login/page-view
        failure; relying only on final stage_exit means those valid successes
        never clear `consecutive_cf_challenges` or route cooldowns.  This helper
        records a synthetic successful business_query at the exact moment
        schedule_days returned valid dates.
        """
        if not isinstance(payload, dict):
            return None
        try:
            feedback_payload = dict(payload)
            feedback_payload["valid_query_success"] = True
            feedback_payload["route_health_feedback"] = "business_days_success"
            feedback_payload["live_round"] = live_round or payload.get("live_round")
            result = StageResult(True, self.stage_name, "business days query success", feedback_payload)
            route_health = record_route_stage_result(getattr(ctx, "store", None), getattr(ctx, "proxy_material", None), self.stage_name, result)
            if route_health and not route_health.get("error"):
                emit(
                    ctx,
                    "route_health_update",
                    {
                        "stage": self.stage_name,
                        "source": "business_query_success_feedback",
                        "live_round": live_round,
                        **route_health,
                    },
                )
                return route_health
        except Exception as exc:
            emit(
                ctx,
                "route_health_update_failed",
                {
                    "stage": self.stage_name,
                    "source": "business_query_success_feedback",
                    "live_round": live_round,
                    "error": repr(exc),
                },
            )
        return None

    async def click_schedule_entry(self, page: Any, cfg: Any) -> None:
        # Compatibility shim used by older tests/tools. New flow uses navigation.py.
        class _Ctx:
            def __init__(self, page: Any, cfg: Any):
                self.page = page
                self.runtime_config = cfg
                self.event_bus = None
                self.store = None
                self.slot_id = "compat"
                self.round_id = "compat"
        await ensure_schedule_context(_Ctx(page, cfg), max_attempts=1)

    def _blocked_result(self, state: Any, nav_meta: dict[str, Any]) -> StageResult:
        needs = nav_meta.get("needs_recover") or getattr(state, "stage", "unknown")
        payload = {"state": getattr(state, "__dict__", {}), "needs_recover": needs, **nav_meta}
        event_map = {
            "cf_challenge": "business_blocked_cf",
            "login": "business_blocked_login",
            "security_questions": "business_blocked_login",
            "waiting_room": "business_blocked_waiting_room",
            "rate_limit_1015": "business_rate_limit_cooldown",
            "rate_limit_429": "business_rate_limit_cooldown",
            "callback_not_found": "business_callback_not_found",
            "page_not_found": "business_page_not_found",
        }
        non_recoverable = {"navigation_not_found", "application_context_unresolved"}
        return StageResult(False, self.stage_name, f"business blocked by {needs}", payload, retryable=needs not in non_recoverable)

    async def _pre_gate_block_if_unhealthy(self, ctx: Any, *, live_round: int | None, session_key: str) -> StageResult | None:
        """Do not take the single business API gate while obviously unhealthy.

        The smart gate is intended to serialize the official business API
        calls.  Runtime evidence showed CF/login/waiting-room pages repeatedly
        grabbed that gate, failed within 0-2s, and starved healthy sessions.  A
        lightweight page classification before reserving the gate keeps recovery
        work out of the scarce query window.
        """
        try:
            state = await classify_page(ctx.page)
        except Exception as exc:
            state = type("State", (), {"stage": "network_error", "url": getattr(ctx.page, "url", ""), "title": "", "reason": repr(exc)})()
        stage = str(getattr(state, "stage", "") or "")
        if stage not in self._PRE_GATE_BLOCKING_STAGES:
            return None
        event_type = {
            "cf_challenge": "business_blocked_cf",
            "login": "business_blocked_login",
            "security_questions": "business_blocked_login",
            "idp_loading": "business_blocked_login",
            "waiting_room": "business_blocked_waiting_room",
            "rate_limit_1015": "business_rate_limit_cooldown",
            "rate_limit_429": "business_rate_limit_cooldown",
            "callback_not_found": "business_callback_not_found",
            "page_not_found": "business_page_not_found",
        }.get(stage, "business_navigation_blocked")
        emit(
            ctx,
            event_type,
            {
                "state": getattr(state, "__dict__", {}),
                "needs_recover": stage,
                "live_round": live_round,
                "session_key": session_key,
                "pre_gate": True,
                "stage_zh": "查询前健康预检：未占用业务 API 闸门，先恢复页面/登录/CF",
            },
        )
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                smart_query_state="preflight_blocked",
                smart_query_wait_reason="page_unhealthy",
                smart_query_wait_seconds=0,
                smart_query_next_allowed_at="",
                last_reason=f"business_pre_gate_blocked:{stage}",
                last_reason_zh=f"查询前健康预检未通过：当前是 {stage}，不占用业务 API 闸门，先恢复",
            )
        return self._blocked_result(state, {"needs_recover": stage, "pre_gate": True, "steps": []})

    async def _probe_once(self, ctx: Any, *, live_round: int | None = None, page_retry: int = 1) -> StageResult:
        steps: list[dict[str, Any]] = []
        ok, state, nav_meta = await ensure_schedule_context(ctx, max_attempts=2)
        steps.extend(nav_meta.get("actions") or [])
        if not ok:
            event_type = {
                "cf_challenge": "business_blocked_cf",
                "login": "business_blocked_login",
                "security_questions": "business_blocked_login",
                "waiting_room": "business_blocked_waiting_room",
                "rate_limit_1015": "business_rate_limit_cooldown",
                "callback_not_found": "business_callback_not_found",
                "page_not_found": "business_page_not_found",
            }.get(str(nav_meta.get("needs_recover") or getattr(state, "stage", "")), "business_navigation_blocked")
            emit(ctx, event_type, {"state": getattr(state, "__dict__", {}), "needs_recover": nav_meta.get("needs_recover")})
            return self._blocked_result(state, {**nav_meta, "steps": steps})

        return await self._probe_ready_context(ctx, steps=steps, live_round=live_round)

    async def _probe_ready_context(self, ctx: Any, *, steps: list[dict[str, Any]], live_round: int | None = None) -> StageResult:
        """Run official business APIs after navigation context is proven ready.

        This intentionally starts after ``ensure_schedule_context``.  The smart
        query gate should protect only the scarce official API sequence
        (page-view/family/posts/days/entries), not CF/login/waiting-room
        recovery or DOM navigation.
        """

        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                smart_query_state="api_querying",
                smart_query_wait_reason="",
                smart_query_wait_seconds=0,
                last_reason="business_api_sequence_started",
                last_reason_zh="业务 API 查询中：page-view / family / posts / days",
            )
        err, schedule, ctx_steps = await resolve_application_context(ctx)
        steps.extend(ctx_steps)
        if err:
            return err
        assert schedule is not None

        err, post = await select_target_post(ctx, schedule, steps)
        if err:
            return err
        assert post is not None

        err, dates = await collect_dates(ctx, schedule, post, steps)
        if err:
            return err
        assert dates is not None

        decision = decide_dates(ctx, dates)
        result: dict[str, Any] = {
            "ok": True,
            "live_round": live_round,
            "app_id": schedule.app_id,
            "applications": schedule.applications,
            "post_id": post.post_id,
            "post_name": post.post_name,
            "post_clicked": post.clicked,
            "post_click_method": post.click_method,
            "days": dates.days,
            "normalized_days": [normalize_date_s(d) for d in dates.days],
            "acceptable_dates": decision.acceptable_dates,
            "rejected_dates": decision.rejected_dates,
            "selected_date": decision.selected_date,
            "entries_by_date": {},
            "slots": [],
            "matched_slots": [],
            "target_hit": False,
            "clicked_date": False,
            "steps": steps,
        }

        if not decision.target_hit:
            ctx.last_business_result = result
            # Keep behavior transparent and repeatable: after a no-hit scan, go
            # back to home/top so the next live probe re-enters via Manage
            # Application instead of staying on a potentially stale widget state.
            await reset_for_next_probe(ctx, "no_acceptable_date_reenter_manage_application")
            return StageResult(True, self.stage_name, "query ok: no acceptable date, did not click date", result)

        err, slots = await collect_slots_for_accepted_date(ctx, schedule, post, dates, decision, steps)
        if err:
            return err
        assert slots is not None
        result.update(
            {
                "entries_by_date": slots.entries_by_date,
                "slots": slots.slots,
                "matched_slots": slots.matched_slots,
                "target_hit": bool(slots.matched_slots),
                "clicked_date": True,
                "booking_signal": ({**ctx.booking_signal, "token": "<redacted>"} if ctx.booking_signal else None),
            }
        )
        ctx.last_business_result = result
        return StageResult(True, self.stage_name, "target hit" if result.get("target_hit") else "accepted date but no available slot", result)

    async def _execute_once(self, ctx: Any, *, live_round: int | None = None) -> StageResult:
        page_retries = max(1, int(getattr(ctx.runtime_config.producer, "business_page_retry_attempts", 2) or 2))
        last: StageResult | None = None
        for attempt in range(1, page_retries + 1):
            slot_patch(ctx, reason="business_probe", reason_zh=f"业务查询第 {live_round or '-'} 轮 / 页面尝试 {attempt}", business_attempt=attempt)
            r = await self._probe_once(ctx, live_round=live_round, page_retry=attempt)
            last = r
            if r.ok:
                return r
            needs = (r.payload or {}).get("needs_recover")
            if needs in BLOCKING_STAGES or needs in {"auth_or_cf", "rate_limit_429", "rate_limited"}:
                return r
            if attempt < page_retries:
                await retry_schedule_entry(ctx, f"probe_failed_{needs or r.message}", max_attempts=1)
        return last or StageResult(False, self.stage_name, "business probe failed", {}, retryable=True)

    async def execute(self, ctx: Any) -> StageResult:
        cfg = ctx.runtime_config
        live_enabled = bool(getattr(cfg.producer, "inline_business_live_loop", False))
        if not live_enabled:
            r = await self._execute_once(ctx)
            self._write_probe_state(ctx, r)
            return r

        interval_s = max(1.0, float(getattr(cfg.producer, "inline_business_live_interval_seconds", 5.0) or 5.0))
        max_s = float(getattr(cfg.producer, "inline_business_live_max_seconds", 0.0) or 0.0)
        failure_grace = max(1, int(getattr(cfg.producer, "inline_business_live_failure_grace_rounds", 3) or 3))
        started = time.monotonic()
        live_ticket_id = f"live_{ctx.slot_id}_{ctx.round_id}_{int(time.time())}"
        ctx.live_ticket_id = live_ticket_id
        rounds: list[dict[str, Any]] = []
        consecutive_bad = 0
        consecutive_429 = 0
        successful_queries = 0
        round_no = 0
        last: StageResult | None = None
        gate = SmartQueryGate(getattr(ctx, "store", None), cfg) if getattr(ctx, "store", None) is not None else None
        reuse_policy = SessionReusePolicy(cfg)
        termination_policy = TerminationPolicy()
        session_key = f"{getattr(ctx, 'slot_id', '-')}/{getattr(ctx, 'round_id', '-')}/{getattr(ctx, 'round_started_at', '')}"
        last_wait_event_reason = ""
        last_wait_event_at = 0.0
        consecutive_cf_recovery_failures = 0
        consecutive_login_recovery_failures = 0
        max_recovery_attempts = 3
        failure_kind_counts: dict[str, int] = {}
        try:
            last_seen_handoff_seq = int((ctx.store.query_handoff() if getattr(ctx, "store", None) else {}).get("seq") or 0)
        except Exception:
            last_seen_handoff_seq = 0

        async def wait_or_handoff(seconds: float) -> bool:
            nonlocal last_seen_handoff_seq
            end_at = time.monotonic() + max(0.0, float(seconds or 0))
            while time.monotonic() < end_at:
                chunk = min(0.25, max(0.01, end_at - time.monotonic()))
                await ctx.page.wait_for_timeout(int(chunk * 1000))
                try:
                    current = int((ctx.store.query_handoff() if getattr(ctx, "store", None) else {}).get("seq") or 0)
                    if current > last_seen_handoff_seq:
                        last_seen_handoff_seq = current
                        emit(ctx, "query_handoff_wakeup", {"seq": current, "stage_zh": "收到业务闸门接力信号：提前重试 reserve"})
                        return True
                except Exception:
                    pass
            return False

        def preflight_exit_result(reason: str) -> StageResult | None:
            if not str(reason or "").startswith("preflight_"):
                return None
            needs = {
                "preflight_recoverable_cf": "cf_challenge",
                "preflight_recoverable_login": "login",
                "preflight_waiting_room": "waiting_room",
                "preflight_rate_limited": "rate_limit_429",
            "preflight_terminal_risk": "recycle_context",
            "preflight_callback_not_found": "callback_not_found",
            "preflight_page_not_found": "page_not_found",
            "preflight_network_bad": "network_error",
                "preflight_not_running": "recycle_context",
                "preflight_not_ready": "navigation_not_found",
                "preflight_querying": "recycle_context",
            }.get(str(reason or ""), "recycle_context")
            retryable = needs not in {"recycle_context"}
            return StageResult(
                False,
                self.stage_name,
                f"preflight exit: {reason}",
                {"needs_recover": needs, "reason": reason, "fast_handoff": True, "preflight_exit": True, "live_round": round_no},
                retryable=retryable,
            )

        while True:
            if max_s > 0 and time.monotonic() - started > max_s:
                break
            round_no += 1
            if getattr(ctx, "store", None):
                ctx.store.update_slot(
                    ctx.slot_id,
                    stage=self.stage_name,
                    stage_zh="查日期/时间段",
                    elapsed_s=round(time.monotonic() - started, 1),
                    live_ticket_id=live_ticket_id,
                    live_round=round_no,
                    last_reason="business_live_loop",
                    last_reason_zh=f"同会话业务轮询第 {round_no} 轮：进入安排预约→协议选择北京→只查日期",
                )
            lease_id = ""
            r: StageResult | None = None
            if gate is not None and gate.enabled:
                pre_block = await self._pre_gate_block_if_unhealthy(ctx, live_round=round_no, session_key=session_key)
                ready_steps: list[dict[str, Any]] = []
                if pre_block is None:
                    try:
                        nav_ok, nav_state, nav_meta = await ensure_schedule_context(ctx, max_attempts=2)
                        ready_steps.extend(nav_meta.get("actions") or [])
                    except Exception as exc:
                        nav_ok = False
                        nav_state = type("State", (), {"stage": "network_error", "url": getattr(ctx.page, "url", ""), "title": "", "reason": repr(exc)})()
                        nav_meta = {"needs_recover": "network_error", "steps": ready_steps, "navigation_exception": repr(exc)}
                    if not nav_ok:
                        event_type = {
                            "cf_challenge": "business_blocked_cf",
                            "login": "business_blocked_login",
                            "security_questions": "business_blocked_login",
                            "idp_loading": "business_blocked_login",
                            "waiting_room": "business_blocked_waiting_room",
                            "rate_limit_1015": "business_rate_limit_cooldown",
                            "callback_not_found": "business_callback_not_found",
                            "page_not_found": "business_page_not_found",
                        }.get(str(nav_meta.get("needs_recover") or getattr(nav_state, "stage", "")), "business_navigation_blocked")
                        emit(ctx, event_type, {"state": getattr(nav_state, "__dict__", {}), "needs_recover": nav_meta.get("needs_recover"), "pre_gate": True, "stage_zh": "预约上下文未就绪：未占用业务 API 闸门"})
                        r = self._blocked_result(nav_state, {**nav_meta, "steps": ready_steps, "pre_gate": True})
                    elif getattr(ctx, "store", None):
                        try:
                            ctx.store.update_slot(
                                ctx.slot_id,
                                live_page_stage=str(getattr(nav_state, "stage", "") or ""),
                                live_page_reason=str(getattr(nav_state, "reason", "") or ""),
                                live_page_url=str(getattr(nav_state, "url", "") or ""),
                                live_page_title=str(getattr(nav_state, "title", "") or ""),
                                live_page_observed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                                live_page_source="business_pre_gate_navigation",
                            )
                        except Exception:
                            pass
                if pre_block is None and r is None:
                    while True:
                        decision = gate.reserve(slot_id=ctx.slot_id, round_id=ctx.round_id, session_key=session_key, live_round=round_no)
                        if decision.ok:
                            lease_id = decision.lease_id
                            if getattr(ctx, "store", None):
                                ctx.store.update_slot(
                                    ctx.slot_id,
                                    smart_query_state="querying",
                                    smart_query_wait_reason="",
                                    smart_query_next_allowed_at="",
                                    smart_query_wait_seconds=0,
                                    last_reason="smart_query_reserved",
                                    last_reason_zh="智能调度：正在执行业务 API 查询",
                                )
                            emit(
                                ctx,
                                "smart_query_reserved",
                                {
                                    "lease_id": lease_id,
                                    "session_key": session_key,
                                    "live_round": round_no,
                                    "strategy": "global_success_gap_and_per_session_cooldown",
                                    "stage_zh": "智能调度：已获得业务查询闸门",
                                },
                            )
                            break
                        preflight_exit = preflight_exit_result(decision.reason)
                        if preflight_exit is not None:
                            r = preflight_exit
                            emit(
                                ctx,
                                "smart_query_preflight_exit",
                                {
                                    "reason": decision.reason,
                                    "needs_recover": (r.payload or {}).get("needs_recover"),
                                    "session_key": session_key,
                                    "stage_zh": "查询前健康预检判定当前会话不可查：退出业务等待循环，释放候选接力",
                                },
                            )
                            break
                        cadence = gate.cadence() if gate is not None else {}
                        poll_cap = float(cadence.get("wait_poll_seconds") or getattr(getattr(cfg, "smart_orchestrator", None), "wait_poll_seconds", 2.0) or 2.0)
                        wait_s = max(0.1, min(max(0.1, poll_cap), float(decision.wait_seconds or 0.3)))
                        if getattr(ctx, "store", None):
                            ctx.store.update_slot(
                                ctx.slot_id,
                                smart_query_state="waiting",
                                last_reason=f"smart_query_wait:{decision.reason}",
                                last_reason_zh=f"智能调度等待：{decision.reason}，预计 {int(decision.wait_seconds)} 秒后可查",
                                smart_query_wait_reason=decision.reason,
                                smart_query_next_allowed_at=decision.next_allowed_at,
                                smart_query_wait_seconds=round(float(decision.wait_seconds or 0), 1),
                            )
                        # Keep slot_status realtime, but do not flood the semantic
                        # event stream every 5s.  Emit wait events only when the
                        # reason changes or every 30s so the UI remains auditable.
                        now_mono = time.monotonic()
                        if decision.reason != last_wait_event_reason or now_mono - last_wait_event_at >= 30.0:
                            emit(
                                ctx,
                                "smart_query_wait",
                                {
                                    "reason": decision.reason,
                                    "wait_seconds": round(float(decision.wait_seconds or 0), 1),
                                    "next_allowed_at": decision.next_allowed_at,
                                    "session_key": session_key,
                                    "stage_zh": "智能调度：等待业务查询窗口",
                                },
                            )
                            last_wait_event_reason = decision.reason
                            last_wait_event_at = now_mono
                        await wait_or_handoff(wait_s)
                else:
                    r = r or pre_block
            if r is None and not lease_id:
                r = await self._execute_once(ctx, live_round=round_no)
            elif r is None and lease_id:
                try:
                    r = await self._probe_ready_context(ctx, steps=ready_steps if 'ready_steps' in locals() else [], live_round=round_no)
                except Exception as exc:
                    # Never let an in-browser fetch/navigation exception leak past
                    # the live loop after the single global business-query gate was
                    # reserved.  Older behavior left active_query occupied until
                    # its long lease expired, causing all other hot sessions to wait
                    # 2-3 minutes despite having capacity.
                    r = StageResult(
                        False,
                        self.stage_name,
                        f"business probe exception: {type(exc).__name__}: {exc}",
                        {
                            "needs_recover": "business_exception",
                            "error_type": type(exc).__name__,
                            "exception": repr(exc),
                            "live_round": round_no,
                        },
                        retryable=True,
                    )
                    emit(
                        ctx,
                        "business_probe_exception",
                        {
                            "live_round": round_no,
                            "error_type": type(exc).__name__,
                            "error": repr(exc),
                            "session_key": session_key,
                            "stage_zh": "业务查询异常：已释放查询闸门并进入会话恢复",
                        },
                    )
            last = r
            payload = r.payload or {}
            if isinstance(payload, dict) and payload.get("preflight_exit"):
                self._write_probe_state(ctx, r, live_round=round_no)
                emit(
                    ctx,
                    "session_fast_terminated",
                    {
                        "session_key": session_key,
                        "decision": {"action": "preflight_exit", "terminal": True, "reason": payload.get("reason"), "error_type": payload.get("needs_recover")},
                        "successful_queries": successful_queries,
                        "live_round": round_no,
                        "stage_zh": "查询前预检退出：不在业务循环里等待，交给 pipeline 恢复/换轮",
                    },
                )
                payload["live_loop"] = {
                    "live_ticket_id": live_ticket_id,
                    "rounds_count": round_no,
                    "rounds": rounds[-50:],
                    "successful_queries": successful_queries,
                    "session_end_reason": payload.get("reason") or "preflight_exit",
                    "session_end_error_type": payload.get("needs_recover") or "",
                    "terminal_or_recoverable": "preflight_exit",
                    "reuse_count": successful_queries,
                }
                return r
            if isinstance(payload, dict):
                payload.setdefault("live_ticket_id", live_ticket_id)
                payload.setdefault("round_started_at", getattr(ctx, "round_started_at", ""))
            quality = judge_query_result(payload if isinstance(payload, dict) else {}, ok=bool(r.ok))
            if isinstance(payload, dict):
                payload["result_quality"] = quality.to_dict()
                payload["valid_query_success"] = quality.valid_success
            query_success = bool(quality.valid_success)
            if query_success:
                successful_queries += 1
                failure_kind_counts.clear()
                route_feedback = self._record_route_query_success(ctx, payload, live_round=round_no) if isinstance(payload, dict) else None
                if route_feedback and isinstance(payload, dict):
                    payload["route_health_feedback"] = route_feedback
            termination_decision = None
            if not query_success and isinstance(payload, dict):
                first = termination_policy.decide(r, successful_queries=successful_queries, consecutive_kind_failures=1)
                kind_count = failure_kind_counts.get(first.error_type, 0) + 1
                failure_kind_counts[first.error_type] = kind_count
                termination_decision = termination_policy.decide(r, successful_queries=successful_queries, consecutive_kind_failures=kind_count)
                payload["termination_decision"] = termination_decision.to_dict()
                if termination_decision.handoff:
                    payload["fast_handoff"] = True
            if gate is not None and lease_id:
                gate_state = gate.complete(
                    lease_id,
                    success=query_success,
                    slot_id=ctx.slot_id,
                    round_id=ctx.round_id,
                    session_key=session_key,
                    result=payload if isinstance(payload, dict) else {},
                    message=r.message,
                )
                session_state = ((gate_state or {}).get("sessions") or {}).get(session_key, {}) if isinstance(gate_state, dict) else {}
                next_query_at = str(session_state.get("next_query_at") or "")
                if getattr(ctx, "store", None):
                    state_after_query = "cooling" if (query_success or quality.empty) else "recovering"
                    wait_reason_after_query = "session_cooldown" if query_success else ("empty_result_cooldown" if quality.empty else "failure_cooldown")
                    ctx.store.update_slot(
                        ctx.slot_id,
                        smart_query_state=state_after_query,
                        smart_query_wait_reason=wait_reason_after_query,
                        smart_query_wait_seconds=0,
                        smart_query_next_allowed_at=next_query_at,
                    )
                emit(
                    ctx,
                    "smart_query_completed",
                    {
                        "lease_id": lease_id,
                        "success": query_success,
                        "completed": quality.completed,
                        "empty": quality.empty,
                        "quality_reason": quality.reason,
                        "session_key": session_key,
                        "days_count": len(payload.get("days") or []) if isinstance(payload, dict) else 0,
                        "message": r.message,
                        "successful_queries": successful_queries,
                        "termination_decision": termination_decision.to_dict() if termination_decision else None,
                        "stage_zh": "智能调度：业务查询窗口已释放",
                    },
                )
            if termination_decision is not None and termination_decision.terminal:
                if getattr(ctx, "store", None):
                    ctx.store.update_slot(
                        ctx.slot_id,
                        last_reason=f"session_fast_terminated:{termination_decision.error_type}",
                        last_reason_zh=f"坏会话快速结束：{termination_decision.reason}",
                        session_successful_queries=successful_queries,
                        session_reuse_action=termination_decision.action,
                    )
                emit(
                    ctx,
                    "session_fast_terminated",
                    {
                        "session_key": session_key,
                        "decision": termination_decision.to_dict(),
                        "successful_queries": successful_queries,
                        "live_round": round_no,
                        "stage_zh": "坏会话快速结束：不再复用，交给候选槽接力",
                    },
                )
                payload["live_loop"] = {
                    "live_ticket_id": live_ticket_id,
                    "rounds_count": round_no,
                    "rounds": rounds[-50:],
                    "successful_queries": successful_queries,
                    "session_end_reason": termination_decision.reason,
                    "session_end_error_type": termination_decision.error_type,
                    "terminal_or_recoverable": "terminal",
                    "reuse_count": successful_queries,
                }
                return r
            self._write_probe_state(ctx, r, live_round=round_no)
            rounds.append(
                {
                    "round": round_no,
                    "ok": r.ok,
                    "message": r.message,
                    "target_hit": bool(payload.get("target_hit")),
                    "days_count": len(payload.get("days") or []),
                    "acceptable_count": len(payload.get("acceptable_dates") or []),
                    "slots_count": len(payload.get("slots") or []),
                    "matched_count": len(payload.get("matched_slots") or []),
                    "clicked_date": bool(payload.get("clicked_date")),
                    "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            if r.ok and payload.get("target_hit"):
                payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": "target_hit"}
                return r
            if not r.ok:
                needs_recover = str(payload.get("needs_recover") or "")
                reuse_grace = reuse_policy.recovery_grace(success_count=successful_queries, default_grace=failure_grace)
                if needs_recover in {"auth_or_cf", "auth_or_cf_block"} or "auth_or_cf" in str(payload.get("result_class") or ""):
                    consecutive_bad += 1
                    reuse_decision = reuse_policy.after_failure(r, success_count=successful_queries, consecutive_bad=consecutive_bad, default_grace=failure_grace)
                    burst = bool(gate and gate.cadence().get("mode") == "release_burst")
                    sleep_s = (1.0 if burst else min(120.0, 20.0 * consecutive_bad))
                    emit(
                        ctx,
                        "business_auth_cf_retreat",
                        {
                            "kind": needs_recover or "auth_or_cf",
                            "consecutive_bad": consecutive_bad,
                            "successful_queries": successful_queries,
                            "reuse_decision": reuse_decision.to_dict(),
                            "cooldown_seconds": sleep_s,
                            "strategy": "keep_browser_return_home_then_retry",
                            "stage_zh": "业务 API 被登录/CF 层拦截：保留浏览器，退回首页短冷却后继续",
                        },
                    )
                    if getattr(ctx, "store", None):
                        ctx.store.update_slot(
                            ctx.slot_id,
                            last_reason="business_auth_or_cf_soft_retreat",
                            last_reason_zh=f"业务 API 被登录/CF 层拦截：第 {consecutive_bad} 次，保留会话退回首页，{int(sleep_s)} 秒后继续",
                        )
                    try:
                        await reset_for_next_probe(ctx, f"auth_or_cf_live_retreat_{consecutive_bad}")
                    except Exception:
                        pass
                    if reuse_decision.terminal:
                        payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": reuse_decision.reason, "session_end_error_type": reuse_decision.error_type, "terminal_or_recoverable": "terminal", "reuse_count": successful_queries}
                        return r
                    await ctx.page.wait_for_timeout(int(sleep_s * 1000))
                    continue
                if needs_recover in {"rate_limit_429", "rate_limited"}:
                    consecutive_bad += 1
                    consecutive_429 += 1
                    reuse_decision = reuse_policy.after_failure(r, success_count=successful_queries, consecutive_bad=consecutive_bad, default_grace=failure_grace)
                    configured_ladder = list(getattr(cfg.producer, "rate_limit_429_cooldowns_seconds", []) or [])
                    ladder = [float(x) for x in configured_ladder if float(x) > 0] or [60.0, 120.0, 180.0]
                    burst = bool(gate and gate.cadence().get("mode") == "release_burst")
                    sleep_s = 1.0 if burst else ladder[min(consecutive_429 - 1, len(ladder) - 1)]
                    emit(
                        ctx,
                        "business_rate_limit_cooldown",
                        {
                            "kind": "429",
                            "consecutive_429": consecutive_429,
                            "successful_queries": successful_queries,
                            "reuse_decision": reuse_decision.to_dict(),
                            "cooldown_seconds": sleep_s,
                            "strategy": "return_home_short_cooldown_keep_session",
                            "stage_zh": "429 短退避：先退回首页/预约入口，保留当前浏览器会话",
                        },
                    )
                    if getattr(ctx, "store", None):
                        ctx.store.update_slot(
                            ctx.slot_id,
                            last_reason="rate_limit_429_live_short_retreat",
                            last_reason_zh=f"429 第 {consecutive_429} 次：退回首页/预约入口，短冷却 {int(sleep_s)} 秒后继续",
                        )
                    try:
                        await reset_for_next_probe(ctx, f"rate_limit_429_live_cooldown_{consecutive_429}")
                    except Exception:
                        pass
                    if reuse_decision.terminal:
                        payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": reuse_decision.reason, "session_end_error_type": reuse_decision.error_type, "terminal_or_recoverable": "terminal", "reuse_count": successful_queries}
                        return r
                    await ctx.page.wait_for_timeout(int(sleep_s * 1000))
                    continue
                if needs_recover in {"navigation_not_found", "home"}:
                    consecutive_bad += 1
                    reuse_decision = reuse_policy.after_failure(r, success_count=successful_queries, consecutive_bad=consecutive_bad, default_grace=failure_grace)
                    if reuse_decision.terminal:
                        payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": reuse_decision.reason, "session_end_error_type": reuse_decision.error_type, "terminal_or_recoverable": "terminal", "reuse_count": successful_queries}
                        return r
                    try:
                        await reset_for_next_probe(ctx, f"soft_navigation_retry_{needs_recover}")
                    except Exception:
                        pass
                    await ctx.page.wait_for_timeout(int(interval_s * 1000))
                    continue
                if needs_recover:
                    consecutive_bad += 1
                    reuse_decision = reuse_policy.after_failure(r, success_count=successful_queries, consecutive_bad=consecutive_bad, default_grace=failure_grace)
                    if getattr(ctx, "store", None):
                        ctx.store.update_slot(
                            ctx.slot_id,
                            last_reason=f"session_reuse_{reuse_decision.error_type}",
                            last_reason_zh=reuse_decision.reason,
                            session_successful_queries=successful_queries,
                            session_reuse_action=reuse_decision.action,
                        )
                    emit(ctx, "session_reuse_decision", {"successful_queries": successful_queries, "consecutive_bad": consecutive_bad, "decision": reuse_decision.to_dict(), "stage_zh": "会话复用判定"})
                    if reuse_decision.terminal:
                        payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": reuse_decision.reason, "session_end_error_type": reuse_decision.error_type, "terminal_or_recoverable": "terminal", "reuse_count": successful_queries}
                        return r
                    try:
                        await reset_for_next_probe(ctx, f"session_reuse_retry_{reuse_decision.error_type}")
                    except Exception:
                        pass
                    await ctx.page.wait_for_timeout(int(min(120.0, 15.0 * consecutive_bad) * 1000))
                    continue
                consecutive_bad += 1
                reuse_decision = reuse_policy.after_failure(r, success_count=successful_queries, consecutive_bad=consecutive_bad, default_grace=failure_grace)
                if reuse_decision.terminal:
                    payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "successful_queries": successful_queries, "session_end_reason": reuse_decision.reason, "session_end_error_type": reuse_decision.error_type, "terminal_or_recoverable": "terminal", "reuse_count": successful_queries}
                    return r
            else:
                consecutive_bad = 0
                consecutive_429 = 0
                if getattr(ctx, "store", None):
                    ctx.store.update_slot(ctx.slot_id, session_successful_queries=successful_queries, session_reuse_state="cooling_or_waiting")

            sleep_s = interval_s
            msg = (r.message or "").lower()
            if "rate" in msg or "429" in msg or "1015" in msg:
                sleep_s = max(interval_s, min(60.0, interval_s * 4))
            await ctx.page.wait_for_timeout(int(sleep_s * 1000))

        if last is None:
            return StageResult(False, self.stage_name, "business live loop ended without probe", {"live_ticket_id": live_ticket_id}, retryable=True)
        payload = dict(last.payload or {})
        payload["live_loop"] = {"live_ticket_id": live_ticket_id, "rounds_count": round_no, "rounds": rounds[-50:], "ended_by": "max_seconds", "successful_queries": successful_queries, "session_end_reason": "max_seconds", "reuse_count": successful_queries}
        return StageResult(last.ok, self.stage_name, "business live loop max seconds reached", payload, retryable=not last.ok)

