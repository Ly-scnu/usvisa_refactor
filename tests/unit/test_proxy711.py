from pathlib import Path
from importlib import import_module
from datetime import timedelta

load_config = import_module("00_infrastructure.config.loader").load_config
_proxy711 = import_module("01_proxy_management.providers.proxy711")
build_711_proxy = _proxy711.build_711_proxy
choose_route = _proxy711.choose_route
choose_route_with_health = _proxy711.choose_route_with_health
clock = import_module("00_infrastructure.orchestration.scheduler_clock")
stage_scoreboard = import_module("00_infrastructure.orchestration.route_health.stage_scoreboard")


def test_build_711_proxy():
    cfg = load_config(Path(__file__).resolve().parents[2])
    mat = build_711_proxy(cfg.proxy, choose_route(cfg.proxy, 0))
    assert mat.proxy_url.startswith("socks5://")
    assert mat.proxy_type == "socks5"
    assert cfg.proxy.provider.host in mat.proxy_url
    assert "session" in mat.username


def _rh_event(route_key: str, stage: str, outcome: str, seconds_ago: int = 60):
    return {
        "event_type": "route_health_update",
        "created_at": (clock.now_dt() - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds"),
        "payload": {"route_key": route_key, "stage": stage, "outcome": outcome},
    }


def test_recent_stage_score_prefers_recent_successful_route():
    snapshot = stage_scoreboard.build_recent_stage_scores_from_events(
        [
            _rh_event("JP:socks5:ASN2516", "login", "cf_challenge", 30),
            _rh_event("JP:socks5:ASN2516", "login", "cf_challenge", 90),
            _rh_event("SG:socks5:", "business_query", "success", 40),
            _rh_event("SG:socks5:", "business_query", "success", 120),
            _rh_event("US:socks5:", "business_query", "cf_challenge", 50),
        ],
        route_keys=["JP:socks5:ASN2516", "SG:socks5:", "US:socks5:"],
    )
    assert snapshot["routes"]["SG:socks5:"]["score"] > snapshot["routes"]["JP:socks5:ASN2516"]["score"]
    assert snapshot["routes"]["SG:socks5:"]["score"] > snapshot["routes"]["US:socks5:"]["score"]


def test_choose_route_with_stage_score_skips_cooldown_and_prefers_score():
    cfg = load_config(Path(__file__).resolve().parents[2])
    snapshot = {
        "enabled": True,
        "windows": [{"seconds": 600, "weight": 0.5}],
        "routes": {
            "JP:socks5:ASN2516": {"score": 0.80, "observations": 5},
            "SG:socks5:": {"score": 0.55, "observations": 5},
            "US:socks5:": {"score": 0.20, "observations": 5},
        },
    }
    route, meta = choose_route_with_health(cfg.proxy, 0, {}, snapshot)
    assert route.country == "JP"
    assert meta["selection_strategy"] == "recent_stage_score"
    assert meta["route_score"]["final_score"] >= 0.7

    cooling = {
        "routes": {
            "JP:socks5:ASN2516": {
                "cooldown_until": (clock.now_dt() + timedelta(minutes=10)).isoformat(timespec="seconds"),
                "cooldown_reason": "hard_block_streak",
            }
        }
    }
    route2, meta2 = choose_route_with_health(cfg.proxy, 0, cooling, snapshot)
    assert route2.country != "JP"
    assert meta2["selection_strategy"] == "recent_stage_score"
    assert meta2["skipped_cooling"]


def test_choose_route_with_health_falls_back_without_stage_score():
    cfg = load_config(Path(__file__).resolve().parents[2])
    route, meta = choose_route_with_health(cfg.proxy, 0, {}, None)
    assert route == choose_route(cfg.proxy, 0)
    assert meta["selection_strategy"] == "cooldown_aware_round_robin"
