from importlib import import_module
from types import SimpleNamespace


clock = import_module("00_infrastructure.orchestration.scheduler_clock")
models = import_module("00_infrastructure.orchestration.sla.models")
SlaDecisionEngine = import_module("00_infrastructure.orchestration.sla.decision_engine").SlaDecisionEngine
SlaEventAnalyzer = import_module("00_infrastructure.orchestration.sla.event_analyzer").SlaEventAnalyzer


def _cfg(**overrides):
    defaults = dict(
        min_slots=2,
        max_slots=10,
        normal_active_slots=3,
        soft_active_slots=4,
        prewarm_window_seconds=45,
        missed_target_grace_seconds=30,
        scale_up_stale_success_seconds=180,
        critical_stale_success_seconds=600,
        scale_up_cooldown_seconds=180,
        adaptive_inventory_active_slots=6,
        severe_gap_active_slots=8,
        cold_start_active_slots=3,
        release_cold_start_active_slots=4,
        stable_success_window_count=5,
        event_window_seconds=300,
        cf_pressure_threshold=0.45,
        risk_pressure_threshold=0.25,
        waiting_room_pressure_threshold=0.45,
        cf_storm_event_threshold=12,
        rate_limit_storm_event_threshold=3,
        churn_event_threshold=25,
    )
    defaults.update(overrides)
    return SimpleNamespace(smart_orchestrator=SimpleNamespace(**defaults))


def test_cf_storm_holds_instead_of_scaling_hot_pool_deficit():
    pool = models.SessionPoolStats(
        active_slots=8,
        hot_sessions=0,
        query_wait_sessions=0,
        cooling_sessions=0,
        needed_hot_sessions=4,
        seconds_since_success=900,
        target_interval_seconds=30,
        inventory={"enabled": True, "total_deficit": 5, "health_level": "hot_low", "desired_inventory_slots": 8},
        peak_mode={"mode": "normal"},
    )
    pressure = models.EventPressure(
        window_seconds=300,
        significant_events=20,
        cf_events=16,
        cf_pressure=0.8,
        bottleneck="cf_challenge",
    )
    decision = SlaDecisionEngine(_cfg()).decide(pool, pressure)
    assert decision.pressure_level == "cf_storm_hold"
    assert decision.desired_active_slots == 3
    assert decision.should_scale_up is False


def test_rate_limit_storm_preempts_peak_scale_up():
    pool = models.SessionPoolStats(
        active_slots=8,
        hot_sessions=0,
        query_wait_sessions=0,
        seconds_since_success=900,
        target_interval_seconds=1,
        peak_mode={"mode": "peak", "desired_active_slots": 10, "min_hot_sessions": 5},
    )
    pressure = models.EventPressure(
        window_seconds=300,
        significant_events=10,
        rate_limit_events=3,
        rate_limit_pressure=0.3,
        bottleneck="rate_limit",
    )
    decision = SlaDecisionEngine(_cfg()).decide(pool, pressure)
    assert decision.pressure_level == "rate_limit_hold"
    assert decision.desired_active_slots == 3


def test_event_analyzer_detects_churn_without_success():
    now = clock.now_dt().isoformat(timespec="seconds")
    events = []
    for _ in range(10):
        events.append({"event_type": "round_start", "created_at": now, "payload": {}})
        events.append({"event_type": "browser_launched", "created_at": now, "payload": {}})
        events.append({"event_type": "recovery_attempt", "created_at": now, "payload": {"reason": "loop"}})

    class Store:
        def events_tail(self, _n):
            return events

    pressure = SlaEventAnalyzer(Store(), _cfg(churn_event_threshold=25, cf_storm_event_threshold=99)).analyze()
    assert pressure.churn_events == 30
    assert pressure.bottleneck == "churn"


def test_peak_release_does_not_fan_out_ten_cold_slots_without_hot_pool():
    pool = models.SessionPoolStats(
        active_slots=2,
        hot_sessions=0,
        query_wait_sessions=0,
        cooling_sessions=0,
        needed_hot_sessions=5,
        seconds_since_success=900,
        target_interval_seconds=1,
        inventory={
            "enabled": True,
            "total_deficit": 10,
            "health_level": "hot_low",
            "desired_inventory_slots": 10,
            "hot_query_sessions": 0,
            "ready_query_sessions": 0,
            "cooling_success_sessions": 0,
            "recovering_success_sessions": 0,
        },
        peak_mode={"mode": "peak", "desired_active_slots": 10, "min_hot_sessions": 5, "reason": "release"},
    )
    pressure = models.EventPressure(window_seconds=300, bottleneck="normal")
    decision = SlaDecisionEngine(_cfg()).decide(pool, pressure)
    assert decision.pressure_level == "peak_cold_start_cap"
    assert decision.desired_active_slots == 4
    assert decision.evidence["cold_starting_without_hot"] is True


def test_inventory_hot_low_cold_start_is_capped_until_first_hot_session():
    pool = models.SessionPoolStats(
        active_slots=3,
        hot_sessions=0,
        query_wait_sessions=0,
        cooling_sessions=0,
        needed_hot_sessions=4,
        seconds_since_success=900,
        target_interval_seconds=30,
        inventory={
            "enabled": True,
            "total_deficit": 8,
            "health_level": "hot_low",
            "desired_inventory_slots": 8,
            "hot_query_sessions": 0,
            "ready_query_sessions": 0,
            "cooling_success_sessions": 0,
            "recovering_success_sessions": 0,
        },
        peak_mode={"mode": "normal"},
    )
    pressure = models.EventPressure(window_seconds=300, bottleneck="normal")
    decision = SlaDecisionEngine(_cfg()).decide(pool, pressure)
    assert decision.pressure_level == "inventory_hot_low"
    assert decision.desired_active_slots == 3
    assert decision.evidence["cold_start_cap"] == 3


