from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .failure_classifier import FailureClassifier, FailureClassification


@dataclass(frozen=True)
class TerminationDecision:
    action: str
    terminal: bool
    reason: str
    error_type: str
    handoff: bool
    cooldown_seconds: float = 0.0
    classification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TerminationPolicy:
    """Decide whether a failed session should end immediately."""

    def __init__(self, classifier: FailureClassifier | None = None):
        self.classifier = classifier or FailureClassifier()

    def decide(
        self,
        result: Any,
        *,
        successful_queries: int = 0,
        consecutive_kind_failures: int = 1,
    ) -> TerminationDecision:
        fc: FailureClassification = self.classifier.classify(result)
        if fc.severity == "terminal":
            return TerminationDecision(
                action="terminate_session",
                terminal=True,
                reason=fc.reason,
                error_type=fc.kind,
                handoff=True,
                classification=fc.to_dict(),
            )
        if fc.kind == "failed_to_fetch":
            if successful_queries <= 0:
                return TerminationDecision(
                    action="fast_recycle",
                    terminal=True,
                    reason="未成功查询过的会话出现 Failed to fetch：立即回收，避免反复占 gate",
                    error_type=fc.kind,
                    handoff=True,
                    classification=fc.to_dict(),
                )
            if consecutive_kind_failures >= 2:
                return TerminationDecision(
                    action="terminate_session",
                    terminal=True,
                    reason="成功会话连续 Failed to fetch：判定当前上下文已坏，立即回收",
                    error_type=fc.kind,
                    handoff=True,
                    classification=fc.to_dict(),
                )
            return TerminationDecision(
                action="recover_once",
                terminal=False,
                reason="成功会话首次 Failed to fetch：允许恢复一次，失败后回收",
                error_type=fc.kind,
                handoff=True,
                classification=fc.to_dict(),
            )
        if fc.severity == "recoverable":
            return TerminationDecision(
                action="recover_once",
                terminal=False,
                reason=fc.reason,
                error_type=fc.kind,
                handoff=True,
                classification=fc.to_dict(),
            )
        return TerminationDecision(
            action="soft_retry",
            terminal=False,
            reason=fc.reason,
            error_type=fc.kind,
            handoff=False,
            classification=fc.to_dict(),
        )
