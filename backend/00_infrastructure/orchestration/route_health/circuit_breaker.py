from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


class RouteCircuitBreaker:
    """Apply route cooldowns after repeated route-level failures."""

    def apply(self, rec: dict[str, Any], outcome: str) -> dict[str, Any]:
        now = clock.now_dt()
        reason = ""
        cooldown_seconds = 0
        existing_reason = str(rec.get("cooldown_reason") or "")
        existing_wait = clock.seconds_until(str(rec.get("cooldown_until") or ""))
        # Only the current outcome may open/extend a cooldown.  A historical
        # hard-block streak must not make a later recoverable CF challenge push
        # the same route another 30 minutes into the future.
        if outcome == "network_error" and int(rec.get("consecutive_network_errors") or 0) >= 2:
            reason = "network_error_streak"
            cooldown_seconds = 5 * 60
        if outcome == "rate_limit_429" and int(rec.get("consecutive_rate_429") or 0) >= 2:
            reason = "rate_429_streak"
            cooldown_seconds = 3 * 60
        if outcome == "cf_challenge":
            cf_streak = int(rec.get("consecutive_cf_challenges") or 0)
            # A single CF challenge is recoverable, but dozens of consecutive
            # CF re-entries on the same country/ASN route are route-level
            # pressure.  Without a soft breaker the orchestrator keeps feeding
            # all 10 slots into the same bad routes and creates a CF storm.
            if cf_streak >= 100:
                reason = "cf_challenge_storm"
                cooldown_seconds = 10 * 60
            elif cf_streak >= 50:
                reason = "cf_challenge_storm"
                cooldown_seconds = 5 * 60
            elif cf_streak >= 20:
                reason = "cf_challenge_storm"
                cooldown_seconds = 3 * 60
            # Do not let a soft CF cooldown overwrite an active hard-block
            # cooldown; keep the stronger evidence intact.
            if existing_wait > 0 and existing_reason == "hard_block_streak":
                reason = ""
                cooldown_seconds = 0
        if outcome in {"ban_1015", "access_denied"} and int(rec.get("consecutive_hard_blocks") or 0) >= 1:
            reason = "hard_block_streak"
            # In the constant-10-slot mode a 30 minute country/ASN-level
            # hard-block cooldown is too coarse: one access_denied on each of
            # JP/SG/US can put the whole production pool into an all-routes
            # cooling blackout.  Keep the breaker, but make it a recovery
            # window instead of a long global stop.
            cooldown_seconds = 8 * 60
        if cooldown_seconds > 0:
            rec["cooldown_until"] = (now + timedelta(seconds=cooldown_seconds)).isoformat(timespec="seconds")
            rec["cooldown_reason"] = reason
            rec["cooldown_seconds"] = cooldown_seconds
        return rec
