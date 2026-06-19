from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any

AppConfig = import_module("00_infrastructure.config.models").AppConfig
EventBus = import_module("00_infrastructure.events.event_bus").EventBus
iso_now = import_module("00_infrastructure.utils.time").iso_now
StateStore = import_module("00_infrastructure.runtime.state_store").StateStore
ProducerService = import_module("07_scheduler.producer_service").ProducerService
SmartQueryGate = import_module("00_infrastructure.orchestration.query_gate").SmartQueryGate
SlaOrchestrator = import_module("00_infrastructure.orchestration.sla_orchestrator").SlaOrchestrator
SessionHealthClassifier = import_module("00_infrastructure.orchestration.session_health.classifier").SessionHealthClassifier
ReuseHealthScorer = import_module("00_infrastructure.orchestration.session_reuse.reuse_health").ReuseHealthScorer


PAGE_STATUS_ZH = {
    "cf_challenge": "人机验证",
    "waiting_room": "等待室状态",
    "login": "登录状态",
    "account_login_blocked": "账号登录禁止",
    "security_questions": "密保状态",
    "idp_loading": "登录回跳状态",
    "home": "首页状态",
    "schedule": "预约查询页状态",
    "rate_limit_1015": "1015 状态",
    "rate_limit_429": "429 状态",
    "access_denied": "拉黑状态",
    "network_error": "页面错误状态",
    "login_failed": "登录失败状态",
    "blank": "空白页状态",
    "site": "官网页面状态",
    "unknown": "未知页面状态",
}


def _page_status_zh(slot: dict[str, Any]) -> str:
    page = str(slot.get("live_page_stage") or "")
    if page:
        return PAGE_STATUS_ZH.get(page, str(slot.get("live_page_stage_zh") or page))
    stage = str(slot.get("stage") or "")
    if stage == "cf_gate":
        return "人机验证"
    if stage == "login":
        return "登录状态"
    if stage == "waiting_room":
        return "等待室状态"
    if stage == "business_query":
        return "查询页状态"
    if str(slot.get("state") or "") == "pending":
        return "等待启动"
    return str(slot.get("stage_zh") or stage or "未知页面状态")


def _query_status_zh(slot: dict[str, Any]) -> str:
    if slot.get("scheduler_status_zh"):
        return str(slot.get("scheduler_status_zh"))
    state = str(slot.get("smart_query_state") or "")
    reason = str(slot.get("smart_query_wait_reason") or "")
    hay = " ".join(
        str(slot.get(k) or "")
        for k in ("stage", "stage_zh", "last_reason", "last_reason_zh", "live_page_stage", "live_page_reason", "recovery_error_type", "recovery_action")
    ).lower()
    page = str(slot.get("live_page_stage") or "")
    if state in {"api_querying", "querying"} or "smart_query_reserved" in hay:
        return "查询状态（使用中）"
    if state == "candidate_primary":
        return "查询状态（主候选）"
    if state == "candidate_backup":
        return "查询状态（候补中）"
    if state in {"planned"}:
        return "查询状态（拟使用中）"
    if state == "cooling" or reason in {"session_cooldown", "failure_cooldown", "rate_limit_cooldown"}:
        return "查询状态（冷却中）"
    if state == "recovering":
        return "查询状态（恢复中）"
    if state == "waiting" or reason in {"global_success_gap", "business_api_gate_busy"} or "smart_query_wait" in hay:
        return "查询状态（等候中）"
    unhealthy_page = page in {"cf_challenge", "login", "account_login_blocked", "security_questions", "idp_loading", "waiting_room", "rate_limit_1015", "rate_limit_429", "access_denied", "network_error", "blank"}
    if unhealthy_page:
        return "查询状态（未就绪）"
    if state == "preflight_blocked" or reason == "page_unhealthy":
        return "查询状态（等候中）"
    if "business" in hay or "query" in hay or "查" in hay:
        return "查询状态（准备中）"
    if str(slot.get("state") or "") == "pending":
        return "查询状态（未启动）"
    return "查询状态（等候中）"


def _realtime_status_zh(slot: dict[str, Any]) -> str:
    page = _page_status_zh(slot)
    query = _query_status_zh(slot)
    if page and query and query != "查询状态（未启动）":
        return f"{page} / {query}"
    return page or query or "等待启动"


class SystemStatusService:
    def __init__(self, config: AppConfig, store: StateStore, producer: ProducerService, event_bus: EventBus):
        self.config = config
        self.store = store
        self.producer = producer
        self.event_bus = event_bus

    def snapshot(self) -> dict[str, Any]:
        slots = self.store.read_slots()
        pipeline = self.store.pipeline_status()
        pipeline_running = self.producer.is_running()
        smart_snapshot = SmartQueryGate(self.store, self.config).snapshot(include_sessions=False)
        scheduler_state = self.store.scheduler_state()
        sessions = scheduler_state.get("sessions") if isinstance(scheduler_state.get("sessions"), dict) else {}
        by_slot_session: dict[str, dict[str, Any]] = {}
        for session in sessions.values():
            if not isinstance(session, dict):
                continue
            sid = str(session.get("slot_id") or "")
            if not sid:
                continue
            old = by_slot_session.get(sid) or {}
            if str(session.get("last_completed_at") or session.get("last_reserved_at") or "") >= str(old.get("last_completed_at") or old.get("last_reserved_at") or ""):
                by_slot_session[sid] = session
        health_classifier = SessionHealthClassifier()
        reuse_scorer = ReuseHealthScorer()
        candidate_roles = {
            str(x.get("slot_id")): x
            for x in (smart_snapshot.get("candidate_queue") or [])
            if isinstance(x, dict) and x.get("slot_id")
        }
        view_slots = []
        for row in slots.values():
            rec = dict(row)
            if pipeline_running and rec.get("state") == "running" and rec.get("round_started_at"):
                try:
                    started = datetime.fromisoformat(str(rec.get("round_started_at")))
                    now = datetime.now(started.tzinfo).astimezone(started.tzinfo) if started.tzinfo else datetime.now()
                    rec["elapsed_s"] = round(max(0.0, (now - started).total_seconds()), 1)
                except Exception:
                    pass
            if not pipeline_running and rec.get("state") == "running":
                rec["stale"] = True
                rec["last_live_stage"] = rec.get("stage")
                rec["state"] = "stale"
                rec["stage_zh"] = "历史状态（当前未运行）"
            cand = candidate_roles.get(str(rec.get("slot") or ""))
            if cand:
                rec["dispatcher_candidate_role"] = cand.get("role")
                rec["dispatcher_candidate_score"] = cand.get("score")
                rec["dispatcher_candidate_reason"] = cand.get("reason")
            health = health_classifier.classify(rec, by_slot_session.get(str(rec.get("slot") or "")))
            reuse_health = reuse_scorer.score(rec, by_slot_session.get(str(rec.get("slot") or ""))).to_dict()
            rec["session_health_state"] = health.state
            rec["session_health_reason"] = health.reason
            rec["session_health_score"] = health.score
            rec["session_query_ready"] = health.query_ready
            rec["session_gate_allowed"] = health.gate_allowed
            rec["pool_role"] = reuse_health.get("pool_role")
            rec["scheduler_status"] = reuse_health.get("scheduler_status")
            rec["scheduler_status_zh"] = reuse_health.get("scheduler_status_zh")
            rec["scheduler_reason"] = reuse_health.get("reason")
            rec["scheduler_blocked_reason"] = reuse_health.get("blocked_reason")
            rec["scheduler_recommended_action"] = reuse_health.get("recommended_action")
            rec["query_eligible"] = reuse_health.get("query_eligible")
            rec["reuse_score"] = reuse_health.get("reuse_score")
            rec["session_success_count"] = reuse_health.get("success_count")
            rec["session_failure_count"] = reuse_health.get("failure_count")
            rec["session_next_query_at"] = reuse_health.get("next_query_at")
            rec["next_query_eta_seconds"] = reuse_health.get("next_query_eta_seconds")
            rec["last_success_age_seconds"] = reuse_health.get("last_success_age_seconds")
            rec["recent_failure_kind"] = reuse_health.get("recent_failure_kind")
            rec["page_status_zh"] = _page_status_zh(rec)
            rec["query_status_zh"] = _query_status_zh(rec)
            rec["realtime_status_zh"] = _realtime_status_zh(rec)
            view_slots.append(rec)
        active_slots = sum(1 for x in view_slots if x.get("state") == "running")
        return {
            "ts": iso_now(),
            "system": {
                "name": self.config.system.name,
                "environment": self.config.system.environment,
                "api_port": self.config.system.api_port,
                "mode": "standalone_numbered_backend",
                "pipeline_running": pipeline_running,
                "pipeline": pipeline,
            },
            "target": self.config.target.model_dump(),
            "slot_policy": self.config.slots.model_dump(),
            "booking": {
                "armed": self.config.booking.armed,
                "max_parallel_submit": self.config.booking.max_parallel_submit,
            },
            "account_guard": self.store.account_guard(),
            "login_admission": self.store.login_admission(),
            "smart_scheduler": smart_snapshot,
            "sla_orchestrator": SlaOrchestrator(self.store, self.config).snapshot(active_slots=active_slots),
            "slots": view_slots,
            "latest_ticket": self.store.latest_ticket(),
            "ticket_history": self.store.ticket_history(200),
            "ticket_query_count_today": self.store.ticket_query_count_today(),
            "availability_text": self.store.availability_text(),
            "booking_signal": self.store.booking_signal(),
            "events": self.event_bus.tail(80),
        }
