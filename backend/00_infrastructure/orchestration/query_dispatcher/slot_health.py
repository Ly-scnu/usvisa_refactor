from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
SessionHealthClassifier = import_module("00_infrastructure.orchestration.session_health.classifier").SessionHealthClassifier

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
READY_PAGES = {"home", "schedule", "site"}


@dataclass
class SlotHealth:
    slot_id: str
    usable: bool
    score: int
    page: str
    query_state: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SlotHealthAnalyzer:
    """Scores slot readiness for dispatcher visibility.

    This does not execute browser actions; it only explains to the UI and gate
    why a slot can or cannot be selected as primary/backup candidate.
    """

    def __init__(self) -> None:
        self.classifier = SessionHealthClassifier()

    def score(self, slot: dict[str, Any], session: dict[str, Any] | None = None) -> SlotHealth:
        health = self.classifier.classify(slot, session)
        usable = bool(health.query_ready)
        reason = health.reason
        if health.state == "cooling":
            usable = False
            reason = "session_cooldown"
        elif health.state == "terminal_risk":
            reason = "hard_risk_block"
        elif health.state in {"recoverable_cf", "recoverable_login", "waiting_room", "rate_limited", "network_bad"}:
            reason = f"page_{health.page or health.state}"
        elif health.state == "not_running":
            reason = "slot_not_running"
        elif health.state == "querying":
            reason = "already_querying"
        return SlotHealth(
            slot_id=health.slot_id,
            usable=usable,
            score=health.score,
            page=health.page,
            query_state=health.query_state,
            reason=reason,
        )
