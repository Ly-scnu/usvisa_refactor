from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from importlib import import_module

from ..base import StageResult

classify_page = import_module("03_browser_management.page_classifier").classify_page
browser_fetch = import_module("05_stage_components.stage04_query.query").browser_fetch
endpoint_url = import_module("05_stage_components.stage04_query.protocol").endpoint_url
BUSINESS_APPD = import_module("05_stage_components.stage04_query.protocol").BUSINESS_APPD
click_date_in_page = import_module("05_stage_components.stage04_query.slot_collector").click_date_in_page


def classify_submit_result(parsed: Any, rec: dict[str, Any]) -> str:
    text = str(parsed or rec.get("text") or "").lower()
    status = int(rec.get("status") or 0)
    if "error 1015" in text or "you are being rate limited" in text or "rate_limit_1015" in text:
        return "ban_1015"
    if status == 429 or "too many requests" in text or "rate_limited" in text:
        return "rate_limited"
    if "just a moment" in text or "cf_chl" in text or "challenges.cloudflare.com" in text:
        return "auth_or_cf_block"
    if "access denied" in text or "error 1020" in text:
        return "auth_or_cf_block"
    if isinstance(parsed, dict) and (parsed.get("AllScheduled") is True or parsed.get("success") is True):
        return "success"
    if isinstance(parsed, dict) and (parsed.get("HasError") is True or parsed.get("ErrorString")):
        return "business_error"
    if status in (200, 201, 204):
        return "submitted_unknown"
    if status in (401, 403):
        return "auth_or_cf_block"
    if "all scheduled" in text or "success" in text:
        return "success"
    return "failed"


class BookingSubmitStage:
    stage_name = "booking_submit"

    def _redact_signal(self, sig: dict[str, Any]) -> dict[str, Any]:
        out = dict(sig or {})
        if "token" in out:
            out["token"] = "<redacted>"
        return out

    def _persist_attempts(self, ctx: Any, payload: dict[str, Any]) -> None:
        try:
            root = Path(ctx.project_root or ctx.runtime_config.project_root)
            out_dir = root / "storage" / "booking"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "booking_attempts.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            if payload.get("ok") and ctx.runtime_config.booking.success_latch:
                (out_dir / "booking_success.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    async def _one_submit(self, ctx: Any, delay_ms: int) -> dict[str, Any]:
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        cfg = ctx.runtime_config
        sig = ctx.booking_signal or {}
        selected = sig.get("selected") or {}
        payload = {
            "primaryId": None,
            "applications": None,
            "scheduleDayId": None,
            "scheduleEntryId": "",
            "postId": None,
            "Token": sig.get("token"),
            "Date": selected.get("date_raw") if "T" in str(selected.get("date_raw") or "") else f"{selected.get('date')}T00:00:00.000Z",
            "Num": str(selected.get("num")),
        }
        if not cfg.target.is_reschedule:
            payload["isReschedule"] = "false"
        ref = f"https://www.usvisascheduling.com/{cfg.target.lang}/schedule/"
        route = sig.get("submit_route") or "/api/v1/schedule-group/schedule-consular-appointments-for-family"
        url = endpoint_url(cfg, route, appd=BUSINESS_APPD)
        rec, parsed = await browser_fetch(ctx.page, "POST", url, payload, referrer=ref)
        return {"delay_ms": delay_ms, "route": route, "payload": {**payload, "Token": "<redacted>"}, "status": rec.get("status"), "body_len": rec.get("text_len"), "parsed": parsed, "result_class": classify_submit_result(parsed, rec)}

    async def _click_time_in_page(self, page: Any, selected: dict[str, Any]) -> tuple[bool, str]:
        time_s = str(selected.get("time") or "").strip()
        num_s = str(selected.get("num") or "").strip()
        selectors: list[str] = []
        if time_s:
            selectors.extend([
                f"input[type='radio'] ~ label:has-text('{time_s}')",
                f"label:has-text('{time_s}')",
                f"button:has-text('{time_s}')",
                f"td:has-text('{time_s}')",
                f"tr:has-text('{time_s}') input[type='radio']",
                f"tr:has-text('{time_s}') button",
            ])
        if num_s:
            selectors.extend([
                f"input[value='{num_s}']",
                f"[data-num='{num_s}']",
                f"[data-value='{num_s}']",
            ])
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=2500)
                    await page.wait_for_timeout(250)
                    return True, sel
            except Exception:
                pass
        try:
            clicked = await page.evaluate(
                """
                ({time_s, num_s}) => {
                  const clean = s => String(s || '').trim();
                  const rows = Array.from(document.querySelectorAll('tr,li,div,label,button,td'));
                  const row = rows.find(n => (time_s && clean(n.innerText || n.textContent).includes(time_s)) || (num_s && String(n.getAttribute('data-num') || n.getAttribute('data-value') || n.getAttribute('value') || '') === num_s));
                  if (!row) return '';
                  const target = row.matches('input,button,a,label') ? row : row.querySelector('input[type=radio],button,a,label,[role=button]') || row;
                  target.scrollIntoView({block:'center', inline:'center'});
                  target.click();
                  return (target.innerText || target.textContent || target.getAttribute('value') || target.tagName || '').slice(0,80);
                }
                """,
                {"time_s": time_s, "num_s": num_s},
            )
            if clicked:
                await page.wait_for_timeout(250)
                return True, f"dom:{clicked}"
        except Exception:
            pass
        return False, "not_clicked"

    async def _click_submit_in_page(self, page: Any) -> tuple[bool, str]:
        selectors = [
            "button:has-text('Submit')",
            "button:has-text('Schedule')",
            "button:has-text('Confirm')",
            "button:has-text('预约')",
            "button:has-text('提交')",
            "button:has-text('确认')",
            "button:has-text('安排')",
            "input[type='submit']",
            "button[type='submit']",
            "[role='button']:has-text('Submit')",
            "[role='button']:has-text('确认')",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).last
                if await loc.count() > 0:
                    await loc.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                    return True, sel
            except Exception:
                pass
        try:
            clicked = await page.evaluate(
                """
                () => {
                  const words = ['submit','schedule','confirm','预约','提交','确认','安排'];
                  const clean = s => String(s || '').trim().toLowerCase();
                  const nodes = Array.from(document.querySelectorAll('button,input[type=submit],a,[role=button]'));
                  const el = nodes.reverse().find(n => words.some(w => clean(n.innerText || n.textContent || n.value).includes(w)));
                  if (!el) return '';
                  el.scrollIntoView({block:'center', inline:'center'});
                  el.click();
                  return (el.innerText || el.textContent || el.value || el.tagName || '').slice(0,80);
                }
                """
            )
            if clicked:
                await page.wait_for_timeout(1200)
                return True, f"dom:{clicked}"
        except Exception:
            pass
        return False, "not_clicked"

    async def _ui_submit_fallback(self, ctx: Any) -> dict[str, Any]:
        cfg = ctx.runtime_config
        sig = ctx.booking_signal or {}
        selected = sig.get("selected") or {}
        out: dict[str, Any] = {
            "result_class": "ui_fallback_failed",
            "enabled": True,
            "date": selected.get("date"),
            "time": selected.get("time"),
            "steps": [],
        }
        try:
            state = await classify_page(ctx.page)
            out["initial_state"] = getattr(state, "__dict__", {})
            if state.stage in {"rate_limit_1015", "rate_limit_429", "cf_challenge", "waiting_room", "login", "security_questions", "access_denied", "network_error", "blank"}:
                out["result_class"] = "ui_fallback_skipped_blocked_page"
                out["reason"] = state.stage
                return out
            if "schedule" not in str(state.url or "").lower() and state.stage != "schedule":
                if not bool(getattr(cfg.booking, "ui_fallback_allow_navigation", False)):
                    out["result_class"] = "ui_fallback_skipped_not_schedule_page"
                    out["reason"] = "navigation_disabled"
                    return out
                target = f"https://www.usvisascheduling.com/{cfg.target.lang}/schedule/"
                await ctx.page.goto(target, wait_until="domcontentloaded", timeout=45000)
                await ctx.page.wait_for_timeout(500)
                out["steps"].append({"goto_schedule": target})
            date_clicked, date_method = await click_date_in_page(ctx.page, selected.get("date") or selected.get("date_raw") or "")
            out["steps"].append({"date_clicked": date_clicked, "method": date_method})
            time_clicked, time_method = await self._click_time_in_page(ctx.page, selected)
            out["steps"].append({"time_clicked": time_clicked, "method": time_method})
            submit_clicked, submit_method = await self._click_submit_in_page(ctx.page)
            out["steps"].append({"submit_clicked": submit_clicked, "method": submit_method})
            final_state = await classify_page(ctx.page)
            out["final_state"] = getattr(final_state, "__dict__", {})
            blob = ""
            try:
                blob = await ctx.page.locator("body").inner_text(timeout=2000)
            except Exception:
                pass
            low = (str(final_state.url or "") + " " + str(final_state.title or "") + " " + blob[:2000]).lower()
            if "appointment-confirmation" in low or "all scheduled" in low or "预约确认" in low or "已预约" in low:
                out["result_class"] = "success"
            elif submit_clicked:
                out["result_class"] = "ui_submitted_unknown"
            return out
        except Exception as exc:
            out["error"] = repr(exc)
            return out

    async def execute(self, ctx: Any) -> StageResult:
        cfg = ctx.runtime_config
        if not ctx.booking_signal:
            return StageResult(True, self.stage_name, "no booking signal", {"attempted": False})
        if not cfg.booking.armed:
            return StageResult(True, self.stage_name, "booking dry run: armed=false", {"attempted": False, "signal": self._redact_signal(ctx.booking_signal)})
        delays = list(cfg.booking.parallel_submit_delays_ms or [0])[: max(1, int(cfg.booking.max_parallel_submit or 1))]
        tasks = [self._one_submit(ctx, int(d)) for d in delays]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        normalized = []
        for r in results:
            if isinstance(r, Exception):
                normalized.append({"error": repr(r), "result_class": "exception"})
            else:
                normalized.append(r)
        ok = any(r.get("result_class") in {"success", "submitted_unknown"} for r in normalized)
        fallback = None
        if not ok and bool(getattr(cfg.booking, "ui_fallback_enabled", True)):
            classes = {str(r.get("result_class") or "") for r in normalized}
            allowed = set(getattr(cfg.booking, "ui_fallback_on_classes", []) or ["business_error", "failed", "exception"])
            blocked = {"ban_1015", "rate_limited", "auth_or_cf_block"}
            if classes and classes.isdisjoint(blocked) and bool(classes & allowed):
                fallback = await self._ui_submit_fallback(ctx)
                ok = fallback.get("result_class") in {"success", "ui_submitted_unknown"}
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ok": ok,
            "attempted": True,
            "slot_id": ctx.slot_id,
            "round_id": ctx.round_id,
            "signal": self._redact_signal(ctx.booking_signal),
            "results": normalized,
            "ui_fallback": fallback,
        }
        self._persist_attempts(ctx, payload)
        if getattr(ctx, "store", None):
            try:
                ctx.store.write_booking_signal(payload)
                ctx.store.update_slot(
                    ctx.slot_id,
                    last_reason="booking_submitted" if ok else "booking_submit_failed",
                    last_reason_zh="抢票提交已发出" if ok else "抢票提交失败",
                    booking_attempted=True,
                    booking_ok=ok,
                )
            except Exception:
                pass
        return StageResult(ok, self.stage_name, "booking submitted" if ok else "booking submit failed", payload, retryable=not ok)

