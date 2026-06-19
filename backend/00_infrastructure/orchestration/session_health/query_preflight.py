from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .classifier import SessionHealthClassifier
from .models import SessionHealth


@dataclass
class QueryPreflightResult:
    allowed: bool
    health: SessionHealth
    wait_seconds: float = 0.0
    reason: str = ""
    next_allowed_at: str = ""


class QueryPreflight:
    """Gate preflight: reject sessions that are not currently query-capable."""

    BLOCK_WAIT_SECONDS = {
        "recoverable_cf": 1.0,
        "recoverable_login": 1.0,
        "waiting_room": 2.0,
        "rate_limited": 10.0,
        "network_bad": 5.0,
        "terminal_risk": 0.5,
        "not_running": 10.0,
        "not_ready": 2.0,
    }

    def __init__(self, classifier: SessionHealthClassifier | None = None):
        self.classifier = classifier or SessionHealthClassifier()

    def check(self, slot: dict[str, Any] | None, session: dict[str, Any] | None = None) -> QueryPreflightResult:
        health = self.classifier.classify(slot, session)
        if health.gate_allowed:
            return QueryPreflightResult(True, health, reason=health.reason, next_allowed_at=health.next_query_at)
        reason = f"preflight_{health.state}"
        wait = self.BLOCK_WAIT_SECONDS.get(health.state, 2.0)
        return QueryPreflightResult(False, health, wait_seconds=wait, reason=reason, next_allowed_at=health.next_query_at)
