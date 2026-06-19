from __future__ import annotations

from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


def seconds_between(a: Any, b: Any) -> float:
    da = clock.parse_ts(a)
    db = clock.parse_ts(b)
    if not da or not db:
        return 0.0
    return max(0.0, (db - da).total_seconds())


def build_reserve_latency(state: dict[str, Any], *, now_s: str, slot_id: str, session_key: str) -> dict[str, Any]:
    last_completion = state.get("last_completion") if isinstance(state.get("last_completion"), dict) else {}
    if not last_completion:
        return {}
    return {
        "slot_id": slot_id,
        "session_key": session_key,
        "previous_slot": last_completion.get("slot_id") or "",
        "previous_session": last_completion.get("session_key") or "",
        "previous_success": bool(last_completion.get("success")),
        "since_previous_completion_ms": round(seconds_between(last_completion.get("at"), now_s) * 1000, 1),
        "at": now_s,
    }


def build_completion_latency(active: dict[str, Any] | None, *, now_s: str) -> dict[str, Any]:
    active = active or {}
    return {
        "gate_hold_ms": round(seconds_between(active.get("started_at"), now_s) * 1000, 1),
        "active_started_at": active.get("started_at") or "",
        "at": now_s,
    }
