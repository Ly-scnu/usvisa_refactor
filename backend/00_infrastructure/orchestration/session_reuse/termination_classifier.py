from __future__ import annotations

from typing import Any

from .models import TerminationDecision


class TerminationClassifier:
    """Classify whether a live browser session should be reused or recycled.

    The policy is intentionally conservative for sessions that have already
    produced at least one successful days query: login/CF/KBA/short 429 are
    treated as recoverable, while 1015/access denied/proxy ban/crash are
    terminal and should start a fresh profile/proxy route.
    """

    TERMINAL_MARKERS = {
        "rate_limit_1015",
        "1015",
        "access_denied",
        "proxy_banned",
        "proxy_ban",
        "browser_crashed",
        "browser_closed",
        "callback_not_found",
        "page_not_found",
        "too_many_recovery_failures",
    }
    RECOVERABLE_MARKERS = {
        "cf_challenge",
        "login",
        "security_questions",
        "auth_or_cf",
        "auth_or_cf_block",
        "rate_limit_429",
        "rate_limited",
        "429",
        "navigation_not_found",
        "home",
        "waiting_room",
    }

    def classify(self, result_or_payload: Any, *, success_count: int, consecutive_bad: int, recovery_grace: int) -> TerminationDecision:
        payload = getattr(result_or_payload, "payload", result_or_payload) or {}
        if not isinstance(payload, dict):
            payload = {}
        message = str(getattr(result_or_payload, "message", "") or payload.get("message") or "")
        keys = [
            payload.get("needs_recover"),
            payload.get("error_type"),
            payload.get("result_class"),
            payload.get("kind"),
            message,
        ]
        text = " ".join(str(x or "") for x in keys).lower()
        marker = "unknown"
        for x in self.TERMINAL_MARKERS:
            if x in text:
                marker = x
                return TerminationDecision("terminal_end", marker, True, False, f"终止错误：{marker}，必须回收当前会话/代理")
        if consecutive_bad >= max(1, int(recovery_grace)):
            return TerminationDecision("terminal_end", "too_many_recovery_failures", True, False, f"连续恢复失败 {consecutive_bad} 次，回收会话")
        for x in self.RECOVERABLE_MARKERS:
            if x in text:
                marker = x
                break
        if success_count > 0:
            return TerminationDecision("cooldown_continue", marker, False, True, f"成功会话遇到 {marker}：先恢复/冷却后复用")
        if marker in {"cf_challenge", "login", "security_questions", "auth_or_cf", "auth_or_cf_block", "rate_limit_429", "rate_limited", "429"}:
            return TerminationDecision("recover", marker, False, True, f"首轮遇到 {marker}：允许恢复")
        return TerminationDecision("recover", marker, False, True, "未知非终止错误：按可恢复处理一次")
