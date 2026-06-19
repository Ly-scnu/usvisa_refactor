from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
SessionHealthClassifier = import_module("00_infrastructure.orchestration.session_health.classifier").SessionHealthClassifier


FAILURE_TOKENS = {
    "rate_limited": ("rate_limited", "429"),
    "failed_to_fetch": ("failed_fetch", "failed_to_fetch", "fetch"),
    "auth_or_cf": ("auth_or_cf", "cf", "challenge"),
    "page_view_blocked": ("page_view_blocked", "page view"),
    "terminal": ("terminal", "1015", "access_denied", "blocked"),
}


@dataclass
class ReuseHealth:
    slot_id: str = ""
    pool_role: str = "candidate"
    scheduler_status: str = "not_ready"
    scheduler_status_zh: str = "查询状态（未就绪）"
    recommended_action: str = "produce"
    query_eligible: bool = False
    reuse_score: int = 0
    reason: str = "未评估"
    blocked_reason: str = ""
    success_count: int = 0
    failure_count: int = 0
    next_query_at: str = ""
    next_query_eta_seconds: float = 0.0
    last_success_at: str = ""
    last_success_age_seconds: float = -1.0
    recent_failure_kind: str = ""
    health_state: str = ""
    health_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReuseHealthScorer:
    """Score whether a live browser session should be reused for days query.

    It is deliberately read-only: no browser action, no file mutation, no
    global scheduling.  The same result is consumed by CandidateQueue, the
    query gate and the dashboard so UI and backend decisions use one language.
    """

    def __init__(self) -> None:
        self.classifier = SessionHealthClassifier()

    def score(self, slot: dict[str, Any] | None, session: dict[str, Any] | None = None) -> ReuseHealth:
        slot = slot or {}
        session = session or {}
        health = self.classifier.classify(slot, session)
        slot_id = str(slot.get("slot") or slot.get("slot_id") or session.get("slot_id") or health.slot_id or "")
        success_count = int(session.get("success_count") or slot.get("session_successful_queries") or 0)
        failure_count = int(session.get("failure_count") or 0)
        next_query_at = str(session.get("next_query_at") or slot.get("smart_query_next_allowed_at") or "")
        wait = round(clock.seconds_until(next_query_at), 1)
        last_success_at = str(session.get("last_success_at") or "")
        last_success_dt = clock.parse_ts(last_success_at)
        last_success_age = round((clock.now_dt() - last_success_dt).total_seconds(), 1) if last_success_dt else -1.0
        cooldown_mode = str(session.get("last_cooldown_mode") or "")
        failure_kind = self._failure_kind(session, slot)

        score = int(health.score or 0)
        if success_count > 0:
            score += 45
            if 0 <= last_success_age <= 300:
                score += 25
            elif 0 <= last_success_age <= 900:
                score += 10
        else:
            score -= 15
        score -= min(60, max(0, failure_count) * 8)

        if failure_kind == "rate_limited":
            score -= 80
        elif failure_kind == "terminal":
            score -= 200
        elif failure_kind in {"auth_or_cf", "page_view_blocked", "failed_to_fetch"}:
            score -= 45 if success_count > 0 else 70

        if health.state == "ready_query" and success_count > 0 and wait <= 0:
            role = "hot_query"
            status = "query_wait"
            status_zh = "查询状态（等候中）"
            action = "reuse_now"
            eligible = True
            reason = "成功会话已就绪，可优先复用"
        elif health.state == "ready_query" and success_count > 0 and wait > 0:
            role = "hot_query"
            status = "query_cooling"
            status_zh = "查询状态（冷却中）"
            action = "cool"
            eligible = False
            reason = f"成功会话保护冷却中，{wait:.0f}s 后可复用"
        elif health.state == "ready_query" and success_count <= 0 and wait <= 0:
            role = "login_standby"
            status = "query_wait"
            status_zh = "查询状态（等候中）"
            action = "probe_once"
            eligible = True
            reason = "已到首页/预约页但未产生成功查询，可作为冷启动候选"
        elif health.state == "querying":
            role = "hot_query" if success_count > 0 else "candidate"
            status = "querying"
            status_zh = "查询状态（使用中）"
            action = "hold"
            eligible = False
            reason = "正在占用业务查询 gate"
        elif health.state == "cooling":
            role = "hot_query" if success_count > 0 else "candidate"
            status = "query_cooling"
            status_zh = "查询状态（冷却中）"
            action = "cool"
            eligible = False
            reason = f"会话冷却中，{wait:.0f}s 后再评估"
        elif health.state in {"recoverable_cf", "recoverable_login", "waiting_room"}:
            role = "recovering" if success_count > 0 else "candidate"
            status = "recovering"
            status_zh = "查询状态（恢复中）" if success_count > 0 else "查询状态（未就绪）"
            action = "recover_once" if success_count > 0 else "produce"
            eligible = False
            reason = f"{health.reason}，需先恢复到首页/预约页"
        elif health.state in {"rate_limited", "network_bad"}:
            role = "recovering"
            status = "recovering"
            status_zh = "查询状态（恢复中）"
            action = "cool_or_recycle"
            eligible = False
            reason = f"{health.reason}，不可占用业务查询 gate"
        elif health.state == "terminal_risk":
            role = "terminal"
            status = "terminal"
            status_zh = "错误状态（需重置）"
            action = "terminal_recycle"
            eligible = False
            reason = f"{health.reason}，应结束本轮并重置"
        else:
            role = "candidate"
            status = "not_ready"
            status_zh = "查询状态（未就绪）"
            action = "produce"
            eligible = False
            reason = health.reason or "尚未到达可查询页面"

        if failure_kind and failure_kind != "none":
            reason = f"{reason}；最近失败={failure_kind}"

        blocked = "" if eligible else reason
        return ReuseHealth(
            slot_id=slot_id,
            pool_role=role,
            scheduler_status=status,
            scheduler_status_zh=status_zh,
            recommended_action=action,
            query_eligible=eligible,
            reuse_score=max(-500, min(250, score)),
            reason=reason,
            blocked_reason=blocked,
            success_count=success_count,
            failure_count=failure_count,
            next_query_at=next_query_at,
            next_query_eta_seconds=wait,
            last_success_at=last_success_at,
            last_success_age_seconds=last_success_age,
            recent_failure_kind=failure_kind,
            health_state=health.state,
            health_reason=health.reason,
        )

    def _failure_kind(self, session: dict[str, Any], slot: dict[str, Any]) -> str:
        text = " ".join(
            str(x or "")
            for x in (
                session.get("last_cooldown_mode"),
                session.get("last_cooldown_reason"),
                session.get("last_message"),
                slot.get("last_reason"),
                slot.get("last_reason_zh"),
                slot.get("smart_query_wait_reason"),
                slot.get("recovery_error_type"),
                slot.get("live_page_stage"),
                slot.get("live_page_reason"),
            )
        ).lower()
        for kind, tokens in FAILURE_TOKENS.items():
            if any(t in text for t in tokens):
                return kind
        return "none"
