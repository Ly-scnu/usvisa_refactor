from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SessionHealth:
    slot_id: str
    state: str
    query_ready: bool
    gate_allowed: bool
    score: int
    reason: str
    page: str = ""
    stage: str = ""
    query_state: str = ""
    next_query_at: str = ""
    observed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
