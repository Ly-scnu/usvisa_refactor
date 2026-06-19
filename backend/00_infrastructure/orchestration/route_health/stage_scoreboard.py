from __future__ import annotations

from collections import defaultdict
from importlib import import_module
from typing import Any

clock = import_module("00_infrastructure.orchestration.scheduler_clock")
route_key_from_route = import_module("00_infrastructure.orchestration.route_health.route_key").route_key_from_route


DEFAULT_WINDOWS_SECONDS = [600, 1800, 3600]
DEFAULT_WINDOW_WEIGHTS = [0.50, 0.35, 0.15]
DEFAULT_TAIL_LIMIT = 8000
PRIOR_TOTAL = 4.0
PRIOR_SUCCESS = 0.9


def _as_float_list(values: Any, fallback: list[float]) -> list[float]:
    if not isinstance(values, (list, tuple)) or not values:
        return list(fallback)
    out: list[float] = []
    for item in values:
        try:
            out.append(float(item))
        except Exception:
            pass
    return out or list(fallback)


def _as_int_list(values: Any, fallback: list[int]) -> list[int]:
    if not isinstance(values, (list, tuple)) or not values:
        return list(fallback)
    out: list[int] = []
    for item in values:
        try:
            n = int(float(item))
            if n > 0:
                out.append(n)
        except Exception:
            pass
    return out or list(fallback)


def _normalize_weights(weights: list[float], count: int) -> list[float]:
    vals = list(weights[:count])
    while len(vals) < count:
        vals.append(0.0)
    total = sum(x for x in vals if x > 0)
    if total <= 0:
        return [1.0 / max(1, count)] * count
    return [(x if x > 0 else 0.0) / total for x in vals]


def _configured_route_keys(config: Any) -> list[str]:
    proxy = getattr(config, "proxy", config)
    provider = getattr(proxy, "provider", None)
    default_type = getattr(provider, "default_type", "") or ""
    keys: list[str] = []
    for route in getattr(proxy, "routes", []) or []:
        key = route_key_from_route(route, default_type)
        if key and key not in keys:
            keys.append(key)
    return keys


def _event_ts(row: dict[str, Any]) -> Any:
    return row.get("created_at") or row.get("ts") or (row.get("payload") if isinstance(row.get("payload"), dict) else {}).get("ts")


def _route_health_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict) or row.get("event_type") != "route_health_update":
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None
    route_key = str(payload.get("route_key") or "")
    outcome = str(payload.get("outcome") or "")
    if not route_key or not outcome:
        return None
    return payload


def _empty_stat() -> dict[str, Any]:
    return {
        "total": 0,
        "success": 0,
        "failure": 0,
        "outcomes": {},
        "stages": {},
    }


def _add_stat(stat: dict[str, Any], *, stage: str, outcome: str) -> None:
    stat["total"] = int(stat.get("total") or 0) + 1
    if outcome == "success":
        stat["success"] = int(stat.get("success") or 0) + 1
    else:
        stat["failure"] = int(stat.get("failure") or 0) + 1
    outcomes = stat.setdefault("outcomes", {})
    outcomes[outcome] = int(outcomes.get(outcome) or 0) + 1
    stages = stat.setdefault("stages", {})
    s = stages.setdefault(stage or "unknown", _empty_stat())
    s["total"] = int(s.get("total") or 0) + 1
    if outcome == "success":
        s["success"] = int(s.get("success") or 0) + 1
    else:
        s["failure"] = int(s.get("failure") or 0) + 1
    so = s.setdefault("outcomes", {})
    so[outcome] = int(so.get(outcome) or 0) + 1


def _success_rate(stat: dict[str, Any]) -> float:
    total = float(stat.get("total") or 0)
    success = float(stat.get("success") or 0)
    # Bayesian smoothing prevents one lucky/one bad event from swinging all
    # slots to the same route.  With no evidence the route is neutral, not dead.
    return (success + PRIOR_SUCCESS) / (total + PRIOR_TOTAL)


def _outcome_rate(stat: dict[str, Any], names: set[str]) -> float:
    total = float(stat.get("total") or 0)
    if total <= 0:
        return 0.0
    outcomes = stat.get("outcomes") if isinstance(stat.get("outcomes"), dict) else {}
    return sum(float(outcomes.get(name) or 0) for name in names) / total


def _failure_penalty(stat: dict[str, Any]) -> float:
    if float(stat.get("total") or 0) <= 0:
        return 0.0
    stages = stat.get("stages") if isinstance(stat.get("stages"), dict) else {}
    login_stat = stages.get("login") if isinstance(stages.get("login"), dict) else {}
    business_stat = stages.get("business_query") if isinstance(stages.get("business_query"), dict) else {}
    hard = _outcome_rate(stat, {"ban_1015", "access_denied"})
    rate = _outcome_rate(stat, {"rate_limit_429"})
    network = _outcome_rate(stat, {"network_error"})
    cf = _outcome_rate(stat, {"cf_challenge"})
    login_cf = _outcome_rate(login_stat, {"cf_challenge"})
    business_cf = _outcome_rate(business_stat, {"cf_challenge"})
    return min(
        0.45,
        hard * 0.28
        + rate * 0.12
        + network * 0.10
        + cf * 0.06
        + login_cf * 0.06
        + business_cf * 0.08,
    )


def _last_success_bonus(last_success_at: str) -> float:
    dt = clock.parse_ts(last_success_at)
    if not dt:
        return 0.0
    age = max(0.0, (clock.now_dt() - dt).total_seconds())
    if age <= 600:
        return 0.07
    if age <= 1800:
        return 0.04
    if age <= 3600:
        return 0.02
    return 0.0


def build_recent_stage_scores_from_events(
    events: list[dict[str, Any]],
    *,
    route_keys: list[str] | None = None,
    windows_seconds: list[int] | None = None,
    window_weights: list[float] | None = None,
    now: Any | None = None,
) -> dict[str, Any]:
    """Build a route score snapshot from recent route-health events.

    This module is intentionally read-only: it observes events and returns a
    snapshot.  It does not mutate persistent route_health and does not decide
    scheduler capacity; those remain separate responsibilities.
    """
    now_dt = now or clock.now_dt()
    windows = _as_int_list(windows_seconds, DEFAULT_WINDOWS_SECONDS)
    weights = _normalize_weights(_as_float_list(window_weights, DEFAULT_WINDOW_WEIGHTS), len(windows))
    keys = list(route_keys or [])

    by_route: dict[str, dict[int, dict[str, Any]]] = defaultdict(lambda: {w: _empty_stat() for w in windows})
    last_event_at: dict[str, str] = {}
    last_success_at: dict[str, str] = {}

    for row in events or []:
        payload = _route_health_payload(row)
        if not payload:
            continue
        ts = clock.parse_ts(_event_ts(row))
        if not ts:
            continue
        age = (now_dt - ts).total_seconds()
        if age < 0:
            age = 0
        route_key = str(payload.get("route_key") or "")
        stage = str(payload.get("stage") or "unknown")
        outcome = str(payload.get("outcome") or "")
        if route_key not in keys:
            keys.append(route_key)
        last_event_at[route_key] = ts.isoformat(timespec="seconds")
        if outcome == "success":
            last_success_at[route_key] = ts.isoformat(timespec="seconds")
        for window in windows:
            if age <= window:
                _add_stat(by_route[route_key][window], stage=stage, outcome=outcome)

    routes: dict[str, Any] = {}
    for key in keys:
        window_rows: dict[str, Any] = {}
        weighted_rate = 0.0
        weighted_penalty = 0.0
        observations = 0
        for window, weight in zip(windows, weights):
            stat = by_route[key][window]
            rate = _success_rate(stat)
            penalty = _failure_penalty(stat)
            weighted_rate += rate * weight
            weighted_penalty += penalty * weight
            observations += int(stat.get("total") or 0)
            window_rows[str(window)] = {
                "seconds": window,
                "weight": round(weight, 3),
                "total": int(stat.get("total") or 0),
                "success": int(stat.get("success") or 0),
                "failure": int(stat.get("failure") or 0),
                "success_rate": round(rate, 4),
                "failure_penalty": round(penalty, 4),
                "outcomes": dict(stat.get("outcomes") or {}),
                "stages": dict(stat.get("stages") or {}),
            }
        score = max(0.01, min(0.99, weighted_rate - weighted_penalty + _last_success_bonus(last_success_at.get(key, ""))))
        routes[key] = {
            "route_key": key,
            "score": round(score, 4),
            "weighted_success_rate": round(weighted_rate, 4),
            "weighted_failure_penalty": round(weighted_penalty, 4),
            "observations": observations,
            "last_event_at": last_event_at.get(key, ""),
            "last_success_at": last_success_at.get(key, ""),
            "windows": window_rows,
            "reason": f"10/30/60近因加权：成功率{weighted_rate:.2f} - 风险{weighted_penalty:.2f}",
        }

    return {
        "enabled": True,
        "generated_at": clock.now_iso(),
        "windows": [{"seconds": w, "weight": round(weight, 3)} for w, weight in zip(windows, weights)],
        "routes": routes,
    }


def build_recent_stage_scores(store: Any, config: Any | None = None) -> dict[str, Any]:
    smart = getattr(config, "smart_orchestrator", None) if config is not None else None
    if smart is not None and not bool(getattr(smart, "route_score_enabled", True)):
        return {"enabled": False, "routes": {}, "reason": "route_score_disabled"}
    tail_limit = int(getattr(smart, "route_score_event_tail_limit", DEFAULT_TAIL_LIMIT) or DEFAULT_TAIL_LIMIT) if smart is not None else DEFAULT_TAIL_LIMIT
    windows = _as_int_list(getattr(smart, "route_score_window_seconds", None), DEFAULT_WINDOWS_SECONDS) if smart is not None else DEFAULT_WINDOWS_SECONDS
    weights = _as_float_list(getattr(smart, "route_score_window_weights", None), DEFAULT_WINDOW_WEIGHTS) if smart is not None else DEFAULT_WINDOW_WEIGHTS
    events = store.events_tail(tail_limit) if store is not None else []
    return build_recent_stage_scores_from_events(
        events,
        route_keys=_configured_route_keys(config) if config is not None else [],
        windows_seconds=windows,
        window_weights=weights,
    )
