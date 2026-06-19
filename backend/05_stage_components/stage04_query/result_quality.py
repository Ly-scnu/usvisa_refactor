from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class QueryResultQuality:
    valid_success: bool
    completed: bool
    empty: bool
    days_count: int
    token_len: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def judge_query_result(payload: dict[str, Any] | None, *, ok: bool = True) -> QueryResultQuality:
    """Classify a business days query result.

    ``HTTP 200`` is not always a useful ticket-pool success.  Runtime evidence
    showed ``days_count=0`` / ``token_len=0`` rows entering the success stream
    and refreshing SLA timers, which hides real blank windows.  A valid success
    means we actually received at least one date from schedule_days.
    """
    data = payload or {}
    days = data.get("normalized_days") or data.get("days") or []
    days_count = int(data.get("days_count") or len(days or []))
    token_len = 0
    for step in data.get("steps") or []:
        if isinstance(step, dict) and step.get("name") == "schedule_days":
            try:
                token_len = int(step.get("token_len") or token_len or 0)
            except Exception:
                pass
    completed = bool(ok and data.get("days") is not None)
    if not completed:
        return QueryResultQuality(False, False, False, days_count, token_len, "business_api_not_completed")
    if days_count > 0:
        return QueryResultQuality(True, True, False, days_count, token_len, "valid_days")
    return QueryResultQuality(False, True, True, days_count, token_len, "empty_days_response")
