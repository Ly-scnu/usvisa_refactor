from __future__ import annotations

from datetime import datetime
from typing import Any

from ..base import StageResult
from .events import emit, slot_patch
from .models import DateCollection, DateDecision, PostSelection, ScheduleContext, SlotCollection
from .protocol import BUSINESS_APPD, browser_fetch, endpoint_url, entry_available, hard_status, in_target_window, normalize_date_s, status_reason


async def click_date_in_page(page: Any, date_s: str) -> tuple[bool, str]:
    day = str(date_s)[-2:].lstrip("0") or str(date_s)[-2:]
    iso = normalize_date_s(date_s)
    selectors = [
        f"[data-date='{iso}']",
        f"[aria-label*='{iso}']",
        f"[title*='{iso}']",
        f"button:has-text('{iso}')",
        f"td:has-text('{day}')",
        f"button:has-text('{day}')",
        f"a:has-text('{day}')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                await page.wait_for_timeout(350)
                return True, sel
        except Exception:
            pass
    try:
        clicked = await page.evaluate(
            """
            ({iso, day}) => {
              const norm = (s) => String(s || '').trim();
              const nodes = Array.from(document.querySelectorAll('[data-date],[aria-label],[title],button,a,td,span,div'));
              const el = nodes.find(n => {
                const blob = [n.getAttribute('data-date'), n.getAttribute('aria-label'), n.getAttribute('title'), n.innerText, n.textContent].map(norm).join(' ');
                if (blob.includes(iso)) return true;
                if (/^\\d{1,2}$/.test(blob) && blob === String(parseInt(day, 10))) return true;
                return false;
              });
              if (!el) return '';
              el.scrollIntoView({block:'center', inline:'center'});
              el.click();
              return (el.getAttribute('data-date') || el.getAttribute('aria-label') || el.innerText || el.textContent || el.tagName || '').slice(0,80);
            }
            """,
            {"iso": iso, "day": day},
        )
        if clicked:
            await page.wait_for_timeout(350)
            return True, f"dom:{clicked}"
    except Exception:
        pass
    return False, "not_clicked"


async def collect_slots_for_accepted_date(ctx: Any, schedule: ScheduleContext, post: PostSelection, dates: DateCollection, decision: DateDecision, steps: list[dict[str, Any]]) -> tuple[StageResult | None, SlotCollection | None]:
    page = ctx.page
    cfg = ctx.runtime_config
    selected_date = decision.selected_date
    if not selected_date:
        return None, SlotCollection(token_for_submit=dates.token)
    emit(ctx, "business_slot_collecting", {"selected_date": selected_date, "note": "日期命中目标后不再点页面日期，直接协议查询时间段"})
    clicked = False
    click_method = "protocol_only_entries"

    token_for_submit = dates.token
    raw_date = next((d for d in dates.days if normalize_date_s(d) == selected_date), selected_date)
    ep = {
        "primaryId": None,
        "applications": None,
        "scheduleDayId": None,
        "scheduleEntryId": "",
        "postId": None,
        "Token": dates.token,
        "Date": raw_date if "T" in str(raw_date) else f"{selected_date}T00:00:00.000Z",
        "isReschedule": str(cfg.target.is_reschedule).lower(),
    }
    rec, parsed = await browser_fetch(page, "POST", endpoint_url(cfg, "/api/v1/schedule-group/get-family-consular-schedule-entries", appd=BUSINESS_APPD), ep, referrer=schedule.referrer)
    entries: list[dict[str, Any]] = []
    if isinstance(parsed, dict):
        token_for_submit = str(parsed.get("Token") or token_for_submit)
        for e in (parsed.get("ScheduleEntries") or []):
            if isinstance(e, dict):
                row = {
                    "date": selected_date,
                    "date_raw": raw_date,
                    "time": e.get("Time") or e.get("time"),
                    "num": e.get("Num"),
                    "entries_available": e.get("EntriesAvailable"),
                    "id": e.get("ID") or e.get("Id") or e.get("id"),
                    "raw": e,
                }
                entries.append(row)
    steps.append({"name": "schedule_entries", "date": selected_date, "status": rec.get("status"), "entry_count": len(entries), "date_clicked": clicked, "click_method": click_method, "submit_path": "protocol_fetch"})
    if hard_status(rec) >= 400:
        reason = status_reason(rec)
        needs = "rate_limit_429" if hard_status(rec) == 429 else reason
        return StageResult(False, "business_query", f"schedule entries failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": needs}, retryable=True), None
    matched = [s for s in entries if in_target_window(s.get("date"), cfg) and entry_available(s)]
    matched = sorted(matched, key=lambda x: (x.get("date") or "", x.get("time") or ""))
    selected = matched[0] if matched else None
    emit(ctx, "business_entries_collected", {"selected_date": selected_date, "date_clicked": clicked, "click_method": click_method, "entries_count": len(entries), "matched_count": len(matched), "matched": [{k: v for k, v in x.items() if k != "raw"} for x in matched[:20]]})
    slot_patch(ctx, reason="entries_collected", reason_zh=f"命中日期 {selected_date}，已查询时间段 {len(entries)} 个", availability_slots=len(entries), matched_slots=len(matched), target_hit=bool(matched))
    collection = SlotCollection(slots=entries, matched_slots=matched, entries_by_date={selected_date: entries}, token_for_submit=token_for_submit, selected=selected)
    if selected:
        ctx.booking_signal = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "slot_id": ctx.slot_id,
            "round_id": ctx.round_id,
            "primary_id": schedule.app_id,
            "applications": schedule.applications,
            "post_id": post.post_id,
            "post_name": post.post_name,
            "selected": selected,
            "token": token_for_submit,
            "submit_route": "/api/v1/schedule-group/reschedule-consular-appointments-for-family" if cfg.target.is_reschedule else "/api/v1/schedule-group/schedule-consular-appointments-for-family",
            "is_reschedule": str(cfg.target.is_reschedule).lower(),
        }
        emit(ctx, "business_booking_signal_ready", {"selected": {k: v for k, v in selected.items() if k != "raw"}, "token": "<redacted>", "post_id": post.post_id, "post_name": post.post_name})
    return None, collection
