from __future__ import annotations

import re
from typing import Any

from .access_denied import AccessDeniedResetRecovery
from .callback_not_found import CallbackNotFoundRecovery
from .components import (
    Ban1015Recovery,
    BaseRecoveryComponent,
    CfChallengeRecovery,
    NetworkErrorRecovery,
    RateLimit429Recovery,
    RecoveryResult,
)


COMPONENTS: dict[str, BaseRecoveryComponent] = {
    "ban_1015": Ban1015Recovery(),
    "rate_limit_1015": Ban1015Recovery(),
    "rate_limit_429": RateLimit429Recovery(),
    "access_denied": AccessDeniedResetRecovery(),
    "network_error": NetworkErrorRecovery(),
    "cf_challenge": CfChallengeRecovery(),
    "callback_not_found": CallbackNotFoundRecovery(),
    "page_not_found": CallbackNotFoundRecovery(),
}


def classify_stage_failure(stage_result: Any) -> str:
    payload = getattr(stage_result, "payload", None) or {}
    reason = str(payload.get("reason") or payload.get("needs_recover") or getattr(stage_result, "message", "") or "").lower()
    state = payload.get("state") or {}
    state_stage = str(state.get("stage") if isinstance(state, dict) else "").lower()
    message = str(getattr(stage_result, "message", "") or "").lower()
    if state_stage in {"rate_limit_1015", "ban_1015"} or reason in {"rate_limit_1015", "ban_1015"}:
        return "ban_1015"
    if state_stage == "access_denied" or reason == "access_denied":
        return "access_denied"
    if state_stage == "cf_challenge" or reason == "cf_challenge":
        return "cf_challenge"
    if state_stage == "network_error" or reason == "network_error":
        return "network_error"
    if state_stage in {"callback_not_found", "page_not_found"} or reason in {"callback_not_found", "page_not_found"}:
        return "callback_not_found"
    if reason in {"rate_limit_429", "rate_limited"} or str(payload.get("status") or payload.get("http_status") or "") == "429":
        return "rate_limit_429"
    direct_text = " ".join([reason, state_stage, message, str(payload.get("official_error") or "").lower()])
    if re.search(r"\berror\s*1015\b|\byou are being rate limited\b|\brate_limit_1015\b|\bban_1015\b", direct_text):
        return "ban_1015"
    if re.search(r"\bhttp\s*429\b|\btoo many requests\b|\brate_limit_429\b", direct_text):
        return "rate_limit_429"
    text = direct_text
    if "access_denied" in text or "access denied" in text or "you have been blocked" in text:
        return "access_denied"
    if "network_error" in text or "target navigation network error" in text or "err_socks_connection_failed" in text or "err_proxy" in text:
        return "network_error"
    if "page not found" in text or "callback_not_found" in text or "page_not_found" in text:
        return "callback_not_found"
    if "cf_challenge" in text or "cloudflare" in text or "challenge" in text:
        return "cf_challenge"
    return ""


def get_recovery(error_type: str) -> BaseRecoveryComponent | None:
    return COMPONENTS.get(error_type)


async def attempt_recovery(ctx: Any, stage_result: Any) -> RecoveryResult | None:
    error_type = classify_stage_failure(stage_result)
    if not error_type:
        return None
    component = get_recovery(error_type)
    if not component:
        return None
    return await component.attempt_recovery(ctx, {"stage_result": getattr(stage_result, "__dict__", str(stage_result))})


