from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

StageResult = import_module("05_stage_components.base").StageResult
save_screenshot = import_module("04_snapshot_system.manager").save_screenshot
classify_page = import_module("03_browser_management.page_classifier").classify_page


@dataclass
class RecoveryResult:
    ok: bool
    error_type: str
    action: str
    message: str
    retryable: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)
    next_strategy: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_type": self.error_type,
            "action": self.action,
            "message": self.message,
            "retryable": self.retryable,
            "evidence": self.evidence,
            "next_strategy": self.next_strategy,
        }


class BaseRecoveryComponent:
    error_type = "unknown"
    display_name = "Generic recovery"

    async def capture_evidence(self, ctx: Any, label: str) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        page = getattr(ctx, "page", None)
        root = getattr(ctx, "project_root", None)
        if not page or not root:
            return evidence
        try:
            state = await classify_page(page)
            evidence["state"] = state.__dict__
            evidence["official_error"] = official_error_from_state(state)
        except Exception as exc:
            evidence["state_error"] = repr(exc)
        try:
            shot = await save_screenshot(page, Path(root), ctx.slot_id, ctx.round_id, f"recovery_{label}")
            if shot:
                evidence["screenshot"] = str(shot).replace(str(root) + "\\", "").replace("\\", "/")
        except Exception as exc:
            evidence["screenshot_error"] = repr(exc)
        return evidence

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, self.error_type)
        return RecoveryResult(False, self.error_type, "record_only", "no concrete recovery component", evidence=evidence)


async def cooldown_refresh(ctx: Any, evidence: dict[str, Any], *, cooldown: int, refresh_attempts: int, bad_stages: set[str]) -> bool:
    page = getattr(ctx, "page", None)
    if not page:
        evidence["cooldown_refresh_error"] = "no_page"
        return False
    if refresh_attempts <= 0:
        evidence.setdefault("cooldown_refresh", []).append({"attempt": 0, "wait_seconds": cooldown, "action": "sleep_only_no_reload"})
        try:
            await page.wait_for_timeout(cooldown * 1000)
        except Exception:
            await asyncio.sleep(cooldown)
        try:
            state = await classify_page(page)
            evidence.setdefault("after_cooldown_states", []).append(state.__dict__)
            return state.stage not in bad_stages
        except Exception as exc:
            evidence.setdefault("cooldown_refresh_errors", []).append({"attempt": 0, "error": repr(exc)})
            return False
    for attempt in range(1, refresh_attempts + 1):
        try:
            evidence.setdefault("cooldown_refresh", []).append({"attempt": attempt, "wait_seconds": cooldown, "action": "sleep_then_reload"})
            try:
                await page.wait_for_timeout(cooldown * 1000)
            except Exception:
                await asyncio.sleep(cooldown)
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_timeout(2500)
            except Exception:
                pass
            state = await classify_page(page)
            evidence.setdefault("after_refresh_states", []).append(state.__dict__)
            if state.stage not in bad_stages:
                return True
        except Exception as exc:
            evidence.setdefault("cooldown_refresh_errors", []).append({"attempt": attempt, "error": repr(exc)})
    return False


def _cooldown_cfg(ctx: Any) -> tuple[int, int]:
    producer = getattr(getattr(ctx, "runtime_config", None), "producer", None)
    cooldown = max(1, int(getattr(producer, "rate_limit_cooldown_seconds", 60) or 60))
    raw_refresh = getattr(producer, "rate_limit_refresh_attempts", 1)
    refresh_attempts = max(0, int(raw_refresh if raw_refresh is not None else 1))
    return cooldown, refresh_attempts


class Ban1015Recovery(BaseRecoveryComponent):
    error_type = "ban_1015"
    display_name = "Cloudflare 1015 rate-limit/temporary ban recovery"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "ban_1015")
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="ban_1015_immediate_recycle",
                last_reason_zh="Cloudflare 1015：立即保存证据并回收当前代理/浏览器画像，不在原页等待或刷新",
                recovery_error_type="ban_1015",
                recovery_action="immediate_recycle_proxy_profile",
                recovery_component="Ban1015Recovery",
            )
        evidence["policy"] = {
            "strategy": "immediate_recycle_proxy_profile",
            "reason": "1015 is a Cloudflare rate-limit/temporary ban; same-page refresh usually burns more risk than it recovers.",
            "cooldown_seconds": 0,
            "refresh_attempts": 0,
        }
        try:
            await ctx.close()
        except Exception as exc:
            evidence["close_error"] = repr(exc)
        ctx.browser_bundle = None
        ctx.browser = None
        ctx.browser_context = None
        ctx.page = None
        ctx.proxy = None
        ctx.proxy_material = None
        return RecoveryResult(
            True,
            self.error_type,
            "recycle_proxy_profile",
            "1015 encountered: evidence saved; current context closed immediately; scheduler should start a fresh round with a new proxy/profile",
            retryable=True,
            evidence=evidence,
            next_strategy={
                "same_page_reload": False,
                "same_page_reload_attempted": False,
                "new_proxy": True,
                "new_browser_profile": True,
                "cooldown_seconds": 0,
                "reduce_parallel_pressure": True,
                "recycle_context": True,
                "restart_next_round": True,
            },
        )


class RateLimit429Recovery(BaseRecoveryComponent):
    error_type = "rate_limit_429"
    display_name = "HTTP 429 API throttling recovery"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "rate_limit_429")
        count = int(getattr(ctx, "rate_limit_429_count", 0) or 0) + 1
        setattr(ctx, "rate_limit_429_count", count)
        producer = getattr(getattr(ctx, "runtime_config", None), "producer", None)
        configured_ladder = list(getattr(producer, "rate_limit_429_cooldowns_seconds", []) or [])
        ladder = [int(x) for x in configured_ladder if int(x) > 0] or [60, 120, 180]
        cooldown = ladder[min(count - 1, len(ladder) - 1)]
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="rate_limit_429_short_retreat",
                last_reason_zh=f"HTTP 429 限流：第 {count} 次，退回首页/预约入口并短冷却 {cooldown} 秒，暂不杀票",
                recovery_error_type="rate_limit_429",
                recovery_action="short_retreat_keep_session",
                recovery_component="RateLimit429Recovery",
            )
        page = getattr(ctx, "page", None)
        evidence["cooldown"] = {"count": count, "wait_seconds": cooldown, "action": "goto_home_then_short_cooldown_keep_session"}
        try:
            if page:
                # Leave the error document / custom-actions page first.  The
                # site's own JS does document.write() on 429; sitting there and
                # re-hitting APIs is noisier than retreating to the portal top.
                try:
                    runtime_config = getattr(ctx, "runtime_config", None)
                    lang = getattr(getattr(runtime_config, "target", None), "lang", "zh-CN")
                    await page.goto(f"https://www.usvisascheduling.com/{lang}/", wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(800)
                    evidence["pre_cooldown_action"] = "goto_home"
                except Exception as exc:
                    evidence["pre_cooldown_home_error"] = repr(exc)
                await page.wait_for_timeout(cooldown * 1000)
                try:
                    evidence["after_cooldown_state"] = (await classify_page(page)).__dict__
                except Exception as exc:
                    evidence["after_cooldown_state_error"] = repr(exc)
            else:
                await asyncio.sleep(cooldown)
        except Exception as exc:
            evidence["cooldown_error"] = repr(exc)
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="rate_limit_429_short_retreat_done",
                last_reason_zh="429 短冷却完成：保留当前浏览器会话，交给业务 live loop 继续",
                recovery_action="short_retreat_done_keep_session",
                recovery_component="RateLimit429Recovery",
            )
        return RecoveryResult(
            True,
            self.error_type,
            "short_retreat_keep_session",
            "429 encountered: returned to portal/home and short cooldown completed; keep current browser session",
            retryable=True,
            evidence=evidence,
            next_strategy={"cooldown_seconds": cooldown, "same_session": True, "reduce_query_frequency": True, "retreat_to_home": True},
        )


class AccessDeniedRecovery(BaseRecoveryComponent):
    error_type = "access_denied"
    display_name = "Cloudflare access denied/block recovery"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "access_denied")
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="access_denied_recycle_proxy",
                last_reason_zh="Cloudflare 访问被拒绝：保存证据并回收当前代理/浏览器画像",
                recovery_error_type="access_denied",
                recovery_action="recycle_proxy_profile",
                recovery_component="AccessDeniedRecovery",
            )
        try:
            await ctx.close()
        except Exception as exc:
            evidence["close_error"] = repr(exc)
        ctx.browser_bundle = None
        ctx.browser = None
        ctx.browser_context = None
        ctx.page = None
        ctx.proxy = None
        ctx.proxy_material = None
        return RecoveryResult(
            True,
            self.error_type,
            "recycle_proxy_profile",
            "Cloudflare access denied/block encountered: evidence saved; current context closed; scheduler should start a fresh round",
            retryable=True,
            evidence=evidence,
            next_strategy={"new_proxy": True, "new_browser_profile": True, "reduce_parallel_pressure": True},
        )


class NetworkErrorRecovery(BaseRecoveryComponent):
    error_type = "network_error"
    display_name = "Proxy/browser network error recovery"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "network_error")
        producer = getattr(getattr(ctx, "runtime_config", None), "producer", None)
        cooldown = max(0, int(getattr(producer, "network_error_cooldown_seconds", 0) or 0))
        action = "recycle_proxy_profile" if cooldown <= 0 else "cooldown_then_recycle_proxy_profile"
        msg = "代理/网络连接失败：立即回收当前代理/画像并开启新一轮" if cooldown <= 0 else f"代理/网络连接失败：不直连官网，冷却 {cooldown}s 后回收代理/画像"
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="network_error_recycle_proxy",
                last_reason_zh=msg,
                recovery_error_type="network_error",
                recovery_action=action,
                recovery_component="NetworkErrorRecovery",
            )
        if cooldown > 0:
            page = getattr(ctx, "page", None)
            try:
                if page:
                    await page.wait_for_timeout(cooldown * 1000)
                else:
                    await asyncio.sleep(cooldown)
            except Exception:
                await asyncio.sleep(cooldown)
        try:
            await ctx.close()
        except Exception as exc:
            evidence["close_error"] = repr(exc)
        ctx.browser_bundle = None
        ctx.browser = None
        ctx.browser_context = None
        ctx.page = None
        ctx.proxy = None
        ctx.proxy_material = None
        return RecoveryResult(
            True,
            self.error_type,
            action,
            "Network/proxy error encountered: current context closed immediately; scheduler should start a fresh proxy/profile" if cooldown <= 0 else "Network/proxy error encountered: do not fallback to direct; cooldown completed and context closed",
            retryable=True,
            evidence=evidence,
            next_strategy={"new_proxy": True, "new_browser_profile": True, "direct_fallback": False, "cooldown_seconds": cooldown},
        )


class CfChallengeRecovery(BaseRecoveryComponent):
    error_type = "cf_challenge"
    display_name = "Cloudflare managed challenge recovery"

    async def attempt_recovery(self, ctx: Any, error: dict[str, Any]) -> RecoveryResult:
        evidence = await self.capture_evidence(ctx, "cf_challenge")
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="cf_challenge_recover",
                last_reason_zh="CF 人机/挑战：交给 CF Gate 组件按 interactiveBegin + CDP 点击恢复",
                recovery_error_type="cf_challenge",
                recovery_action="rerun_cf_gate",
                recovery_component="CfChallengeRecovery",
            )
        return RecoveryResult(
            True,
            self.error_type,
            "rerun_cf_gate",
            "Cloudflare challenge detected: rerun CF gate component",
            retryable=True,
            evidence=evidence,
            next_strategy={"rerun_stage": "cf_gate", "same_session": True},
        )


def official_error_from_state(state: Any) -> dict[str, Any]:
    text = f"{getattr(state, 'title', '')} {getattr(state, 'reason', '')} {getattr(state, 'url', '')}".lower()
    if getattr(state, "stage", "") == "rate_limit_1015" or "1015" in text:
        return {
            "code": "1015",
            "name": "Cloudflare Error 1015",
            "headline": "You are being rate limited",
            "meaning": "官方反馈：访问频率/代理画像触发临时限流或封禁。",
            "recommended_action": "立即保存证据并回收代理/浏览器画像；不要在原 1015 页面等待、刷新或继续打 API。",
        }
    if "429" in text:
        return {
            "code": "429",
            "name": "HTTP 429",
            "headline": "Too Many Requests",
            "meaning": "官方反馈：接口请求频率过高。",
            "recommended_action": "退回首页/预约入口并短冷却；不要在 custom-actions 错误页连续打 API，多次仍 429 再回收会话。",
        }
    if getattr(state, "stage", "") == "cf_challenge":
        return {
            "code": "cf_challenge",
            "name": "Cloudflare Challenge",
            "headline": "Verify you are human / managed challenge",
            "meaning": "官方反馈：需要通过 Cloudflare 人机/托管挑战。",
            "recommended_action": "等待 interactiveBegin 后由 CF Gate 组件 CDP 点击。",
        }
    if getattr(state, "stage", "") == "access_denied":
        return {
            "code": "access_denied",
            "name": "Cloudflare Access Denied",
            "headline": "Access denied / Sorry, you have been blocked",
            "meaning": "官方反馈：当前访问画像或代理被 Cloudflare 阻止。",
            "recommended_action": "立即保存证据，关闭当前浏览器上下文，回收代理/画像，下一轮重新开始；不要在原页刷新或继续打 API。",
        }
    if getattr(state, "stage", "") in {"callback_not_found", "page_not_found"}:
        return {
            "code": getattr(state, "stage", ""),
            "name": "Portal Page Not Found / B2C callback not found",
            "headline": "Page Not Found · Customer Self-Service",
            "meaning": "官方反馈：当前登录回跳/门户路径已经落到 404 页面，不是正常登录回跳等待。",
            "recommended_action": "保存证据并立即回收当前浏览器上下文/画像/代理；不要把它当 idp_loading 继续等待。",
        }
    return {}
