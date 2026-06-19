from __future__ import annotations

import math
import random
from typing import Any

from .base import CfClickResult, click_point


def _fallback_curve(start: tuple[float, float], end: tuple[float, float], steps: int) -> list[tuple[float, float]]:
    sx, sy = start
    ex, ey = end
    out: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / max(1, steps)
        # smoothstep + slight arc, intentionally modest; Turnstile wants a
        # trusted input event more than a visually dramatic path.
        s = t * t * (3 - 2 * t)
        arc = math.sin(math.pi * t) * random.uniform(-8.0, 8.0)
        out.append((sx + (ex - sx) * s + arc * 0.15, sy + (ey - sy) * s + arc))
    return out


class CloakCurveCdpClickStrategy:
    """CloakBrowser-humanize shaped movement + low-level CDP press/release.

    CloakBrowser's native humanize layer patches Playwright's high-level page
    methods.  The stable historical Turnstile path, however, uses trusted CDP
    mouse events against the iframe coordinates.  This strategy combines both
    ideas: read the CloakBrowser human preset when available, generate a
    matching curve, but keep the final Input.dispatchMouseEvent delivery.
    """

    name = "cloak_curve_cdp"

    def _config(self) -> dict[str, Any]:
        try:
            from cloakbrowser.human.config import resolve_config  # type: ignore

            cfg = resolve_config("careful", None)
            return {
                "mouse_steps_divisor": float(getattr(cfg, "mouse_steps_divisor", 8) or 8),
                "mouse_min_steps": int(getattr(cfg, "mouse_min_steps", 25) or 25),
                "mouse_max_steps": int(getattr(cfg, "mouse_max_steps", 80) or 80),
                "mouse_burst_pause": tuple(getattr(cfg, "mouse_burst_pause", (12, 25)) or (12, 25)),
                "click_hold_button": tuple(getattr(cfg, "click_hold_button", (80, 200)) or (80, 200)),
                "click_aim_delay_button": tuple(getattr(cfg, "click_aim_delay_button", (120, 280)) or (120, 280)),
                "source": "cloakbrowser.careful",
            }
        except Exception:
            return {
                "mouse_steps_divisor": 8.0,
                "mouse_min_steps": 24,
                "mouse_max_steps": 70,
                "mouse_burst_pause": (12, 25),
                "click_hold_button": (80, 180),
                "click_aim_delay_button": (100, 220),
                "source": "fallback",
            }

    async def click(self, page: Any, info: dict[str, Any], *, widget_id: str = "", click_index: int = 0) -> CfClickResult:
        x, y = click_point(info, jitter=True)
        cfg = self._config()
        try:
            cdp = await page.context.new_cdp_session(page)
            start_x = max(8.0, x + random.uniform(-260, -85))
            start_y = max(8.0, y + random.uniform(-95, 105))
            dist = max(1.0, math.hypot(x - start_x, y - start_y))
            steps = max(int(cfg["mouse_min_steps"]), min(int(cfg["mouse_max_steps"]), round(dist / float(cfg["mouse_steps_divisor"]))))
            points = _fallback_curve((start_x, start_y), (x, y), steps)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(start_x), "y": round(start_y), "button": "none", "buttons": 0})
            lo, hi = cfg["click_aim_delay_button"]
            await page.wait_for_timeout(random.randint(int(lo), int(hi)))
            burst_lo, burst_hi = cfg["mouse_burst_pause"]
            burst_every = random.randint(3, 6)
            for i, (mx, my) in enumerate(points[1:], start=1):
                await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": round(mx), "y": round(my), "button": "none", "buttons": 0})
                if i % burst_every == 0:
                    await page.wait_for_timeout(random.randint(int(burst_lo), int(burst_hi)))
            hold_lo, hold_hi = cfg["click_hold_button"]
            await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": round(x), "y": round(y), "button": "left", "buttons": 1, "clickCount": 1})
            await page.wait_for_timeout(random.randint(int(hold_lo), int(hold_hi)))
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": round(x), "y": round(y), "button": "left", "buttons": 0, "clickCount": 1})
            return CfClickResult(True, self.name, x, y, {"widget_id": widget_id, "click_index": click_index, "steps": steps, "distance": round(dist, 1), "config_source": cfg["source"]})
        except Exception as exc:
            return CfClickResult(False, self.name, x, y, {"widget_id": widget_id, "click_index": click_index, "config_source": cfg.get("source")}, repr(exc))

