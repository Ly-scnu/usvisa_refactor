from __future__ import annotations

import random
from typing import Any

from .base import CfClickResult, click_point


class CloakHumanizedMouseStrategy:
    """Use CloakBrowser-patched Playwright mouse actions.

    This is the closest route to the wrapper's native humanize behavior.  It is
    kept as an explicit strategy because Turnstile sometimes responds more
    reliably to lower-level CDP input than to high-level mouse wrappers.
    """

    name = "cloak_humanized_mouse"

    async def click(self, page: Any, info: dict[str, Any], *, widget_id: str = "", click_index: int = 0) -> CfClickResult:
        x, y = click_point(info, jitter=True)
        try:
            await page.mouse.move(x + random.uniform(-18, 18), y + random.uniform(-12, 12))
            await page.wait_for_timeout(random.randint(90, 220))
            await page.mouse.move(x, y)
            await page.wait_for_timeout(random.randint(80, 190))
            await page.mouse.down()
            await page.wait_for_timeout(random.randint(70, 180))
            await page.mouse.up()
            return CfClickResult(True, self.name, x, y, {"widget_id": widget_id, "click_index": click_index})
        except Exception as exc:
            return CfClickResult(False, self.name, x, y, {"widget_id": widget_id, "click_index": click_index}, repr(exc))

