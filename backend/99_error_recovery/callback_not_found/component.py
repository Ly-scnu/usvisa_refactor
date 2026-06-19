from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..components import BaseRecoveryComponent, RecoveryResult


class CallbackNotFoundRecovery(BaseRecoveryComponent):
    """Fast reset for broken USVisaScheduling/B2C callback pages.

    The portal sometimes lands on a Customer Self-Service "Page Not Found"
    document at ``signin-aad-b2c_1`` after B2C/CF redirects.  That is not a
    normal spinner: waiting there makes the slot look like "idp_loading" while
    it can no longer reach home/schedule.  Treat it as a bad browser context
    and let the scheduler produce a fresh round instead of occupying query
    capacity.
    """

    error_type = "callback_not_found"
    display_name = "B2C callback Page Not Found fast reset"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "callback_not_found")
        evidence["detected_at"] = datetime.now(timezone.utc).isoformat()
        evidence["input_error"] = error
        evidence["reset_policy"] = {
            "same_page_retry": False,
            "reload": False,
            "close_context": True,
            "new_browser_profile": True,
            "new_proxy": True,
            "restart_next_round": True,
        }

        message_zh = "B2C 登录回跳进入 Page Not Found：已保存证据，立即关闭当前浏览器/画像/代理，本轮结束并由调度器重新产出票"
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                state="running",
                stage="round_recycle",
                stage_zh="本轮回收",
                waiting_acquired=False,
                last_reason="callback_not_found_reset_restart",
                last_reason_zh=message_zh,
                recovery_error_type=self.error_type,
                recovery_action="reset_proxy_profile_restart_round",
                recovery_component="CallbackNotFoundRecovery",
            )

        close_ok = False
        try:
            await ctx.close()
            close_ok = True
        except Exception as exc:
            evidence["close_error"] = repr(exc)
        evidence["context_close_ok"] = close_ok

        ctx.browser_bundle = None
        ctx.browser = None
        ctx.browser_context = None
        ctx.page = None
        ctx.proxy = None
        ctx.proxy_material = None

        return RecoveryResult(
            True,
            self.error_type,
            "reset_proxy_profile_restart_round",
            message_zh,
            retryable=True,
            evidence=evidence,
            next_strategy={
                "recycle_context": True,
                "restart_next_round": True,
                "same_session": False,
                "do_not_wait_callback": True,
                "do_not_reload": True,
                "new_proxy": True,
                "new_browser_profile": True,
                "fast_handoff": True,
            },
        )
