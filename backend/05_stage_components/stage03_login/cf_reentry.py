from __future__ import annotations

from importlib import import_module
from typing import Any


class LoginCfReentryHandler:
    """Handle Cloudflare challenges injected in the middle of login.

    The normal first gate is stage01_cf_gate.  During the B2C flow the official
    site can inject another CF page at SignIn/ExternalLoginCallback/
    signin-aad-b2c_1/profile.  Treating that as a completed login failure makes
    the scheduler start a fresh browser/profile too often and increases CF
    pressure.  This helper keeps the recovery local to the login stage: solve
    the challenge in the same browser context, then return to the login loop.
    """

    def __init__(self, max_attempts: int = 3, max_seconds: int = 75):
        self.max_attempts = max(1, int(max_attempts or 3))
        self.max_seconds = max(20, int(max_seconds or 75))
        self._cf_gate_cls = import_module("05_stage_components.stage01_cf_gate.executor").CfGateStage
        self._classify_page = import_module("03_browser_management.page_classifier").classify_page

    async def maybe_handle(self, ctx: Any, state: Any, events: list[dict[str, Any]], *, attempt_no: int, where: str) -> dict[str, Any]:
        stage = str(getattr(state, "stage", "") or "")
        if stage != "cf_challenge":
            return {"handled": False, "ok": False, "reason": f"not_cf:{stage}"}
        if attempt_no > self.max_attempts:
            return {"handled": False, "ok": False, "reason": "cf_reentry_attempt_budget_exhausted"}

        events.append(
            {
                "event": "login_cf_reentry_begin",
                "attempt": attempt_no,
                "where": where,
                "state": getattr(state, "__dict__", {}),
            }
        )
        result = await self._cf_gate_cls(max_seconds=self.max_seconds).execute(ctx)
        after = await self._classify_page(ctx.page)
        payload = {
            "handled": True,
            "ok": bool(result.ok),
            "attempt": attempt_no,
            "where": where,
            "cf_gate_message": result.message,
            "cf_gate_stage": result.stage,
            "after_state": after.__dict__,
        }
        events.append({"event": "login_cf_reentry_end", **payload})
        return payload

