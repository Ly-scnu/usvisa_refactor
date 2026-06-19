from __future__ import annotations

from importlib import import_module
from typing import Any

Event = import_module("00_infrastructure.events.event_bus").Event

STAGE_EVENT_ZH = {
    "business_navigation_start": "进入预约入口",
    "business_manage_clicked": "点击预约入口",
    "business_schedule_page_ready": "预约页就绪",
    "business_context_resolved": "申请上下文已解析",
    "business_post_selecting": "选择北京使馆",
    "business_post_selected": "北京已选择",
    "business_dates_collecting": "收集可预约日期",
    "business_dates_collected": "日期已收集",
    "business_date_rejected": "日期不符合目标",
    "business_date_accepted": "日期命中目标",
    "business_slot_collecting": "查询时间段",
    "business_entries_collected": "时间段已收集",
    "business_booking_signal_ready": "抢票信号已生成",
    "business_retry_home": "回首页重试",
    "business_blocked_cf": "遇到 CF 人机",
    "business_blocked_login": "退回登录/密保",
    "business_blocked_waiting_room": "进入等待室",
    "business_rate_limit_cooldown": "限流冷却",
}


def emit(ctx: Any, event_type: str, payload: dict[str, Any] | None = None) -> None:
    payload = dict(payload or {})
    payload.setdefault("stage_zh", STAGE_EVENT_ZH.get(event_type, event_type))
    if getattr(ctx, "event_bus", None):
        ctx.event_bus.publish(Event(event_type, slot_id=ctx.slot_id, round_id=ctx.round_id, payload=payload))


def slot_patch(ctx: Any, *, reason: str = "", reason_zh: str = "", **patch: Any) -> None:
    if not getattr(ctx, "store", None):
        return
    data = {"stage": "business_query", "stage_zh": "查日期/时间段"}
    if reason:
        data["last_reason"] = reason
    if reason_zh:
        data["last_reason_zh"] = reason_zh
    data.update(patch)
    try:
        ctx.store.update_slot(ctx.slot_id, **data)
    except Exception:
        pass
