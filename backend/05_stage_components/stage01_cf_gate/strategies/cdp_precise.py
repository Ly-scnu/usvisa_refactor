from __future__ import annotations

from typing import Any

from .base import CfClickResult, click_point


class CdpPreciseClickStrategy:
    name = "cdp_precise"

    async def click(self, page: Any, info: dict[str, Any], *, widget_id: str = "", click_index: int = 0) -> CfClickResult:
        x, y = click_point(info, jitter=False)
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0})
            await page.wait_for_timeout(80)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1})
            await page.wait_for_timeout(130)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1})
            return CfClickResult(True, self.name, x, y, {"widget_id": widget_id, "click_index": click_index, "mode": "legacy_fixed_point"})
        except Exception as exc:
            return CfClickResult(False, self.name, x, y, {"widget_id": widget_id, "click_index": click_index}, repr(exc))

