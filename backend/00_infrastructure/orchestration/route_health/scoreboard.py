from __future__ import annotations

from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


class RouteScoreboard:
    """Persistent route counters and cooldown updates."""

    def update(self, state: dict[str, Any], key: str, outcome: str, *, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        state.setdefault("routes", {})
        routes = state["routes"]
        rec = dict(routes.get(key) or {"route_key": key})
        now_s = clock.now_dt().isoformat(timespec="seconds")
        rec["route_key"] = key
        rec["last_outcome"] = outcome
        rec["last_detail"] = detail or {}
        if outcome == "success":
            rec["success_count"] = int(rec.get("success_count") or 0) + 1
            rec["last_success_at"] = now_s
            rec["consecutive_network_errors"] = 0
            rec["consecutive_hard_blocks"] = 0
            rec["consecutive_cf_challenges"] = 0
            rec["consecutive_rate_429"] = 0
            rec["cooldown_until"] = ""
            rec["cooldown_reason"] = ""
            rec["cooldown_seconds"] = 0
        elif outcome == "network_error":
            rec["network_error_count"] = int(rec.get("network_error_count") or 0) + 1
            rec["consecutive_network_errors"] = int(rec.get("consecutive_network_errors") or 0) + 1
            # A fresh transport failure should not keep extending a stale
            # 1015/access-denied hard-block streak from a previous generation.
            rec["consecutive_hard_blocks"] = 0
            rec["last_failure_at"] = now_s
        elif outcome == "cf_challenge":
            rec["cf_challenge_count"] = int(rec.get("cf_challenge_count") or 0) + 1
            rec["consecutive_cf_challenges"] = int(rec.get("consecutive_cf_challenges") or 0) + 1
            # CF challenge is a soft, recoverable site state.  It proves the
            # route reached the site, so it should break network-error and
            # stale hard-block streaks instead of extending a 30-minute ban.
            rec["consecutive_network_errors"] = 0
            rec["consecutive_hard_blocks"] = 0
            rec["last_failure_at"] = now_s
        elif outcome == "rate_limit_429":
            rec["rate_429_count"] = int(rec.get("rate_429_count") or 0) + 1
            rec["consecutive_rate_429"] = int(rec.get("consecutive_rate_429") or 0) + 1
            rec["consecutive_network_errors"] = 0
            rec["consecutive_hard_blocks"] = 0
            rec["last_failure_at"] = now_s
        elif outcome == "ban_1015":
            rec["ban_1015_count"] = int(rec.get("ban_1015_count") or 0) + 1
            rec["consecutive_hard_blocks"] = int(rec.get("consecutive_hard_blocks") or 0) + 1
            rec["consecutive_network_errors"] = 0
            rec["last_failure_at"] = now_s
        elif outcome == "access_denied":
            rec["access_denied_count"] = int(rec.get("access_denied_count") or 0) + 1
            rec["consecutive_hard_blocks"] = int(rec.get("consecutive_hard_blocks") or 0) + 1
            rec["consecutive_network_errors"] = 0
            rec["last_failure_at"] = now_s
        else:
            rec["other_failure_count"] = int(rec.get("other_failure_count") or 0) + 1
            rec["last_failure_at"] = now_s
        routes[key] = rec
        state["routes"] = routes
        state["last_updated_route"] = key
        return rec
