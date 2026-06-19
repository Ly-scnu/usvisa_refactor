from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any

from .models import EventPressure

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


def _parse_ts(value: Any) -> datetime | None:
    return clock.parse_ts(value)


def _within_window(ts: Any, window_seconds: int) -> bool:
    dt = _parse_ts(ts)
    if not dt:
        return False
    now = clock.now_dt()
    if dt.tzinfo and not now.tzinfo:
        now = now.astimezone(dt.tzinfo)
    elif now.tzinfo and not dt.tzinfo:
        dt = dt.replace(tzinfo=now.tzinfo)
    return 0 <= (now - dt).total_seconds() <= window_seconds


class SlaEventAnalyzer:
    """Analyze recent event stream and classify the current bottleneck.

    This module is intentionally read-only.  It never starts slots and never
    calls the target site; it only turns raw events into pressure metrics.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def analyze(self) -> EventPressure:
        window = int(getattr(self.cfg, "event_window_seconds", 300) or 300)
        events = self.store.events_tail(3000) if self.store else []
        events = [e for e in events if _within_window(e.get("created_at") or e.get("ts"), window)]
        result = EventPressure(window_seconds=window, total_events=len(events))
        for ev in events:
            et = str(ev.get("event_type") or "")
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            text = " ".join(
                [
                    et,
                    str(payload.get("stage") or ""),
                    str(payload.get("reason") or ""),
                    str(payload.get("message") or ""),
                    str(payload.get("error_type") or ""),
                    str(payload.get("needs_recover") or ""),
                    str(payload.get("kind") or ""),
                ]
            ).lower()
            significant = False
            if et == "recovery_attempt":
                result.recovery_events += 1
            if et == "round_start":
                result.round_start_events += 1
            if et == "browser_launched":
                result.browser_launch_events += 1
            if any(x in text for x in ["waiting_room", "waiting room", "等候", "等待室"]):
                result.waiting_room_events += 1
                significant = True
            if any(x in text for x in ["cf_challenge", "cloudflare", "challenge", "turnstile", "人机"]):
                result.cf_events += 1
                significant = True
            if any(x in text for x in ["login", "security_question", "b2c", "登录", "密保"]):
                result.login_events += 1
                significant = True
            if any(x in text for x in ["network_error", "err_proxy", "err_tunnel", "failed_to_fetch", "chrome_network_error", "网络错误"]):
                result.network_events += 1
                significant = True
            if any(x in text for x in ["429", "rate_limit", "rate limit", "rate_limited"]):
                result.rate_limit_events += 1
                significant = True
            if any(x in text for x in ["1015", "access_denied", "blocked by access", "ban", "forbidden"]):
                result.risk_events += 1
                significant = True
            if et in {"business_dates_collected", "smart_query_completed"} and (payload.get("success") is True or "dates_collected" in et):
                result.success_events += 1
                significant = True
            if significant:
                result.significant_events += 1

        # Churn is intentionally an absolute signal, not only a percentage of
        # failures.  The bad runtime pattern is: many fresh rounds/browsers and
        # recovery attempts in a short window, but very few successful queries.
        # In that state adding more slots amplifies CF/429 pressure and keeps
        # destroying the hot pool.
        result.churn_events = result.recovery_events + result.round_start_events + result.browser_launch_events
        denom = max(1, result.significant_events)
        result.waiting_room_pressure = round(result.waiting_room_events / denom, 3)
        result.cf_pressure = round(result.cf_events / denom, 3)
        result.login_pressure = round(result.login_events / denom, 3)
        result.network_pressure = round(result.network_events / denom, 3)
        result.rate_limit_pressure = round(result.rate_limit_events / denom, 3)
        result.risk_pressure = round(result.risk_events / denom, 3)
        result.churn_pressure = round(result.churn_events / max(1, len(events)), 3)

        thresholds = {
            "risk": float(getattr(self.cfg, "risk_pressure_threshold", 0.25) or 0.25),
            "waiting_room": float(getattr(self.cfg, "waiting_room_pressure_threshold", 0.45) or 0.45),
            "cf": float(getattr(self.cfg, "cf_pressure_threshold", 0.45) or 0.45),
        }
        churn_threshold = int(getattr(self.cfg, "churn_event_threshold", 25) or 25)
        cf_storm_threshold = int(getattr(self.cfg, "cf_storm_event_threshold", 12) or 12)
        rate_storm_threshold = int(getattr(self.cfg, "rate_limit_storm_event_threshold", 3) or 3)
        if result.risk_pressure >= thresholds["risk"] and result.risk_events:
            result.bottleneck = "risk"
        elif result.rate_limit_events >= rate_storm_threshold or (result.rate_limit_events and result.rate_limit_pressure >= 0.25):
            result.bottleneck = "rate_limit"
        elif result.network_events and result.network_pressure >= 0.30:
            result.bottleneck = "network"
        elif result.cf_events >= cf_storm_threshold or (result.cf_pressure >= thresholds["cf"] and result.cf_events):
            result.bottleneck = "cf_challenge"
        elif result.churn_events >= churn_threshold and result.success_events == 0:
            result.bottleneck = "churn"
        elif result.waiting_room_pressure >= thresholds["waiting_room"] and result.waiting_room_events:
            result.bottleneck = "waiting_room"
        elif result.login_pressure >= 0.35 and result.login_events:
            result.bottleneck = "login"
        else:
            result.bottleneck = "normal"
        return result
