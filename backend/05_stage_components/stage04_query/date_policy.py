from __future__ import annotations

from typing import Any

from .events import emit
from .models import DateCollection, DateDecision
from .protocol import in_target_window, normalize_date_s


def decide_dates(ctx: Any, dates: DateCollection) -> DateDecision:
    cfg = ctx.runtime_config
    normalized = sorted(list(dict.fromkeys([normalize_date_s(d) for d in dates.days if normalize_date_s(d)])))
    acceptable = [d for d in normalized if in_target_window(d, cfg)]
    rejected = [d for d in normalized if d not in acceptable]
    selected = acceptable[0] if acceptable else ""
    if selected:
        emit(ctx, "business_date_accepted", {"selected_date": selected, "acceptable_dates": acceptable[:50], "cutoff_date": getattr(cfg.target, "cutoff_date", ""), "policy": "date <= cutoff; now allowed to click date and query slots"})
    else:
        emit(ctx, "business_date_rejected", {"days_count": len(normalized), "rejected_dates": rejected[:80], "cutoff_date": getattr(cfg.target, "cutoff_date", ""), "policy": "not clicking any date because no date is within target window"})
    return DateDecision(acceptable_dates=acceptable, rejected_dates=rejected, selected_date=selected, target_hit=bool(selected))
