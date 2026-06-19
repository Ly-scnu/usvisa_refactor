from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TerminationDecision:
    action: str = "recover"  # recover | cooldown_continue | terminal_end
    error_type: str = "unknown"
    terminal: bool = False
    recoverable: bool = True
    reason: str = "可恢复错误，保留会话"
    cooldown_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
