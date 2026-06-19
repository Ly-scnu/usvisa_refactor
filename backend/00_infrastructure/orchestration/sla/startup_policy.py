from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any


PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy


@dataclass(frozen=True)
class StartupStaggerDecision:
    wait_seconds: float
    mode: str
    reason: str
    hot_sessions: int = 0
    query_wait_sessions: int = 0
    seconds_since_success: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SlotStartupPolicy:
    """Decide whether a newly started slot should wait before doing real work.

    The old implementation used ``slot_no * slot_start_stagger_seconds``.
    That is safe when a healthy hot pool already exists, but disastrous when
    the user has zero query-ready sessions: new slots sit idle for 120-270s
    before even reaching login/CF, exactly during 00/30 release windows.

    This policy keeps gentle staggering only when the pool is healthy.  When
    hot sessions are missing, SLA is already missed, or release prewarm/peak is
    active, it compresses the wait to a few seconds so production can actually
    create query-ready sessions.
    """

    def __init__(self, config: Any, store: Any):
        self.config = config
        self.store = store
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _pool(self) -> dict[str, Any]:
        try:
            return (self.store.sla_state() or {}).get("pool") or {}
        except Exception:
            return {}

    def _peak(self) -> dict[str, Any]:
        try:
            return PeakWindowPolicy(self.config).current().to_dict()
        except Exception as exc:
            return {"mode": "normal", "reason": "放票期策略不可用", "error": repr(exc)}

    def decide(self, *, slot_no: int) -> StartupStaggerDecision:
        base = max(0.0, float(getattr(getattr(self.config, "producer", None), "slot_start_stagger_seconds", 0) or 0))
        if slot_no <= 1 or base <= 0:
            return StartupStaggerDecision(0.0, "none", "首槽或未配置启动错峰")

        pool = self._pool()
        peak = self._peak()
        peak_mode = str(peak.get("mode") or "normal")
        hot = int(pool.get("hot_sessions") or 0)
        waiting = int(pool.get("query_wait_sessions") or 0)
        cooling = int(pool.get("cooling_sessions") or 0)
        seconds_since_success = float(pool.get("seconds_since_success") or 0.0)
        target_interval = float(pool.get("target_interval_seconds") or getattr(self.cfg, "normal_target_result_interval_seconds", 30.0) or 30.0)
        active_slots = int(pool.get("active_slots") or 0)
        hot_capacity = hot + waiting

        # Release windows must not be blocked by slot_no*30s.  Prewarm exists
        # specifically to get profiles through CF/login before :00/:30.
        if peak_mode == "peak":
            return StartupStaggerDecision(min(2.0, max(0.0, (slot_no - 1) * 0.25)), "release_peak_fast", str(peak.get("reason") or "放票期快速启动"), hot, waiting, seconds_since_success)
        if peak_mode == "prewarm":
            return StartupStaggerDecision(min(5.0, max(0.0, (slot_no - 1) * 0.5)), "release_prewarm_fast", str(peak.get("reason") or "放票前预热快速启动"), hot, waiting, seconds_since_success)

        # No hot sessions means the system cannot query at all.  Waiting before
        # login/CF only lengthens the blank window.
        if hot_capacity <= 0:
            return StartupStaggerDecision(min(5.0, max(0.0, (slot_no - 1) * 0.75)), "hot_pool_empty_fast", "热查询池为空：取消长错峰，尽快产出可查询会话", hot, waiting, seconds_since_success)

        # If we already missed the target, reduce but do not fully remove
        # staggering.  The business API gate still guarantees one query at a
        # time; this only speeds up session production.
        if seconds_since_success > max(target_interval, 1.0):
            return StartupStaggerDecision(min(10.0, max(0.0, (slot_no - 1) * 1.5)), "sla_missed_fast", "已错过目标查询间隔：缩短启动错峰补热池", hot, waiting, seconds_since_success)

        # Healthy pool: keep the original gentle stagger, but cap it so a high
        # slot number does not sit for several minutes doing nothing.
        raw = base * max(0, slot_no - 1)
        cap = 45.0 if active_slots >= 4 else 30.0
        return StartupStaggerDecision(min(raw, cap), "healthy_stagger", "热池基本可用：保留温和启动错峰避免 CF/登录同秒拥挤", hot, waiting, seconds_since_success)
