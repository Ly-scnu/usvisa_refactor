from __future__ import annotations

from typing import Any

from .sla.decision_engine import SlaDecisionEngine
from .sla.event_analyzer import SlaEventAnalyzer
from .sla.latency_estimator import QueryLatencyEstimator
from .sla.session_pool import SlaSessionPool
from .sla.models import SlaDecision
from .sla.route_recovery_ramp import RouteRecoveryRampPolicy
from .route_health.route_cooling_guard import routes_cooling_snapshot


class SlaOrchestrator:
    """SLA-driven smart slot orchestrator.

    This is the single high-level entry used by producer/API/UI.  It combines:
    event pressure -> current bottleneck, session pool -> available capacity,
    decision engine -> desired active slot count.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)
        self.event_analyzer = SlaEventAnalyzer(store, config)
        self.session_pool = SlaSessionPool(store, config)
        self.engine = SlaDecisionEngine(config)
        self.latency_estimator = QueryLatencyEstimator(store, config)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "enabled", True)) and self.store is not None

    def decide(self, *, active_slots: int | None = None) -> SlaDecision:
        if not self.enabled:
            min_slots = int(getattr(getattr(self.config, "slots", None), "total_slots", 2) or 2)
            return SlaDecision(enabled=False, desired_active_slots=min_slots, min_slots=min_slots, max_slots=min_slots, reason="smart_orchestrator disabled")
        pressure = self.event_analyzer.analyze()
        pool = self.session_pool.snapshot(active_count=active_slots)
        latency = self.latency_estimator.estimate()
        decision = self.engine.decide(pool, pressure)
        route_cooling = routes_cooling_snapshot(self.config, self.store)
        route_recovery_ramp = {"active": False}
        if route_cooling.get("all_routes_cooling"):
            wait_s = float(route_cooling.get("min_wait_seconds") or 0.0)
            min_slots = max(1, int(decision.min_slots or 2))
            decision.desired_active_slots = min(decision.desired_active_slots, min_slots)
            decision.pressure_level = "proxy_routes_cooling"
            decision.bottleneck = "proxy_route_cooling"
            decision.should_scale_up = False
            decision.scale_cooldown_seconds = max(float(decision.scale_cooldown_seconds or 0.0), min(300.0, max(15.0, wait_s or 60.0)))
            decision.reason = f"全部代理路线冷却中，暂停扩槽，约 {round(wait_s, 1)}s 后再补热池"
            decision.evidence = {
                **(decision.evidence or {}),
                "route_cooling": route_cooling,
                "all_proxy_routes_cooling": True,
            }
        else:
            decision, ramp = RouteRecoveryRampPolicy(self.config).apply(
                decision,
                route_cooling,
                active_slots=int(active_slots if active_slots is not None else pool.active_slots or 0),
            )
            route_recovery_ramp = ramp.to_dict()
        decision.latency = latency
        payload = {
            "enabled": decision.enabled,
            "decision": decision.to_dict(),
            "pool": pool.to_dict(),
            "pressure": pressure.to_dict(),
            "peak_mode": pool.peak_mode,
            "inventory": pool.inventory,
            "latency": latency,
            "route_cooling": route_cooling,
            "route_recovery_ramp": route_recovery_ramp,
            "summary": {
                "target_interval_seconds": decision.target_interval_seconds,
                "next_target_query_at": decision.next_target_query_at,
                "seconds_to_target": decision.seconds_to_target,
                "query_launch_lead_seconds": latency.get("lead_seconds"),
                "peak_mode": pool.peak_mode,
                "inventory": pool.inventory,
                "active_slots": pool.active_slots,
                "desired_active_slots": decision.desired_active_slots,
                "pressure_level": decision.pressure_level,
                "bottleneck": decision.bottleneck,
                "reason": decision.reason,
                "route_cooling": route_cooling,
                "route_recovery_ramp": route_recovery_ramp,
            },
        }
        try:
            self.store.write_sla_state(payload)
        except Exception:
            pass
        return decision

    def snapshot(self, *, active_slots: int | None = None) -> dict[str, Any]:
        try:
            decision = self.decide(active_slots=active_slots)
            current = self.store.sla_state() if self.store else {}
            if current:
                return current
            return {"enabled": decision.enabled, "decision": decision.to_dict()}
        except Exception as exc:
            old = self.store.sla_state() if self.store else {}
            if old:
                old = dict(old)
                old["snapshot_error"] = repr(exc)
                return old
            return {"enabled": False, "snapshot_error": repr(exc)}
