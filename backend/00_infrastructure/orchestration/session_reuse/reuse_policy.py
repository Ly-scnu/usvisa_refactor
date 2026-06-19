from __future__ import annotations

from typing import Any

from .models import TerminationDecision
from .termination_classifier import TerminationClassifier


class SessionReusePolicy:
    """Small façade used by stage04 live loop.

    It keeps reuse semantics outside the query stage so future tuning (for
    example different 429 ladders or peak-time grace) does not turn query.py
    into another monolith.
    """

    def __init__(self, config: Any):
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)
        self.classifier = TerminationClassifier()

    def recovery_grace(self, *, success_count: int, default_grace: int) -> int:
        if success_count > 0:
            return max(default_grace, int(getattr(self.cfg, "successful_session_recovery_grace_rounds", 12) or 12))
        return max(1, int(default_grace or 1))

    def after_failure(self, result_or_payload: Any, *, success_count: int, consecutive_bad: int, default_grace: int) -> TerminationDecision:
        decision = self.classifier.classify(
            result_or_payload,
            success_count=success_count,
            consecutive_bad=consecutive_bad,
            recovery_grace=self.recovery_grace(success_count=success_count, default_grace=default_grace),
        )
        hard = max(1, int(getattr(self.cfg, "successful_session_hard_recycle_rounds", 3) or 3))
        if success_count > 0 and consecutive_bad >= hard and decision.error_type in {
            "cf_challenge",
            "login",
            "security_questions",
            "auth_or_cf",
            "auth_or_cf_block",
            "unknown",
        }:
            return TerminationDecision(
                "terminal_end",
                "hot_session_repeated_unhealthy",
                True,
                False,
                f"成功会话连续 {consecutive_bad} 次卡在 {decision.error_type}，不再占热池名额，回收会话/代理",
            )
        return decision
