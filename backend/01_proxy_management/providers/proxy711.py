from __future__ import annotations

from importlib import import_module
import uuid
from dataclasses import dataclass
from urllib.parse import quote

_models = import_module("00_infrastructure.config.models")
ProxyConfig = _models.ProxyConfig
ProxyRouteConfig = _models.ProxyRouteConfig
clock = import_module("00_infrastructure.orchestration.scheduler_clock")
route_key_from_route = import_module("00_infrastructure.orchestration.route_health.route_key").route_key_from_route


@dataclass
class ProxyMaterial:
    provider: str
    proxy_type: str
    country: str
    asn: str
    username: str
    password: str
    host: str
    port: int
    proxy_url: str
    session_id: str


def build_711_proxy(config: ProxyConfig, route: ProxyRouteConfig) -> ProxyMaterial:
    provider = config.provider
    session_id = uuid.uuid4().hex[:8]
    username = f"{provider.account}-zone-custom-region-{route.country}"
    if route.asn:
        username += f"-asn-{route.asn}"
    if provider.sticky_session:
        username += f"-session-{session_id}-sessTime-{provider.sticky_minutes}"
    proxy_type = route.proxy_type or provider.default_type
    proxy_url = f"{proxy_type}://{quote(username, safe='')}:{quote(provider.password, safe='')}@{provider.host}:{provider.port}"
    return ProxyMaterial(
        provider=provider.name,
        proxy_type=proxy_type,
        country=route.country,
        asn=route.asn,
        username=username,
        password=provider.password,
        host=provider.host,
        port=provider.port,
        proxy_url=proxy_url,
        session_id=session_id,
    )


def choose_route(config: ProxyConfig, index: int = 0) -> ProxyRouteConfig:
    weighted: list[ProxyRouteConfig] = []
    for route in config.routes:
        weighted.extend([route] * max(1, route.weight))
    if not weighted:
        raise RuntimeError("no proxy routes configured")
    return weighted[index % len(weighted)]


def _route_health_penalty(rec: dict) -> float:
    penalty = 0.0
    try:
        penalty += min(0.30, max(0, int(rec.get("consecutive_cf_challenges") or 0)) * 0.015)
        penalty += min(0.20, max(0, int(rec.get("consecutive_rate_429") or 0)) * 0.04)
        penalty += min(0.20, max(0, int(rec.get("consecutive_network_errors") or 0)) * 0.05)
        penalty += min(0.35, max(0, int(rec.get("consecutive_hard_blocks") or 0)) * 0.20)
    except Exception:
        penalty = 0.0
    return min(0.60, penalty)


def _stage_score_for_route(stage_score_snapshot: dict | None, key: str) -> dict:
    if not isinstance(stage_score_snapshot, dict) or not bool(stage_score_snapshot.get("enabled", True)):
        return {}
    routes = stage_score_snapshot.get("routes") if isinstance(stage_score_snapshot.get("routes"), dict) else {}
    rec = routes.get(key)
    return rec if isinstance(rec, dict) else {}


def _choose_scored_candidate(candidates: list[dict], index: int) -> dict:
    if not candidates:
        raise RuntimeError("no candidate route")
    # Enforce route diversity before scoring when the scheduler reports an
    # active-load cap.  A route with one recent success (for example SG) must
    # not monopolize all cold starts: that pattern caused simultaneous
    # waiting-room/login traffic on the same provider route and then 429/1015.
    capped = [c for c in candidates if float(c.get("load_penalty") or 0) <= 0]
    if capped:
        candidates = capped
    best = max(float(c.get("final_score") or 0) for c in candidates)
    # Balance close-quality routes so one recent lucky route does not receive
    # all slots.  Routes outside the band are avoided until their recent score
    # improves or the better routes enter cooldown.
    band = [c for c in candidates if float(c.get("final_score") or 0) >= best - 0.06]
    band.sort(key=lambda c: (-float(c.get("final_score") or 0), int(c.get("offset") or 0), str(c.get("route_key") or "")))
    return band[index % len(band)]


def choose_route_with_health(
    config: ProxyConfig,
    index: int = 0,
    route_health_state: dict | None = None,
    stage_score_snapshot: dict | None = None,
    route_load_snapshot: dict | None = None,
) -> tuple[ProxyRouteConfig, dict]:
    weighted: list[ProxyRouteConfig] = []
    for route in config.routes:
        weighted.extend([route] * max(1, route.weight))
    if not weighted:
        raise RuntimeError("no proxy routes configured")

    routes_state = (route_health_state or {}).get("routes") if isinstance(route_health_state, dict) else {}
    routes_state = routes_state if isinstance(routes_state, dict) else {}
    skipped: list[dict] = []
    candidates: list[dict] = []
    for offset in range(len(weighted)):
        route = weighted[(index + offset) % len(weighted)]
        key = route_key_from_route(route, getattr(config.provider, "default_type", ""))
        rec = routes_state.get(key) if isinstance(routes_state.get(key), dict) else {}
        cooldown_until = str(rec.get("cooldown_until") or "")
        wait = clock.seconds_until(cooldown_until)
        if wait > 0:
            skipped.append({"route_key": key, "cooldown_until": cooldown_until, "wait_seconds": round(wait, 1)})
            continue
        score_rec = _stage_score_for_route(stage_score_snapshot, key)
        base_score = float(score_rec.get("score") or 0.225)
        health_penalty = _route_health_penalty(rec)
        # Weighted duplicated routes should still have a tiny rotation nudge,
        # but not enough to beat clear recent evidence.
        rotation_nudge = max(0.0, 0.012 - (offset * 0.001))
        load_snapshot = route_load_snapshot if isinstance(route_load_snapshot, dict) else {}
        counts = load_snapshot.get("counts") if isinstance(load_snapshot.get("counts"), dict) else {}
        active_total = max(0, int(load_snapshot.get("active_total") or 0))
        max_share = float(load_snapshot.get("max_share") or 1.0)
        current_count = int(counts.get(key) or 0)
        projected_share = (current_count + 1) / max(1, active_total + 1)
        load_penalty = 0.0
        if 0 < max_share < 1.0 and active_total >= 1 and projected_share > max_share:
            load_penalty = min(0.45, (projected_share - max_share) * 1.20)
        final_score = max(0.001, min(0.999, base_score - health_penalty - load_penalty + rotation_nudge))
        candidates.append({
            "route": route,
            "route_key": key,
            "offset": offset,
            "base_score": round(base_score, 4),
            "health_penalty": round(health_penalty, 4),
            "load_penalty": round(load_penalty, 4),
            "active_route_count": current_count,
            "projected_share": round(projected_share, 4),
            "rotation_nudge": round(rotation_nudge, 4),
            "final_score": round(final_score, 4),
            "stage_score": score_rec,
        })

    if candidates and isinstance(stage_score_snapshot, dict) and bool(stage_score_snapshot.get("enabled", True)):
        chosen = _choose_scored_candidate(candidates, index)
        debug_candidates = [
            {
                "route_key": c.get("route_key"),
                "offset": c.get("offset"),
                "base_score": c.get("base_score"),
                "health_penalty": c.get("health_penalty"),
                "load_penalty": c.get("load_penalty"),
                "active_route_count": c.get("active_route_count"),
                "final_score": c.get("final_score"),
                "observations": (c.get("stage_score") or {}).get("observations"),
                "last_success_at": (c.get("stage_score") or {}).get("last_success_at"),
            }
            for c in sorted(candidates, key=lambda x: -float(x.get("final_score") or 0))[:6]
        ]
        return chosen["route"], {
            "route_key": chosen["route_key"],
            "route_index_offset": chosen["offset"],
            "skipped_cooling": skipped,
            "all_routes_cooling": False,
            "selection_strategy": "recent_stage_score",
            "route_score": {
                "base_score": chosen["base_score"],
                "health_penalty": chosen["health_penalty"],
                "load_penalty": chosen.get("load_penalty", 0),
                "rotation_nudge": chosen["rotation_nudge"],
                "final_score": chosen["final_score"],
                "reason": (chosen.get("stage_score") or {}).get("reason", "no_recent_evidence_neutral_score"),
                "windows": (stage_score_snapshot or {}).get("windows", []),
                "last_success_at": (chosen.get("stage_score") or {}).get("last_success_at", ""),
                "observations": (chosen.get("stage_score") or {}).get("observations", 0),
                "candidate_preview": debug_candidates,
            },
        }

    if candidates:
        chosen = candidates[0]
        return chosen["route"], {
            "route_key": chosen["route_key"],
            "route_index_offset": chosen["offset"],
            "skipped_cooling": skipped,
            "all_routes_cooling": False,
            "selection_strategy": "cooldown_aware_round_robin",
        }

    route = weighted[index % len(weighted)]
    key = route_key_from_route(route, getattr(config.provider, "default_type", ""))
    return route, {"route_key": key, "route_index_offset": 0, "skipped_cooling": skipped, "all_routes_cooling": True}
