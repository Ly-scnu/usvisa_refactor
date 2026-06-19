from __future__ import annotations

from importlib import import_module
from typing import Any

from .models import SessionHealth

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


READY_PAGES = {"home", "schedule", "site"}
RECOVERABLE_CF_PAGES = {"cf_challenge", "idp_loading"}
RECOVERABLE_LOGIN_PAGES = {"login", "security_questions", "login_failed"}
WAITING_ROOM_PAGES = {"waiting_room"}
NETWORK_BAD_PAGES = {"network_error", "blank"}
TERMINAL_RISK_PAGES = {"rate_limit_1015", "access_denied"}
PAGE_NOT_FOUND_PAGES = {"callback_not_found", "page_not_found"}

HARD_RISK_TOKENS = ("1015", "1020", "access_denied", "access denied", "proxy_banned", "blocked")
NETWORK_TOKENS = ("network_error", "net::", "err_tunnel", "err_proxy", "connection reset", "timeout")
LOGIN_TOKENS = ("login", "security", "密保", "登录")
CF_TOKENS = ("cf_challenge", "cloudflare", "turnstile", "人机")


class SessionHealthClassifier:
    """Single readiness classifier for query gate, candidate queue and UI.

    It only reads persisted slot/session evidence.  It does not drive browser
    actions and it does not decide cadence timing beyond exposing cooldown.
    """

    def classify(self, slot: dict[str, Any] | None, session: dict[str, Any] | None = None) -> SessionHealth:
        slot = slot or {}
        session = session or {}
        slot_id = str(slot.get("slot") or slot.get("slot_id") or session.get("slot_id") or "")
        state = str(slot.get("state") or "")
        page = str(slot.get("live_page_stage") or "")
        stage = str(slot.get("stage") or "")
        query_state = str(slot.get("smart_query_state") or session.get("state") or "")
        next_query_at = str(session.get("next_query_at") or slot.get("smart_query_next_allowed_at") or "")
        observed_at = str(slot.get("live_page_observed_at") or "")
        success_count = int(session.get("success_count") or slot.get("session_successful_queries") or 0)
        hay = " ".join(
            str(slot.get(k) or "")
            for k in (
                "last_reason",
                "last_reason_zh",
                "live_page_reason",
                "recovery_error_type",
                "recovery_action",
                "stage",
                "stage_zh",
                "smart_query_wait_reason",
            )
        ).lower()

        def h(name: str, query_ready: bool, gate_allowed: bool, score: int, reason: str) -> SessionHealth:
            return SessionHealth(
                slot_id=slot_id,
                state=name,
                query_ready=query_ready,
                gate_allowed=gate_allowed,
                score=score,
                reason=reason,
                page=page,
                stage=stage,
                query_state=query_state,
                next_query_at=next_query_at,
                observed_at=observed_at,
            )

        if state != "running":
            return h("not_running", False, False, -500, "slot_not_running")
        if page in PAGE_NOT_FOUND_PAGES:
            return h("terminal_risk", False, False, -430, page)
        if page in TERMINAL_RISK_PAGES or any(token in hay for token in HARD_RISK_TOKENS):
            return h("terminal_risk", False, False, -450, page or "hard_risk_block")
        if page in NETWORK_BAD_PAGES or any(token in hay for token in NETWORK_TOKENS):
            return h("network_bad", False, False, -350, page or "network_bad")
        if page == "rate_limit_429":
            return h("rate_limited", False, False, -300, "rate_limit_429")
        if page in RECOVERABLE_CF_PAGES or any(token in hay for token in CF_TOKENS):
            return h("recoverable_cf", False, False, -240, page or "cf_challenge")
        if page in RECOVERABLE_LOGIN_PAGES:
            return h("recoverable_login", False, False, -220, page)
        if page in WAITING_ROOM_PAGES:
            return h("waiting_room", False, False, -210, "waiting_room")
        if "auth_or_cf" in hay:
            return h("recoverable_cf", False, False, -230, "auth_or_cf")
        if any(token in hay for token in LOGIN_TOKENS) and page not in READY_PAGES and stage != "business_query":
            return h("recoverable_login", False, False, -180, "login_or_security")
        if query_state in {"querying", "api_querying"}:
            return h("querying", False, False, -100, "already_querying")

        wait = clock.seconds_until(next_query_at)
        if wait > 0:
            # Cooldown is a cadence decision.  The gate may bypass it for SLA
            # coverage, so do not hard-block reserve() here.
            return h("cooling", False, True, -40, "session_cooldown")

        if page in READY_PAGES or stage == "business_query":
            score = 100
            if page == "schedule":
                score += 30
            if stage == "business_query":
                score += 30
            if success_count > 0:
                score += 40
            if query_state in {"ready_warm", "ready_hot", "cooling", "waiting"}:
                score += 10
            return h("ready_query", True, True, score, "ready")

        if not page and stage in {"round_recycle", "direct_only_recycle"}:
            return h("not_ready", False, False, -120, "round_recycle")

        return h("not_ready", False, False, -80, page or stage or "not_query_ready")
