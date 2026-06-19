from __future__ import annotations

import asyncio
from importlib import import_module
from pathlib import Path
from typing import Any

from .base import StageResult

SessionContext = import_module("02_session_context.context").SessionContext
BrowserLaunchOptions = import_module("03_browser_management.options").BrowserLaunchOptions
launch_cloak_browser = import_module("03_browser_management.launcher").launch_cloak_browser
classify_page = import_module("03_browser_management.page_classifier").classify_page
save_screenshot = import_module("04_snapshot_system.manager").save_screenshot
save_live_screenshot = import_module("04_snapshot_system.manager").save_live_screenshot
Event = import_module("00_infrastructure.events.event_bus").Event
iso_now = import_module("00_infrastructure.utils.time").iso_now
ProxyAcquireStage = import_module("05_stage_components.stage00_proxy.executor").ProxyAcquireStage
CfGateStage = import_module("05_stage_components.stage01_cf_gate.executor").CfGateStage
WaitingRoomStage = import_module("05_stage_components.stage02_waiting_room.executor").WaitingRoomStage
LoginStage = import_module("05_stage_components.stage03_login.executor").LoginStage
BusinessQueryStage = import_module("05_stage_components.stage04_query.query").BusinessQueryStage
BookingSubmitStage = import_module("05_stage_components.stage05_booking.submitter").BookingSubmitStage
attempt_error_recovery = import_module("99_error_recovery.registry").attempt_recovery
record_route_stage_result = import_module("00_infrastructure.orchestration.route_health.event_recorder").record_stage_result


STAGE_ZH = {
    "proxy_acquire": "代理已获取",
    "browser_launch": "CloakBrowser 已启动",
    "cf_gate": "CF/人机处理",
    "waiting_room": "等待室处理",
    "login": "登录/密保",
    "business_query": "查日期/时间段",
    "booking_submit": "命中后提交",
    "recover_access": "访问恢复",
    "round_done": "本轮完成",
    "round_recycle": "本轮回收",
}


LIVE_PAGE_STAGE_ZH = {
    "cf_challenge": "人机验证/CF",
    "waiting_room": "等待室",
    "login": "登录页",
    "security_questions": "密保页",
    "idp_loading": "登录回跳中",
    "home": "首页",
    "schedule": "预约查询页",
    "rate_limit_1015": "1015 限流",
    "rate_limit_429": "429 限流",
    "access_denied": "拉黑/拒绝访问",
    "network_error": "网络/代理错误",
    "login_failed": "登录失败页",
    "callback_not_found": "登录回跳404",
    "page_not_found": "页面404",
    "blank": "空白页",
    "site": "官网页面",
    "unknown": "未知页面",
}


def live_page_stage_zh(stage: str) -> str:
    return LIVE_PAGE_STAGE_ZH.get(str(stage or ""), str(stage or "未知页面"))


class OneDragonPipeline:
    def __init__(self, *, limiter: Any | None = None):
        self.limiter = limiter

    def publish(self, ctx: Any, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if ctx.event_bus:
            ctx.event_bus.publish(Event(event_type, slot_id=ctx.slot_id, round_id=ctx.round_id, payload=payload or {}))

    def update(self, ctx: Any, stage: str, **patch: Any) -> None:
        if "elapsed_s" in patch and "stage_elapsed_s" not in patch:
            patch["stage_elapsed_s"] = patch.get("elapsed_s")
        if hasattr(ctx, "round_elapsed_s"):
            patch["elapsed_s"] = ctx.round_elapsed_s()
        if getattr(ctx, "round_started_at", None):
            patch.setdefault("round_started_at", ctx.round_started_at)
        if ctx.store:
            ctx.store.update_slot(ctx.slot_id, state="running", stage=stage, stage_zh=STAGE_ZH.get(stage, stage), **patch)

    def _rel(self, ctx: Any, path: str) -> str:
        try:
            return str(path).replace(str(ctx.project_root) + "\\", "").replace("\\", "/")
        except Exception:
            return str(path).replace("\\", "/")

    def _drain_requested(self, ctx: Any) -> bool:
        try:
            store = getattr(ctx, "store", None)
            if not store:
                return False
            slot = (store.read_slots() or {}).get(getattr(ctx, "slot_id", ""), {}) or {}
            return bool(slot.get("drain_requested"))
        except Exception:
            return False

    def _drain_stage_result(self, ctx: Any, stage_name: str) -> StageResult:
        try:
            if getattr(ctx, "store", None):
                ctx.store.update_slot(
                    ctx.slot_id,
                    last_reason="drain_requested",
                    last_reason_zh=f"智能排水：进入 {STAGE_ZH.get(stage_name, stage_name)} 前收到排水请求，本轮安全结束",
                )
        except Exception:
            pass
        return StageResult(
            False,
            stage_name,
            "stage skipped by drain request",
            {"needs_recover": "drain_requested", "reason": "drain_requested", "safe_stop": True, "where": f"before_{stage_name}"},
            retryable=False,
        )

    def _requires_fresh_round(self, result: StageResult, ctx: Any) -> bool:
        """True when a recovery component already closed/recycled this context.

        Access Denied/1020 is not recoverable by calling recover_access() in the
        same browser.  The recovery component saves evidence and closes the
        context; this guard makes execute() return the original failed stage so
        the scheduler immediately starts a fresh round instead of overwriting
        the useful recovery payload with "browser page is closed".
        """
        payload = result.payload or {}
        needs = str(payload.get("needs_recover") or payload.get("reason") or "")
        recovery = payload.get("recovery") if isinstance(payload, dict) else None
        strategy = recovery.get("next_strategy") if isinstance(recovery, dict) else {}
        action = str(recovery.get("action") if isinstance(recovery, dict) else "")
        return (
            needs in {"drain_requested", "login_timeout"}
            or bool(payload.get("fresh_round"))
            or bool(payload.get("safe_stop"))
            or bool(payload.get("restart_next_round"))
            or getattr(ctx, "page", None) is None
            or bool(strategy.get("recycle_context"))
            or bool(strategy.get("restart_next_round"))
            or action in {"reset_proxy_profile_restart_round", "recycle_proxy_profile", "cooldown_then_recycle_proxy_profile"}
        )

    async def capture_live(self, ctx: Any, stage: str, reason: str) -> str:
        if getattr(ctx, "page", None) is None or not getattr(ctx, "project_root", None):
            return ""
        shot = await save_live_screenshot(ctx.page, ctx.project_root, ctx.slot_id, ctx.round_id, stage)
        if not shot:
            return ""
        rel = self._rel(ctx, shot)
        page_state_payload = await self.update_live_page_state(ctx, stage=stage, source=f"snapshot_{reason}")
        if ctx.store:
            live_patch = {
                "live_snapshot": rel,
                "live_snapshot_stage": stage,
                "live_snapshot_reason": reason,
                "round_started_at": getattr(ctx, "round_started_at", ""),
            }
            if page_state_payload:
                live_patch.update({
                    "live_snapshot_page_stage": page_state_payload.get("live_page_stage", ""),
                    "live_snapshot_page_stage_zh": page_state_payload.get("live_page_stage_zh", ""),
                    "live_snapshot_page_reason": page_state_payload.get("live_page_reason", ""),
                    "live_snapshot_observed_at": page_state_payload.get("live_page_observed_at", ""),
                    "live_snapshot_source": f"snapshot_{reason}",
                })
            live_patch.update(page_state_payload)
            if hasattr(ctx, "round_elapsed_s"):
                live_patch["elapsed_s"] = ctx.round_elapsed_s()
            ctx.store.update_slot(ctx.slot_id, **live_patch)
        self.publish(ctx, "live_snapshot", {"stage": stage, "reason": reason, "screenshot": rel, **page_state_payload})
        return rel

    async def update_live_page_state(self, ctx: Any, *, stage: str, source: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if getattr(ctx, "page", None) is None:
            return payload
        try:
            page_state = await classify_page(ctx.page)
            payload = {
                "live_page_stage": page_state.stage,
                "live_page_stage_zh": live_page_stage_zh(page_state.stage),
                "live_page_reason": page_state.reason,
                "live_page_url": page_state.url,
                "live_page_title": page_state.title,
                "live_page_observed_at": iso_now(),
                "live_page_source": source,
            }
            if ctx.store:
                patch = dict(payload)
                if hasattr(ctx, "round_elapsed_s"):
                    patch["elapsed_s"] = ctx.round_elapsed_s()
                ctx.store.update_slot(ctx.slot_id, **patch)
        except Exception as exc:
            payload = {"live_page_classify_error": repr(exc), "live_page_source": source}
            try:
                if ctx.store:
                    ctx.store.update_slot(ctx.slot_id, **payload)
            except Exception:
                pass
        return payload

    async def _live_snapshot_loop(self, ctx: Any, stage: str, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=10.0)
                return
            except asyncio.TimeoutError:
                try:
                    await self.capture_live(ctx, stage, "auto_10s")
                except Exception as exc:
                    self.publish(ctx, "live_snapshot_failed", {"stage": stage, "reason": "auto_10s", "error": repr(exc)})

    async def _live_page_state_loop(self, ctx: Any, stage: str, stop: asyncio.Event) -> None:
        last_page_key = ""
        last_change_snapshot_at = 0.0
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.5)
                return
            except asyncio.TimeoutError:
                payload = await self.update_live_page_state(ctx, stage=stage, source="poll_2_5s")
                page_key = "|".join([str(payload.get("live_page_stage", "")), str(payload.get("live_page_reason", ""))]) if payload else ""
                now_mono = asyncio.get_running_loop().time()
                # The text badge can change faster than the 10s live snapshot loop.
                # Capture immediately on real page-state transitions so the one
                # overwritten live image stays aligned with the displayed state.
                if page_key and page_key != last_page_key and now_mono - last_change_snapshot_at >= 2.0:
                    last_page_key = page_key
                    last_change_snapshot_at = now_mono
                    try:
                        await self.capture_live(ctx, stage, "page_state_change")
                    except Exception as exc:
                        self.publish(ctx, "live_snapshot_failed", {"stage": stage, "reason": "page_state_change", "error": repr(exc)})
            except Exception:
                return

    async def run_stage(self, ctx: Any, stage: Any) -> StageResult:
        if self._drain_requested(ctx) and stage.stage_name not in {"business_query", "booking_submit"}:
            return self._drain_stage_result(ctx, stage.stage_name)
        # Clear stale result text when entering a new real stage.  Otherwise
        # the dashboard can show e.g. "cf gate timeout" while the current page
        # is already in waiting_room, which looks like CF was not released.
        self.update(
            ctx,
            stage.stage_name,
            last_reason="",
            last_reason_zh="",
            result_stage="",
            recovery_error_type="",
            recovery_action="",
            recovery_component="",
        )
        self.publish(ctx, "stage_enter", {"stage": stage.stage_name})
        try:
            await self.capture_live(ctx, stage.stage_name, "stage_enter")
        except Exception as exc:
            self.publish(ctx, "live_snapshot_failed", {"stage": stage.stage_name, "reason": "stage_enter", "error": repr(exc)})
        stop_live = asyncio.Event()
        live_task = asyncio.create_task(self._live_snapshot_loop(ctx, stage.stage_name, stop_live))
        page_state_task = asyncio.create_task(self._live_page_state_loop(ctx, stage.stage_name, stop_live))
        try:
            result = await stage.execute(ctx)
        finally:
            stop_live.set()
            try:
                await live_task
            except Exception:
                pass
            try:
                await page_state_task
            except Exception:
                pass
        try:
            await self.capture_live(ctx, stage.stage_name, "stage_exit")
        except Exception as exc:
            self.publish(ctx, "live_snapshot_failed", {"stage": stage.stage_name, "reason": "stage_exit", "error": repr(exc)})
        # Do not let a failed/blocked stage become a black box.  Capture the
        # last visible browser page before the pipeline closes/recycles the
        # context, and attach it to the stage_exit event for the dashboard.
        if not result.ok and getattr(ctx, "page", None) is not None and getattr(ctx, "project_root", None):
            try:
                # Failure evidence must be close to the failure moment.  A long
                # render-stability wait can catch a later automatic login/CF
                # redirect and make the screenshot look unrelated to the event.
                shot = await save_screenshot(
                    ctx.page,
                    ctx.project_root,
                    ctx.slot_id,
                    ctx.round_id,
                    f"{stage.stage_name}_final",
                    stable_ms=250,
                    max_ms=900,
                )
                if shot:
                    payload = dict(result.payload or {})
                    artifacts = dict(payload.get("artifacts") or {})
                    artifacts["final_screenshot"] = self._rel(ctx, shot)
                    try:
                        final_state = await classify_page(ctx.page)
                        artifacts["final_state"] = final_state.__dict__
                        if payload.get("needs_recover") in {"navigation_not_found", "home", None, ""} and final_state.stage in {"login", "cf_challenge", "security_questions", "idp_loading", "account_login_blocked"}:
                            payload["needs_recover"] = "cf_challenge" if final_state.stage in {"cf_challenge", "idp_loading"} else final_state.stage
                            payload["state"] = final_state.__dict__
                            result.message = f"business redirected to {payload['needs_recover']}"
                    except Exception:
                        pass
                    payload["artifacts"] = artifacts
                    result.payload = payload
                    self.publish(ctx, "stage_final_snapshot", {"stage": stage.stage_name, "screenshot": artifacts.get("final_screenshot")})
            except Exception as exc:
                self.publish(ctx, "stage_final_snapshot_failed", {"stage": stage.stage_name, "error": repr(exc)})
        skip_recovery = False
        try:
            payload = result.payload or {}
            needs = str(payload.get("needs_recover") or payload.get("reason") or "")
            skip_recovery = needs in {"drain_requested", "login_timeout"} or bool(payload.get("fresh_round")) or bool(payload.get("safe_stop"))
        except Exception:
            skip_recovery = False
        if not result.ok and not skip_recovery:
            try:
                recovery = await attempt_error_recovery(ctx, result)
                if recovery:
                    payload = dict(result.payload or {})
                    payload["recovery"] = recovery.as_dict()
                    result.payload = payload
                    self.publish(ctx, "recovery_attempt", {"stage": stage.stage_name, **recovery.as_dict()})
            except Exception as exc:
                self.publish(ctx, "recovery_attempt_failed", {"stage": stage.stage_name, "error": repr(exc)})
        route_health = None
        try:
            route_health = record_route_stage_result(getattr(ctx, "store", None), getattr(ctx, "proxy_material", None), stage.stage_name, result)
        except Exception as exc:
            route_health = {"error": repr(exc)}
        exit_payload = {"stage": stage.stage_name, "ok": result.ok, "message": result.message, "payload": result.payload}
        if route_health:
            exit_payload["route_health"] = route_health
        self.publish(ctx, "stage_exit", exit_payload)
        if route_health and not route_health.get("error"):
            self.publish(ctx, "route_health_update", {"stage": stage.stage_name, **route_health})
        return result


    async def recover_stage_once(self, ctx: Any, failed_stage: Any, needs: str) -> StageResult:
        """Recover the current page and hand control back to the failed stage.

        Important for login/B2C callback: Cloudflare can be injected after
        credentials/KBA submission on `signin-aad-b2c_1`.  The generic
        `recover_access()` used to run LoginStage inside recovery; if CF
        appeared a second time, the round ended before the outer pipeline could
        retry cleanly.  This helper keeps recovery as a transition only:
        clear CF / waiting room / access blocker, then let the original stage
        rerun with a small bounded retry budget.
        """
        if str(needs or "") == "cf_challenge":
            return await self.run_stage(ctx, CfGateStage())
        if str(needs or "") == "waiting_room":
            return await self.run_stage(ctx, WaitingRoomStage(self.limiter))
        return await self.recover_access(ctx)

    async def retry_stage_after_recovery(self, ctx: Any, stage: Any, result: StageResult, *, max_retries: int = 2) -> StageResult:
        """Bounded same-context recovery/retry for a stage result.

        Returns the first successful stage result, or the last failed result.
        """
        current = result
        for retry_no in range(1, max(1, int(max_retries)) + 1):
            if current.ok or not current.retryable:
                return current
            if self._requires_fresh_round(current, ctx):
                return current
            needs = (current.payload or {}).get("needs_recover")
            if not needs:
                return current
            self.publish(ctx, "stage_recovery_retry", {
                "stage": stage.stage_name,
                "retry_no": retry_no,
                "needs_recover": needs,
                "message": current.message,
            })
            rr = await self.recover_stage_once(ctx, stage, str(needs))
            if not rr.ok:
                return rr
            current = await self.run_stage(ctx, stage)
        return current

    async def recover_access(self, ctx: Any) -> StageResult:
        self.update(ctx, "recover_access")
        if getattr(ctx, "page", None) is None:
            return StageResult(False, "recover_access", "browser page is closed after recovery attempt", {"needs_recover": "recycle_context"}, retryable=True)

        # Recovery is a small state machine, not a single one-shot call.  The
        # site often inserts CF during B2C callback/login after a business API
        # block.  Old behavior: business_query -> login -> CF -> round_finish.
        # New behavior: business_query -> login -> CF gate -> login/home -> rerun
        # business_query, bounded by max_steps to avoid infinite loops.
        max_steps = int(getattr(ctx.runtime_config.producer, "access_recover_max_steps", 6) or 6)
        recoverable_login_needs = {"cf_challenge", "waiting_room", "login", "security_questions", "idp_loading"}
        for step_no in range(1, max(1, max_steps) + 1):
            state = await classify_page(ctx.page)
            self.publish(ctx, "access_recover_detected", {"state": state.__dict__, "step": step_no, "max_steps": max_steps})

            if state.stage == "cf_challenge":
                r = await self.run_stage(ctx, CfGateStage())
                if not r.ok:
                    return r
                continue

            if state.stage == "rate_limit_1015":
                return StageResult(False, "recover_access", "recovery requires proxy/profile recycle for 1015", {"state": state.__dict__, "needs_recover": "ban_1015", "reason": "ban_1015"}, retryable=True)

            if state.stage == "rate_limit_429":
                return StageResult(False, "recover_access", "recovery requires cooldown for 429", {"state": state.__dict__, "needs_recover": "rate_limit_429", "reason": "rate_limit_429"}, retryable=True)

            if state.stage == "account_login_blocked":
                return StageResult(False, "recover_access", "account login blocked", {"state": state.__dict__, "needs_recover": "account_login_blocked", "reason": "account_login_blocked", "safe_stop": True}, retryable=False)

            if state.stage in {"access_denied", "network_error", "blank", "login_failed"}:
                return StageResult(False, "recover_access", f"cannot recover access from {state.stage}", {"state": state.__dict__, "needs_recover": state.stage, "reason": state.stage}, retryable=True)

            if state.stage in {"callback_not_found", "page_not_found"}:
                failed = StageResult(False, "recover_access", f"cannot recover access from {state.stage}", {"state": state.__dict__, "needs_recover": state.stage, "reason": state.stage}, retryable=True)
                try:
                    recovery = await attempt_error_recovery(ctx, failed)
                    if recovery:
                        payload = dict(failed.payload or {})
                        payload["recovery"] = recovery.as_dict()
                        failed.payload = payload
                        self.publish(ctx, "recovery_attempt", {"stage": "recover_access", **recovery.as_dict()})
                except Exception as exc:
                    self.publish(ctx, "recovery_attempt_failed", {"stage": "recover_access", "error": repr(exc)})
                return failed

            if state.stage == "waiting_room":
                r = await self.run_stage(ctx, WaitingRoomStage(self.limiter))
                if not r.ok:
                    return r
                continue

            if state.stage in {"login", "security_questions", "idp_loading"}:
                r = await self.run_stage(ctx, LoginStage())
                if r.ok:
                    continue
                needs = str((r.payload or {}).get("needs_recover") or "")
                if r.retryable and needs in recoverable_login_needs and step_no < max_steps:
                    self.publish(ctx, "access_recover_retry", {
                        "step": step_no,
                        "source_stage": "login",
                        "needs_recover": needs,
                        "message": r.message,
                    })
                    continue
                return r

            return StageResult(True, "recover_access", "access recovered", {"state": state.__dict__, "steps": step_no})

        final_state = await classify_page(ctx.page)
        return StageResult(
            False,
            "recover_access",
            "access recovery max steps exceeded",
            {"state": final_state.__dict__, "needs_recover": final_state.stage, "max_steps": max_steps},
            retryable=True,
        )

    async def execute(self, ctx: Any, *, route_index: int = 0) -> StageResult:
        try:
            proxy_stage = ProxyAcquireStage(route_index=route_index)
            r = await self.run_stage(ctx, proxy_stage)
            if not r.ok:
                return r
            self.update(ctx, "browser_launch")
            opts = BrowserLaunchOptions(
                project_root=ctx.runtime_config.project_root,
                slot_id=ctx.slot_id,
                round_id=ctx.round_id,
                headless=bool(ctx.runtime_config.producer.headless),
                proxy_url=ctx.proxy,
                profile_scope=ctx.runtime_config.producer.profile_scope,
                cloak_browser_root=Path(str(getattr(ctx.runtime_config.producer, "cloak_browser_root", "") or BrowserLaunchOptions.cloak_browser_root)),
            )
            bundle = await launch_cloak_browser(opts)
            ctx.browser_bundle = bundle
            ctx.browser = bundle.browser
            ctx.browser_context = bundle.context
            ctx.page = bundle.page
            self.publish(ctx, "browser_launched", {"executable": str(bundle.executable_path), "profile": str(bundle.user_data_dir)})

            r = await self.run_stage(ctx, CfGateStage())
            allow_direct_fallback = bool(getattr(ctx.runtime_config.producer, "network_error_direct_fallback", False))
            if allow_direct_fallback and not r.ok and (r.payload or {}).get("reason") == "network_error" and ctx.proxy:
                self.publish(ctx, "proxy_bypass_after_network_error", {"proxy_display": getattr(ctx.proxy_material, "session_id", ""), "reason": r.message})
                try:
                    await ctx.close()
                except Exception:
                    pass
                ctx.proxy = None
                ctx.proxy_material = None
                self.update(ctx, "browser_launch", last_reason="proxy_network_error_fallback_direct")
                opts = BrowserLaunchOptions(
                    project_root=ctx.runtime_config.project_root,
                    slot_id=ctx.slot_id,
                    round_id=ctx.round_id + "_direct",
                    headless=bool(ctx.runtime_config.producer.headless),
                    proxy_url=None,
                    profile_scope=ctx.runtime_config.producer.profile_scope,
                    cloak_browser_root=Path(str(getattr(ctx.runtime_config.producer, "cloak_browser_root", "") or BrowserLaunchOptions.cloak_browser_root)),
                )
                bundle = await launch_cloak_browser(opts)
                ctx.browser_bundle = bundle
                ctx.browser = bundle.browser
                ctx.browser_context = bundle.context
                ctx.page = bundle.page
                self.publish(ctx, "browser_relaunched_direct", {"executable": str(bundle.executable_path), "profile": str(bundle.user_data_dir)})
                r = await self.run_stage(ctx, CfGateStage())
            if not r.ok:
                return r

            for stage in (WaitingRoomStage(self.limiter), LoginStage(), CfGateStage(), WaitingRoomStage(self.limiter)):
                r = await self.run_stage(ctx, stage)
                if not r.ok:
                    r = await self.retry_stage_after_recovery(ctx, stage, r, max_retries=3 if stage.stage_name == "login" else 2)
                    if r.ok:
                        continue
                    return r

            business = BusinessQueryStage()
            for attempt in range(1, 4):
                r = await self.run_stage(ctx, business)
                if r.ok:
                    break
                needs = (r.payload or {}).get("needs_recover")
                if not r.retryable or not needs:
                    return r
                if self._requires_fresh_round(r, ctx):
                    return r
                rr = await self.recover_access(ctx)
                if not rr.ok:
                    return rr
            else:
                return r

            # Persist query result even when no hit.
            if ctx.store and ctx.last_business_result is not None:
                ctx.store.write_latest_ticket(ctx.last_business_result)
                days = ctx.last_business_result.get("days") or []
                matched = ctx.last_business_result.get("matched_slots") or []
                ctx.store.write_availability_text(f"days={days}\nmatched={matched}\n")

            if ctx.booking_signal:
                if ctx.store:
                    ctx.store.write_booking_signal({**ctx.booking_signal, "token": "<redacted>"})
                br = await self.run_stage(ctx, BookingSubmitStage())
                return br
            return StageResult(True, "round_done", "query completed without target hit", {"business": ctx.last_business_result})
        finally:
            try:
                if getattr(ctx, "page", None) is not None and getattr(ctx, "project_root", None):
                    try:
                        await self.capture_live(ctx, "round_close", "before_close")
                    except Exception:
                        pass
                    shot = await save_screenshot(ctx.page, ctx.project_root, ctx.slot_id, ctx.round_id, "round_close")
                    if shot:
                        rel = str(shot).replace(str(ctx.project_root) + "\\", "").replace("\\", "/")
                        self.publish(ctx, "round_close_snapshot", {"screenshot": rel})
            except Exception:
                pass
            try:
                await ctx.close()
            except Exception:
                pass


async def run_one_dragon_round(
    *,
    config: Any,
    store: Any,
    event_bus: Any,
    slot_id: str,
    round_no: int,
    limiter: Any | None = None,
    route_index: int = 0,
    round_started_at: str | None = None,
    round_started_monotonic: float | None = None,
) -> StageResult:
    ctx = SessionContext(
        slot_id=slot_id,
        round_id=f"round_{round_no:04d}",
        runtime_config=config,
        account=config.accounts[0] if config.accounts else None,
        project_root=config.project_root,
        event_bus=event_bus,
        store=store,
    )
    if round_started_at:
        ctx.round_started_at = round_started_at
    if round_started_monotonic is not None:
        ctx.round_started_monotonic = round_started_monotonic
    return await OneDragonPipeline(limiter=limiter).execute(ctx, route_index=route_index)

