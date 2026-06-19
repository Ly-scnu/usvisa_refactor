from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Any


@dataclass
class PeakMode:
    active: bool = False
    prewarm: bool = False
    cooldown: bool = False
    name: str = ""
    mode: str = "normal"
    target_success_interval_seconds: float | None = None
    desired_active_slots: int | None = None
    min_hot_sessions: int | None = None
    reason: str = "普通时段"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_hhmm(value: str) -> time | None:
    try:
        hh, mm = str(value).strip().split(":", 1)
        return time(int(hh), int(mm))
    except Exception:
        return None


def _window_datetimes(now: datetime, start_s: str, end_s: str) -> tuple[datetime, datetime] | None:
    st, et = _parse_hhmm(start_s), _parse_hhmm(end_s)
    if not st or not et:
        return None
    start = now.replace(hour=st.hour, minute=st.minute, second=0, microsecond=0)
    end = now.replace(hour=et.hour, minute=et.minute, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
        if now < start:
            start -= timedelta(days=1)
            end -= timedelta(days=1)
    return start, end


def _parse_minute_range(value: str) -> tuple[int, int] | None:
    """Parse recurring release-period minute ranges such as ``58-03``.

    ``58-03`` means every hour from HH:58 to next hour HH+1:03.
    ``28-33`` means every hour from HH:28 to HH:33.  The user calls these
    windows 放票期; they are deliberately independent of a fixed hour.
    """
    try:
        raw = str(value or "").strip().replace(":", "-").replace("~", "-").replace("到", "-")
        a, b = [int(x) for x in raw.split("-", 1)]
        if 0 <= a <= 59 and 0 <= b <= 59:
            return a, b
    except Exception:
        return None
    return None


def _recurring_minute_windows(now: datetime, minute_range: str) -> list[tuple[datetime, datetime]]:
    parsed = _parse_minute_range(minute_range)
    if not parsed:
        return []
    start_min, end_min = parsed
    base_hour = now.replace(minute=0, second=0, microsecond=0)
    out: list[tuple[datetime, datetime]] = []
    # Generate neighboring hour windows so cross-hour ranges (:58-:03) are
    # detected correctly both before and after the hour changes.
    for hour_offset in (-1, 0, 1):
        base = base_hour + timedelta(hours=hour_offset)
        start = base.replace(minute=start_min)
        if end_min <= start_min:
            end = (base + timedelta(hours=1)).replace(minute=end_min)
        else:
            end = base.replace(minute=end_min)
        out.append((start, end))
    return out


class PeakWindowPolicy:
    """Time-window policy for known release-prone periods."""

    def __init__(self, config: Any):
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def current(self, now: datetime | None = None) -> PeakMode:
        now = now or datetime.now().astimezone()
        windows = list(getattr(self.cfg, "peak_windows", []) or [])
        prewarm_min = int(getattr(self.cfg, "peak_prewarm_minutes", 5) or 5)
        cooldown_min = int(getattr(self.cfg, "peak_cooldown_minutes", 3) or 3)
        candidates: list[PeakMode] = []

        def add_window(name: str, start: datetime, end: datetime, *, target_interval: float, desired_slots: int, min_hot: int, source: str) -> None:
            prewarm_start = start - timedelta(minutes=prewarm_min)
            cooldown_end = end + timedelta(minutes=cooldown_min)
            if start <= now <= end:
                candidates.append(PeakMode(True, False, False, name, "peak", target_interval, desired_slots, min_hot, f"命中放票期 {name}（{source}），进入高压查询准备"))
            elif prewarm_start <= now < start:
                candidates.append(PeakMode(False, True, False, name, "prewarm", target_interval, desired_slots, min_hot, f"距离放票期 {name} 不足 {prewarm_min} 分钟，提前预热"))
            elif end < now <= cooldown_end:
                candidates.append(PeakMode(False, False, True, name, "cooldown", None, None, None, f"放票期 {name} 刚结束，进入排水观察"))

        for win in windows:
            pair = _window_datetimes(now, getattr(win, "start", ""), getattr(win, "end", ""))
            if not pair:
                continue
            start, end = pair
            name = str(getattr(win, "name", "peak") or "peak")
            add_window(
                name,
                start,
                end,
                target_interval=float(getattr(win, "target_success_interval_seconds", 30.0) or 30.0),
                desired_slots=int(getattr(win, "desired_active_slots", 8) or 8),
                min_hot=int(getattr(win, "min_hot_sessions", 4) or 4),
                source="固定窗口",
            )

        if bool(getattr(self.cfg, "release_periods_enabled", True)):
            for minute_range in list(getattr(self.cfg, "release_period_windows", []) or []):
                for start, end in _recurring_minute_windows(now, str(minute_range)):
                    name = f"release_{str(minute_range).replace(':', '').replace('-', '_')}"
                    add_window(
                        name,
                        start,
                        end,
                        target_interval=float(getattr(self.cfg, "release_period_target_success_interval_seconds", 30.0) or 30.0),
                        desired_slots=int(getattr(self.cfg, "release_period_desired_active_slots", 8) or 8),
                        min_hot=int(getattr(self.cfg, "release_period_min_hot_sessions", 4) or 4),
                        source="每小时循环窗口",
                    )

        if candidates:
            priority = {"peak": 3, "prewarm": 2, "cooldown": 1}
            candidates.sort(key=lambda x: (priority.get(x.mode, 0), int(x.desired_active_slots or 0), int(x.min_hot_sessions or 0)), reverse=True)
            return candidates[0]
        return PeakMode()

    def effective_interval(self, default: float) -> float:
        mode = self.current()
        return float(mode.target_success_interval_seconds or default)
