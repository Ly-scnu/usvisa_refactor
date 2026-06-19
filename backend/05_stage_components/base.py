from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol

SessionContext = import_module("02_session_context.context").SessionContext


@dataclass
class StageResult:
    ok: bool
    stage: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


class StageStrategy(Protocol):
    stage_name: str

    async def execute(self, ctx: SessionContext) -> StageResult:
        ...
