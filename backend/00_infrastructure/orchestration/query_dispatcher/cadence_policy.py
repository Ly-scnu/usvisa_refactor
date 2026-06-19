from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any


PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy
clock = import_module("00_infrastructure.orchestration.scheduler_clock")


@dataclass(frozen=True)
class CadenceSnapshot:
    mode: str
    interval_seconds: float
    per_session_gap_seconds: float
    failure_gap_seconds: float
    wait_poll_seconds: float
    active_query_lease_seconds: float
    candidate_count: int
    desired_active_slots: int
    same_second_guard: bool
    reason: str
    peak_mode: dict[str, Any]


class QueryCadencePolicy:
    """Translate user SLA into dispatch cadence.

    Normal mode targets result freshness (default 30s).  Release/peak mode is a
    burst window: one official business API per second at most, with several
    ready candidates waiting as backups.
    """

    def __init__(self, config: Any):
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def peak(self) -> dict[str, Any]:
        try:
            return PeakWindowPolicy(self.config).current(clock.now_dt()).to_dict()
        except Exception as exc:
            return {"mode": "normal", "reason": "放票期策略不可用", "error": repr(exc)}

    def is_burst(self, peak: dict[str, Any] | None = None) -> bool:
        data = peak or self.peak()
        return str(data.get("mode") or "normal") == "peak"

    def snapshot(self) -> CadenceSnapshot:
        peak = self.peak()
        burst = self.is_burst(peak)
        if burst:
            interval = max(1.0, float(getattr(self.cfg, "release_burst_interval_seconds", 1.0) or 1.0))
            per_session_gap = max(0.0, float(getattr(self.cfg, "release_per_session_min_query_interval_seconds", 1.0) or 1.0))
            failure_gap = max(0.0, float(getattr(self.cfg, "release_failure_cooldown_seconds", 1.0) or 1.0))
            wait_poll = max(0.1, float(getattr(self.cfg, "release_wait_poll_seconds", 0.3) or 0.3))
            lease = max(10.0, float(getattr(self.cfg, "release_active_query_lease_seconds", 45.0) or 45.0))
            desired = max(2, int(getattr(self.cfg, "release_period_desired_active_slots", 10) or 10))
            candidates = max(1, int(getattr(self.cfg, "release_candidate_count", 3) or 3))
            reason = str(peak.get("reason") or "放票突发期：秒级派发，同秒仅一个业务 API")
            return CadenceSnapshot("release_burst", interval, per_session_gap, failure_gap, wait_poll, lease, candidates, desired, True, reason, peak)

        base = (
            getattr(self.cfg, "normal_target_result_interval_seconds", None)
            or getattr(self.cfg, "target_success_interval_seconds", None)
            or getattr(self.cfg, "target_global_query_interval_seconds", 30.0)
            or 30.0
        )
        interval = max(5.0, float(base))
        per_session_gap = max(1.0, float(getattr(self.cfg, "per_session_min_query_interval_seconds", 180.0) or 180.0))
        failure_gap = max(1.0, float(getattr(self.cfg, "query_failure_cooldown_seconds", 60.0) or 60.0))
        wait_poll = max(0.5, float(getattr(self.cfg, "wait_poll_seconds", 2.0) or 2.0))
        lease = max(30.0, float(getattr(self.cfg, "active_query_lease_seconds", 90.0) or 90.0))
        desired = max(2, int(getattr(self.cfg, "normal_active_slots", 4) or 4))
        candidates = max(1, int(getattr(self.cfg, "normal_candidate_count", 3) or 3))
        return CadenceSnapshot("normal", interval, per_session_gap, failure_gap, wait_poll, lease, candidates, desired, True, "普通期：30秒结果新鲜度，按查询耗时提前启动", peak)

