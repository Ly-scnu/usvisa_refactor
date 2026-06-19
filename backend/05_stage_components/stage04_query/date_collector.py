from __future__ import annotations

from typing import Any

from ..base import StageResult
from .events import emit, slot_patch
from .models import DateCollection, PostSelection, ScheduleContext
from .protocol import BUSINESS_APPD, browser_fetch, endpoint_url, hard_status, normalize_date_s, status_reason, transport_failed


async def collect_dates(ctx: Any, schedule: ScheduleContext, post: PostSelection, steps: list[dict[str, Any]]) -> tuple[StageResult | None, DateCollection | None]:
    page = ctx.page
    cfg = ctx.runtime_config
    fetch_timeout_ms = int(getattr(getattr(cfg, "producer", None), "business_fetch_timeout_ms", 15000) or 15000)
    emit(ctx, "business_dates_collecting", {"post_id": post.post_id, "post_name": post.post_name, "important": "只收集日期，不点击日期"})

    days_params = {
        "primaryId": schedule.app_id,
        "applications": schedule.applications,
        "scheduleDayId": "",
        "scheduleEntryId": "",
        "postId": post.post_id,
        "isReschedule": str(cfg.target.is_reschedule).lower(),
    }
    rec, parsed = await browser_fetch(page, "POST", endpoint_url(cfg, "/api/v1/schedule-group/get-family-consular-schedule-days", appd=BUSINESS_APPD), days_params, referrer=schedule.referrer, timeout_ms=fetch_timeout_ms)
    days: list[str] = []
    token = ""
    if isinstance(parsed, dict):
        days = [d.get("Date") for d in (parsed.get("ScheduleDays") or []) if isinstance(d, dict) and d.get("Date")]
        token = str(parsed.get("Token") or "")
    days = sorted(list(dict.fromkeys([d for d in days if d])), key=lambda x: normalize_date_s(x))
    source = "protocol_only_post_selection" if not post.clicked else "protocol_after_post_dom_click"
    steps.append({"name": "schedule_days", "status": rec.get("status"), "days_count": len(days), "token_len": len(token), "source": source})
    if transport_failed(rec):
        reason = status_reason(rec)
        return StageResult(False, "business_query", f"schedule days failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": reason}, retryable=True), None
    if hard_status(rec) >= 400:
        reason = status_reason(rec)
        needs = "rate_limit_429" if hard_status(rec) == 429 else reason
        return StageResult(False, "business_query", f"schedule days failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": needs}, retryable=True), None
    emit(ctx, "business_dates_collected", {"post_id": post.post_id, "post_name": post.post_name, "days_count": len(days), "days": [normalize_date_s(d) for d in days[:80]], "clicked_date": False})
    slot_patch(ctx, reason="dates_collected", reason_zh=f"已收集日期 {len(days)} 个，尚未点击日期", availability_days=len(days))
    return None, DateCollection(days=days, token=token, source=source, raw=parsed if isinstance(parsed, dict) else {})
