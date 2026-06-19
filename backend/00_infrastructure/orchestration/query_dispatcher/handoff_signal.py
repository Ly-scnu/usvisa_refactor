from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ...utils.time import iso_now


@dataclass(frozen=True)
class HandoffSignal:
    seq: int
    created_at: str
    reason: str
    source_slot: str
    source_session: str
    success: bool
    prefer_slots: list[str]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HandoffBuilder:
    def build(
        self,
        previous: dict[str, Any] | None,
        *,
        reason: str,
        source_slot: str,
        source_session: str,
        success: bool,
        prefer_slots: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> HandoffSignal:
        seq = int((previous or {}).get("seq") or 0) + 1
        return HandoffSignal(
            seq=seq,
            created_at=iso_now(),
            reason=reason,
            source_slot=source_slot,
            source_session=source_session,
            success=success,
            prefer_slots=list(prefer_slots or []),
            payload=payload or {},
        )
