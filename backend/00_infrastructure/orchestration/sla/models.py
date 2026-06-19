from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SessionInventoryStats:
    enabled: bool = True
    target_hot_query_sessions: int = 4
    target_login_standby_sessions: int = 2
    target_candidate_sessions: int = 2
    hot_query_sessions: int = 0
    ready_query_sessions: int = 0
    cooling_success_sessions: int = 0
    recovering_success_sessions: int = 0
    terminal_success_sessions: int = 0
    login_standby_sessions: int = 0
    candidate_sessions: int = 0
    recovering_sessions: int = 0
    terminal_risk_sessions: int = 0
    active_slots: int = 0
    desired_inventory_slots: int = 8
    hot_deficit: int = 0
    login_standby_deficit: int = 0
    candidate_deficit: int = 0
    total_deficit: int = 0
    health_level: str = "unknown"
    reason: str = "库存未统计"
    slots: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EventPressure:
    window_seconds: int
    total_events: int = 0
    significant_events: int = 0
    waiting_room_events: int = 0
    cf_events: int = 0
    login_events: int = 0
    network_events: int = 0
    rate_limit_events: int = 0
    risk_events: int = 0
    recovery_events: int = 0
    round_start_events: int = 0
    browser_launch_events: int = 0
    churn_events: int = 0
    success_events: int = 0
    waiting_room_pressure: float = 0.0
    cf_pressure: float = 0.0
    login_pressure: float = 0.0
    network_pressure: float = 0.0
    rate_limit_pressure: float = 0.0
    risk_pressure: float = 0.0
    churn_pressure: float = 0.0
    bottleneck: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionPoolStats:
    active_slots: int = 0
    hot_sessions: int = 0
    query_wait_sessions: int = 0
    querying_sessions: int = 0
    cooling_sessions: int = 0
    producing_sessions: int = 0
    recovering_sessions: int = 0
    pending_slots: int = 0
    last_success_at: str = ""
    next_target_query_at: str = ""
    seconds_to_target: float = 0.0
    seconds_since_success: float = 999999.0
    target_interval_seconds: float = 60.0
    needed_hot_sessions: int = 3
    recent_success_intervals: list[float] = field(default_factory=list)
    stable_successes: int = 0
    peak_mode: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SlaDecision:
    enabled: bool = True
    desired_active_slots: int = 2
    min_slots: int = 2
    max_slots: int = 10
    normal_active_slots: int = 3
    soft_active_slots: int = 4
    pressure_level: str = "normal"
    bottleneck: str = "normal"
    reason: str = "正常保持"
    should_scale_up: bool = False
    scale_cooldown_seconds: float = 60.0
    target_interval_seconds: float = 60.0
    next_target_query_at: str = ""
    seconds_to_target: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    peak_mode: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
