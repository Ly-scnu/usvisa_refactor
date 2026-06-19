from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .. import scheduler_clock as clock
from .models import SlaDecision


@dataclass
class RouteRecoveryRamp:
    active: bool = False
    cap_slots: int = 0
    seconds_since_recovery: float = 0.0
    recovered_routes: list[dict[str, Any]] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["recovered_routes"] = data.get("recovered_routes") or []
        return data


class RouteRecoveryRampPolicy:
    """Cap scale-up shortly after proxy routes recover from cooldown.

    Runtime evidence showed this pattern:

    all routes cooling -> first route recovers -> inventory_hot_low immediately
    asks for 8/10 slots -> many browsers launch together -> login/CF/network
    failures push every route back into cooldown.

    The ramp keeps the scheduler aggressive but staged: probe with a small
    number of slots first, then open more capacity every few seconds if routes
    do not fail again.
    """

    def __init__(self, config: Any):
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def evaluate(self, route_cooling: dict[str, Any], *, min_slots: int, max_slots: int, peak_mode: str = "") -> RouteRecoveryRamp:
        if not bool(getattr(self.cfg, "route_recovery_ramp_enabled", True)):
            return RouteRecoveryRamp(False)
        if bool(route_cooling.get("all_routes_cooling")):
            return RouteRecoveryRamp(False)
        window_s = max(5.0, float(getattr(self.cfg, "route_recovery_ramp_window_seconds", 240.0) or 240.0))
        step_s = max(1.0, float(getattr(self.cfg, "route_recovery_ramp_step_seconds", 30.0) or 30.0))
        initial = max(min_slots, int(getattr(self.cfg, "route_recovery_ramp_initial_slots", min_slots + 1) or (min_slots + 1)))
        normal_max = max(initial, int(getattr(self.cfg, "route_recovery_ramp_max_slots", 6) or 6))
        peak_max = max(normal_max, int(getattr(self.cfg, "route_recovery_ramp_peak_max_slots", 8) or 8))

        recovered: list[dict[str, Any]] = []
        for row in list(route_cooling.get("routes") or []):
            if bool(row.get("cooling")):
                continue
            cooldown_until = str(row.get("cooldown_until") or "")
            recovered_at = str(row.get("last_recovered_at") or "")
            # Normal expiry keeps cooldown_until in the route file.  Manual
            # maintenance/repair clears cooldown_until but records
            # last_recovered_at; both should trigger the same ramp.
            dt = clock.parse_ts(cooldown_until) or clock.parse_ts(recovered_at)
            if not dt:
                continue
            now = clock.now_dt()
            if dt.tzinfo and not now.tzinfo:
                now = now.astimezone(dt.tzinfo)
            elif now.tzinfo and not dt.tzinfo:
                dt = dt.replace(tzinfo=now.tzinfo)
            ago = max(0.0, (now - dt).total_seconds())
            if 0.0 <= ago <= window_s:
                recovered.append({**row, "seconds_since_recovery": round(ago, 1)})
        if not recovered:
            return RouteRecoveryRamp(False)

        # Use the newest recovered route as the conservative ramp anchor.
        seconds_since = min(float(x.get("seconds_since_recovery") or 0.0) for x in recovered)
        cap = initial + int(seconds_since // step_s)
        cap_limit = peak_max if peak_mode in {"prewarm", "peak"} else normal_max
        cap = max(min_slots, min(max_slots, min(cap, cap_limit)))
        return RouteRecoveryRamp(
            True,
            cap,
            round(seconds_since, 1),
            recovered,
            f"代理路线刚恢复 {round(seconds_since, 1)}s，渐进预热上限 {cap} 槽，避免恢复瞬间扩槽风暴",
        )

    def apply(self, decision: SlaDecision, route_cooling: dict[str, Any], *, active_slots: int) -> tuple[SlaDecision, RouteRecoveryRamp]:
        ramp = self.evaluate(
            route_cooling,
            min_slots=int(decision.min_slots or 2),
            max_slots=int(decision.max_slots or 10),
            peak_mode=str((decision.peak_mode or {}).get("mode") or ""),
        )
        if not ramp.active or int(decision.desired_active_slots or 0) <= int(ramp.cap_slots or 0):
            return decision, ramp
        decision.desired_active_slots = max(int(decision.min_slots or 2), int(ramp.cap_slots or decision.desired_active_slots))
        decision.should_scale_up = decision.desired_active_slots > int(active_slots or 0)
        decision.pressure_level = f"{decision.pressure_level}_ramped"
        decision.reason = f"{decision.reason}；{ramp.reason}"
        decision.evidence = {**(decision.evidence or {}), "route_recovery_ramp": ramp.to_dict()}
        return decision, ramp
