from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FailureClassification:
    kind: str
    severity: str
    reason: str
    handoff: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailureClassifier:
    """Classify a failed business probe by concrete evidence."""

    def classify(self, result: Any) -> FailureClassification:
        payload = getattr(result, "payload", {}) or {}
        payload = payload if isinstance(payload, dict) else {}
        message = str(getattr(result, "message", "") or "")
        hay = " ".join(
            [
                message,
                str(payload.get("needs_recover") or ""),
                str(payload.get("reason") or ""),
                str(payload.get("error_type") or ""),
                str(payload.get("exception") or ""),
            ]
        ).lower()

        if "failed to fetch" in hay:
            return FailureClassification("failed_to_fetch", "fast_recycle", "浏览器同源 fetch 已失败：当前会话不应反复占业务闸门")
        if "target closed" in hay or "page closed" in hay or "browser page is closed" in hay:
            return FailureClassification("page_closed", "terminal", "浏览器页面/上下文已关闭")
        if "1015" in hay or "rate_limit_1015" in hay or "ban_1015" in hay:
            return FailureClassification("ban_1015", "terminal", "Cloudflare 1015/临时封禁")
        if "1020" in hay or "access_denied" in hay or "access denied" in hay:
            return FailureClassification("access_denied", "terminal", "Access Denied/1020")
        if "network_error" in hay or "net::" in hay or "err_tunnel" in hay or "err_proxy" in hay:
            return FailureClassification("network_error", "terminal", "网络/代理错误")
        if "blank" in hay:
            return FailureClassification("blank", "terminal", "空白页/无有效上下文")
        if "callback_not_found" in hay or "page_not_found" in hay or "page not found" in hay:
            return FailureClassification("callback_not_found", "terminal", "B2C 回跳/门户 404：当前上下文不可复用")
        if "rate_limit_429" in hay or "rate_limited" in hay or "too many requests" in hay or "http 429" in hay:
            return FailureClassification("rate_limited", "recoverable", "429/业务限流：当前会话需退避，不能连续占用业务闸门")
        if "page view blocked" in hay or "auth_or_cf" in hay or "auth_or_cf_block" in hay:
            return FailureClassification("auth_or_cf_block", "recoverable", "业务 page-view 被登录/CF 层拦截：需恢复，不可连续占用业务闸门")
        if "cf_challenge" in hay or "cloudflare" in hay or "turnstile" in hay:
            return FailureClassification("cf_challenge", "recoverable", "CF challenge，可交给 CF 组件恢复")
        if "login" in hay or "security_questions" in hay:
            return FailureClassification("login", "recoverable", "登录/密保状态，可交给登录组件恢复")
        return FailureClassification("unknown", "soft_retry", "普通失败，沿用复用策略", handoff=False)
