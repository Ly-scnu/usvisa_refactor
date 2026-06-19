from importlib import import_module
from types import SimpleNamespace
from datetime import timedelta


StageResult = import_module("05_stage_components.base").StageResult
event_recorder = import_module("00_infrastructure.orchestration.route_health.event_recorder")
maintenance = import_module("00_infrastructure.orchestration.route_health.maintenance")
TerminationPolicy = import_module("00_infrastructure.orchestration.session_health.termination_policy").TerminationPolicy
RouteScoreboard = import_module("00_infrastructure.orchestration.route_health.scoreboard").RouteScoreboard
RouteCircuitBreaker = import_module("00_infrastructure.orchestration.route_health.circuit_breaker").RouteCircuitBreaker
clock = import_module("00_infrastructure.orchestration.scheduler_clock")
SlaDecision = import_module("00_infrastructure.orchestration.sla.models").SlaDecision
RouteRecoveryRampPolicy = import_module("00_infrastructure.orchestration.sla.route_recovery_ramp").RouteRecoveryRampPolicy


def test_proxy_acquire_success_does_not_scan_embedded_route_health_text():
    result = StageResult(
        True,
        "proxy_acquire",
        "proxy acquired",
        {
            "route_health": {
                "skipped_cooling": [
                    {"route_key": "JP:socks5:ASN2516", "cooldown_reason": "ban_1015 rate_limit_429 access_denied"}
                ]
            }
        },
    )
    assert event_recorder._classify("proxy_acquire", result) == ""


def test_business_query_success_records_success():
    result = StageResult(True, "business_query", "query ok", {"valid_query_success": True, "days": ["2026-08-26"]})
    assert event_recorder._classify("business_query", result) == "success"


def test_business_query_success_feedback_resets_cf_storm_route_health():
    class Store:
        def __init__(self):
            self.state = {
                "routes": {
                    "JP:socks5:ASN2516": {
                        "route_key": "JP:socks5:ASN2516",
                        "last_outcome": "cf_challenge",
                        "consecutive_cf_challenges": 99,
                        "cooldown_until": "2099-01-01T00:00:00+08:00",
                        "cooldown_reason": "cf_challenge_storm",
                    }
                }
            }

        def mutate_route_health(self, mutator):
            return mutator(self.state)

    store = Store()
    material = SimpleNamespace(country="JP", proxy_type="socks5", asn="ASN2516")
    result = StageResult(True, "business_query", "business days query success", {"valid_query_success": True, "days": ["2026-09-17"]})
    out = event_recorder.record_stage_result(store, material, "business_query", result)
    rec = store.state["routes"]["JP:socks5:ASN2516"]
    assert out["outcome"] == "success"
    assert rec["success_count"] == 1
    assert rec["consecutive_cf_challenges"] == 0
    assert rec["cooldown_until"] == ""
    assert rec["cooldown_reason"] == ""


def test_failed_to_fetch_without_previous_success_fast_recycles():
    result = StageResult(
        False,
        "business_query",
        "business probe exception: Error: Page.evaluate: TypeError: Failed to fetch",
        {"needs_recover": "business_exception"},
        retryable=True,
    )
    decision = TerminationPolicy().decide(result, successful_queries=0, consecutive_kind_failures=1)
    assert decision.terminal is True
    assert decision.action == "fast_recycle"
    assert decision.error_type == "failed_to_fetch"


def test_polluted_proxy_acquire_route_health_can_be_repaired():
    state = {
        "routes": {
            "JP:socks5:ASN2516": {
                "route_key": "JP:socks5:ASN2516",
                "last_outcome": "ban_1015",
                "last_detail": {"stage": "proxy_acquire", "ok": True, "message": "proxy acquired"},
                "cooldown_until": "2026-06-16T09:42:05+08:00",
                "cooldown_reason": "hard_block_streak",
                "consecutive_hard_blocks": 9,
            }
        }
    }
    repaired, changes = maintenance.repair_polluted_route_health_state(state)
    rec = repaired["routes"]["JP:socks5:ASN2516"]
    assert changes
    assert rec["cooldown_until"] == ""
    assert rec["consecutive_hard_blocks"] == 0
    assert rec["last_outcome"] == "repaired_proxy_acquire_pollution"


def test_cf_challenge_does_not_extend_stale_hard_route_cooldown():
    rec = {
        "route_key": "JP:socks5:ASN2516",
        "consecutive_hard_blocks": 5,
        "cooldown_reason": "hard_block_streak",
        "cooldown_until": "2099-01-01T00:00:00+08:00",
    }
    state = {"routes": {"JP:socks5:ASN2516": rec}}
    updated = RouteScoreboard().update(state, "JP:socks5:ASN2516", "cf_challenge", detail={"stage": "login"})
    before = updated.get("cooldown_until")
    RouteCircuitBreaker().apply(updated, "cf_challenge")
    assert updated["consecutive_hard_blocks"] == 0
    assert updated["consecutive_network_errors"] == 0
    assert updated.get("cooldown_until") == before


def test_cf_challenge_storm_opens_soft_route_cooldown():
    rec = {
        "route_key": "JP:socks5:ASN2516",
        "consecutive_cf_challenges": 19,
        "cooldown_until": "",
        "cooldown_reason": "",
    }
    state = {"routes": {"JP:socks5:ASN2516": rec}}
    updated = RouteScoreboard().update(state, "JP:socks5:ASN2516", "cf_challenge", detail={"stage": "login"})
    RouteCircuitBreaker().apply(updated, "cf_challenge")
    assert updated["consecutive_cf_challenges"] == 20
    assert updated["cooldown_reason"] == "cf_challenge_storm"
    assert int(updated["cooldown_seconds"]) == 180


def test_soft_cf_hard_cooldown_can_be_repaired():
    state = {
        "routes": {
            "JP:socks5:ASN2516": {
                "route_key": "JP:socks5:ASN2516",
                "last_outcome": "cf_challenge",
                "cooldown_until": "2099-01-01T00:00:00+08:00",
                "cooldown_reason": "hard_block_streak",
                "consecutive_hard_blocks": 9,
            }
        }
    }
    repaired, changes = maintenance.repair_soft_cf_hard_cooldowns_state(state)
    rec = repaired["routes"]["JP:socks5:ASN2516"]
    assert changes
    assert rec["cooldown_until"] == ""
    assert rec["cooldown_reason"] == ""
    assert rec["consecutive_hard_blocks"] == 0


def test_route_recovery_ramp_caps_desired_slots_after_cooling_expires():
    cfg = SimpleNamespace(
        smart_orchestrator=SimpleNamespace(
            route_recovery_ramp_enabled=True,
            route_recovery_ramp_window_seconds=240.0,
            route_recovery_ramp_step_seconds=30.0,
            route_recovery_ramp_initial_slots=3,
            route_recovery_ramp_max_slots=6,
            route_recovery_ramp_peak_max_slots=8,
        )
    )
    recovered_at = (clock.now_dt() - timedelta(seconds=20)).isoformat(timespec="seconds")
    route_cooling = {
        "all_routes_cooling": False,
        "routes": [{"route_key": "JP:socks5:ASN2516", "cooldown_until": recovered_at, "wait_seconds": 0, "cooling": False}],
    }
    decision = SlaDecision(desired_active_slots=8, min_slots=2, max_slots=10, pressure_level="inventory_hot_low", peak_mode={"mode": "normal"})
    decision, ramp = RouteRecoveryRampPolicy(cfg).apply(decision, route_cooling, active_slots=2)
    assert ramp.active is True
    assert decision.desired_active_slots == 3
    assert decision.pressure_level.endswith("_ramped")
