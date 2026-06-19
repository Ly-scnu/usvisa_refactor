from __future__ import annotations

import random
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class CfClickResult:
    ok: bool
    strategy: str
    x: float
    y: float
    detail: dict[str, Any]
    error: str = ""

    def to_event(self) -> dict[str, Any]:
        return asdict(self)


def frame_box(info: dict[str, Any]) -> dict[str, float]:
    raw = info.get("box") if isinstance(info, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    return {
        "x": float(raw.get("x") or 0),
        "y": float(raw.get("y") or 0),
        "width": float(raw.get("width") or 0),
        "height": float(raw.get("height") or 0),
    }


def click_point(info: dict[str, Any], *, jitter: bool = True) -> tuple[float, float]:
    """Return a Turnstile checkbox-like target point within the iframe.

    The historical stable point is roughly iframe + (22, 30).  Keep that as the
    center, but add bounded jitter for non-precise strategies so repeated slots
    do not click an identical pixel.
    """
    b = frame_box(info)
    base_x = b["x"] + min(28.0, max(18.0, b["width"] * 0.075))
    base_y = b["y"] + min(38.0, max(24.0, b["height"] * 0.38))
    if not jitter:
        return b["x"] + 22.0, b["y"] + 30.0
    return base_x + random.uniform(-3.5, 4.5), base_y + random.uniform(-3.0, 4.0)

