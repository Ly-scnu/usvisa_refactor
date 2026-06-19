from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..components import BaseRecoveryComponent, RecoveryResult


class AccessDeniedResetRecovery(BaseRecoveryComponent):
    """Immediate reset for Cloudflare Access Denied / blocked pages.

    This component is intentionally isolated because Access Denied is not a
    same-page recoverable state.  The stable response is: save evidence, close
    the current CloakBrowser context/profile, release proxy material, and let
    the scheduler create the next round with a fresh route/profile.
    """

    error_type = "access_denied"
    display_name = "Cloudflare access denied immediate reset"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "access_denied_reset")
        evidence["detected_at"] = datetime.now(timezone.utc).isoformat()
        evidence["reset_policy"] = {
            "same_page_retry": False,
            "reload": False,
            "close_context": True,
            "new_proxy": True,
            "new_browser_profile": True,
            "restart_next_round": True,
        }
        evidence["input_error"] = error

        message_zh = "Cloudflare 已阻止访问：已保存截图，立即关闭当前浏览器/画像/代理，本轮结束并由调度器下一轮重新开始"
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                state="running",
                stage="round_recycle",
                stage_zh="本轮回收",
                waiting_acquired=False,
                last_reason="access_denied_reset_restart",
                last_reason_zh=message_zh,
                recovery_error_type="access_denied",
                recovery_action="reset_proxy_profile_restart_round",
                recovery_component="AccessDeniedResetRecovery",
            )

        close_ok = False
        try:
            await ctx.close()
            close_ok = True
        except Exception as exc:  # keep evidence; the cleanup below still runs
            evidence["close_error"] = repr(exc)
        evidence["context_close_ok"] = close_ok

        # Make downstream pipeline logic see that this round cannot continue in
        # the same browser. This prevents recover_access() from attempting a
        # same-context login/CF recovery after Access Denied.
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
                "do_not_reload": True,
                "new_proxy": True,
                "new_browser_profile": True,
                "reduce_parallel_pressure": True,
            },
        )
