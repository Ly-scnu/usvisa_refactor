from __future__ import annotations

from datetime import datetime
from importlib import import_module
from math import ceil
from typing import Any

from .models import SessionPoolStats

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy
SessionInventoryAnalyzer = import_module("00_infrastructure.orchestration.sla.session_inventory").SessionInventoryAnalyzer


def _parse_ts(value: Any) -> datetime | None:
    return clock.parse_ts(value)


def _seconds_since(ts: Any) -> float:
    dt = _parse_ts(ts)
    if not dt:
        return 999999.0
    now = clock.now_dt()
    if dt.tzinfo and not now.tzinfo:
        now = now.astimezone(dt.tzinfo)
    elif now.tzinfo and not dt.tzinfo:
        dt = dt.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - dt).total_seconds())


def _future_ts(ts: Any) -> bool:
    dt = _parse_ts(ts)
    if not dt:
        return False
    now = clock.now_dt()
    if dt.tzinfo and not now.tzinfo:
        now = now.astimezone(dt.tzinfo)
    elif now.tzinfo and not dt.tzinfo:
        dt = dt.replace(tzinfo=now.tzinfo)
    return dt > now


UNHEALTHY_PAGES = {
    "cf_challenge",
    "login",
    "security_questions",
    "idp_loading",
    "waiting_room",
    "rate_limit_1015",
    "rate_limit_429",
    "access_denied",
    "network_error",
    "login_failed",
    "callback_not_found",
    "page_not_found",
    "blank",
}
QUERY_READY_PAGES = {"home", "schedule", "site"}


class SlaSessionPool:
    """Build hot/cooling/producing pool statistics from local runtime state."""

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _target_interval(self) -> float:
        base = max(
            1.0,
            float(
                getattr(self.cfg, "target_success_interval_seconds", 0)
                or getattr(self.cfg, "target_global_query_interval_seconds", 60.0)
                or 60.0
            ),
        )
        try:
            return max(1.0, float(PeakWindowPolicy(self.config).effective_interval(base)))
        except Exception:
            return base

    def _last_success_from_history(self) -> str:
        best = ""
        for row in self.store.ticket_history(1000):
            ts = str(row.get("queried_at") or row.get("ts") or "")
            if (row.get("days") is not None or row.get("normalized_days") is not None) and ts > best:
                best = ts
        return best

    def _recent_success_intervals(self) -> list[float]:
        times = []
        for row in self.store.ticket_history(1000):
            ts = str(row.get("queried_at") or row.get("ts") or "")
            if row.get("days") is not None or row.get("normalized_days") is not None:
                dt = _parse_ts(ts)
                if dt:
                    times.append(dt)
        times = sorted(times)[-20:]
        out: list[float] = []
        for a, b in zip(times, times[1:]):
            out.append(round(max(0.0, (b - a).total_seconds()), 1))
        return out

    def snapshot(self, *, active_count: int | None = None) -> SessionPoolStats:
        slots = self.store.read_slots() if self.store else {}
        scheduler_state = self.store.scheduler_state() if self.store else {}
        sessions = scheduler_state.get("sessions") if isinstance(scheduler_state.get("sessions"), dict) else {}
        target_interval = self._target_interval()
        per_session_gap = max(1.0, float(getattr(self.cfg, "per_session_min_query_interval_seconds", 180.0) or 180.0))
        needed_hot = max(1, min(int(getattr(self.cfg, "max_slots", 10) or 10), ceil(per_session_gap / target_interval)))
        now = clock.now_dt()

        try:
            peak_mode = PeakWindowPolicy(self.config).current(now).to_dict()
        except Exception:
            peak_mode = {"mode": "normal", "reason": "高峰策略不可用"}
        stats = SessionPoolStats(target_interval_seconds=target_interval, needed_hot_sessions=needed_hot, peak_mode=peak_mode)
        stats.active_slots = int(active_count) if active_count is not None else sum(1 for s in slots.values() if str(s.get("state")) == "running")
        stats.pending_slots = sum(1 for s in slots.values() if str(s.get("state")) == "pending")
        live_session_keys: set[str] = set()
        live_slots_by_key: dict[str, dict[str, Any]] = {}
        for slot_id, slot in slots.items():
            if str(slot.get("state") or "") != "running":
                continue
            round_no = int(slot.get("round") or 0)
            started_at = str(slot.get("round_started_at") or "")
            if round_no > 0 and started_at:
                key = f"{slot_id}/round_{round_no:04d}/{started_at}"
                live_session_keys.add(key)
                live_slots_by_key[key] = slot

        active_query = scheduler_state.get("active_query") if isinstance(scheduler_state.get("active_query"), dict) else None
        active_query_key = str(active_query.get("session_key") or "") if active_query else ""

        for key, sess in sessions.items():
            if not isinstance(sess, dict):
                continue
            # Only current live rounds can be used for the next SLA window.
            # Historical ready_warm sessions are useful for analytics but must
            # not fool the scheduler into thinking hot capacity exists now.
            if str(key) not in live_session_keys and str(key) != active_query_key:
                continue
            sess_state = str(sess.get("state") or "")
            slot = live_slots_by_key.get(str(key), {})
            page = str(slot.get("live_page_stage") or "")
            smart_state = str(slot.get("smart_query_state") or "")
            is_active_query = bool(active_query_key and str(sess.get("session_key") or key) == active_query_key)
            if is_active_query or sess_state == "querying":
                stats.querying_sessions += 1
                if int(sess.get("success_count") or 0) > 0:
                    stats.hot_sessions += 1
                continue
            if sess_state == "recovering" or page in UNHEALTHY_PAGES or smart_state == "preflight_blocked":
                stats.recovering_sessions += 1
                continue
            if int(sess.get("success_count") or 0) > 0:
                if page and page not in QUERY_READY_PAGES and str(slot.get("stage") or "") != "business_query":
                    stats.recovering_sessions += 1
                elif _future_ts(sess.get("next_query_at")):
                    stats.cooling_sessions += 1
                elif sess_state in {"ready_warm", "ready_hot"}:
                    stats.hot_sessions += 1

        for slot in slots.values():
            if str(slot.get("state") or "") != "running":
                continue
            stage = str(slot.get("stage") or "")
            reason = str(slot.get("last_reason") or "")
            page = str(slot.get("live_page_stage") or "")
            smart_state = str(slot.get("smart_query_state") or "")
            if page in UNHEALTHY_PAGES or smart_state == "preflight_blocked":
                stats.recovering_sessions += 1
            elif stage == "business_query" and (reason.startswith("smart_query_wait") or smart_state in {"waiting", "planned", "cooling"}):
                stats.query_wait_sessions += 1
            elif stage == "business_query" and smart_state == "querying":
                stats.querying_sessions += 1
            elif stage == "business_query" and page in QUERY_READY_PAGES:
                stats.query_wait_sessions += 1
            elif stage in {"cf_gate", "waiting_room", "login", "real_one_dragon", "proxy_acquire", "proxy_acquired"}:
                stats.producing_sessions += 1
            elif stage in {"round_recycle", "direct_only_recycle"}:
                stats.recovering_sessions += 1

        last_success = str(scheduler_state.get("last_success_at") or self._last_success_from_history() or "")
        stats.last_success_at = last_success
        if last_success:
            dt = _parse_ts(last_success)
            stats.next_target_query_at = clock.add_seconds(dt, target_interval) if dt else ""
            stats.seconds_to_target = round(clock.seconds_until(stats.next_target_query_at), 1)
            stats.seconds_since_success = round(_seconds_since(last_success), 1)
        stats.recent_success_intervals = self._recent_success_intervals()
        stable_limit = float(target_interval + max(15.0, target_interval * 0.5))
        stats.stable_successes = sum(1 for x in stats.recent_success_intervals[-int(getattr(self.cfg, "stable_success_window_count", 5) or 5):] if x <= stable_limit)
        try:
            stats.inventory = SessionInventoryAnalyzer(self.store, self.config).snapshot(active_count=stats.active_slots).to_dict()
        except Exception as exc:
            stats.inventory = {"enabled": False, "error": repr(exc), "reason": "库存统计失败"}
        return stats
