from __future__ import annotations

from datetime import datetime, timezone
import random
from typing import Any


def now_dt() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def seconds_until(ts: Any) -> float:
    dt = parse_ts(ts)
    if not dt:
        return 0.0
    return max(0.0, (dt - now_dt()).total_seconds())


def add_seconds(base: datetime | None, seconds: float) -> str:
    import datetime as _dt

    b = base or now_dt()
    return (b + _dt.timedelta(seconds=max(0.0, float(seconds)))).isoformat(timespec="seconds")


def jitter_seconds(bounds: Any, default: tuple[int, int] = (0, 0)) -> int:
    try:
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
            lo, hi = int(bounds[0]), int(bounds[1])
        else:
            lo, hi = default
        if hi < lo:
            lo, hi = hi, lo
        return random.randint(lo, hi)
    except Exception:
        return 0

