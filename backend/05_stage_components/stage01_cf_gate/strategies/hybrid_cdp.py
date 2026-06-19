from __future__ import annotations

import math
import random
from typing import Any

from .base import CfClickResult, click_point


def _ease_in_out(t: float) -> float:
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _bezier(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], t: float) -> tuple[float, float]:
    u = 1 - t
    return (
        (u ** 3) * p0[0] + 3 * (u ** 2) * t * p1[0] + 3 * u * (t ** 2) * p2[0] + (t ** 3) * p3[0],
        (u ** 3) * p0[1] + 3 * (u ** 2) * t * p1[1] + 3 * u * (t ** 2) * p2[1] + (t ** 3) * p3[1],
    )


class HybridCdpClickStrategy:
    """Human-shaped movement with CDP press/release.

    It keeps the reliable Turnstile-compatible CDP input path while removing
    the old fixed-pixel, fixed-timing mouseMoved -> press -> release pattern.
    """

    name = "hybrid_cdp"

    async def click(self, page: Any, info: dict[str, Any], *, widget_id: str = "", click_index: int = 0) -> CfClickResult:
        x, y = click_point(info, jitter=True)
        try:
            cdp = await page.context.new_cdp_session(page)
            start_x = max(8.0, x + random.uniform(-220, -90))
            start_y = max(8.0, y + random.uniform(-80, 90))
            dx = x - start_x
            dy = y - start_y
            dist = max(1.0, math.hypot(dx, dy))
            px = -dy / dist
            py = dx / dist
            p0 = (start_x, start_y)
            p3 = (x, y)
            p1 = (start_x + dx * 0.25 + px * random.uniform(-0.25, 0.25) * dist, start_y + dy * 0.25 + py * random.uniform(-0.25, 0.25) * dist)
            p2 = (start_x + dx * 0.75 + px * random.uniform(-0.25, 0.25) * dist, start_y + dy * 0.75 + py * random.uniform(-0.25, 0.25) * dist)
            steps = max(18, min(65, round(dist / random.uniform(7.0, 11.0))))
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(start_x), "y": round(start_y), "button": "none", "buttons": 0})
            await page.wait_for_timeout(random.randint(80, 180))
            for i in range(1, steps + 1):
                t = _ease_in_out(i / steps)
                mx, my = _bezier(p0, p1, p2, p3, t)
                wobble = math.sin(math.pi * (i / steps)) * random.uniform(0.2, 1.6)
                mx += random.uniform(-wobble, wobble)
                my += random.uniform(-wobble, wobble)
                await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(mx), "y": round(my), "button": "none", "buttons": 0})
                if i % random.randint(3, 6) == 0:
                    await page.wait_for_timeout(random.randint(7, 22))
            if random.random() < 0.18:
                await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(x + random.uniform(-4, 6)), "y": round(y + random.uniform(-4, 5)), "button": "none", "buttons": 0})
                await page.wait_for_timeout(random.randint(35, 90))
                await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(x), "y": round(y), "button": "none", "buttons": 0})
            await page.wait_for_timeout(random.randint(95, 240))
            await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": round(x), "y": round(y), "button": "left", "buttons": 1, "clickCount": 1})
            await page.wait_for_timeout(random.randint(70, 190))
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": round(x), "y": round(y), "button": "left", "buttons": 0, "clickCount": 1})
            return CfClickResult(True, self.name, x, y, {"widget_id": widget_id, "click_index": click_index, "steps": steps, "distance": round(dist, 1)})
        except Exception as exc:
            return CfClickResult(False, self.name, x, y, {"widget_id": widget_id, "click_index": click_index}, repr(exc))

