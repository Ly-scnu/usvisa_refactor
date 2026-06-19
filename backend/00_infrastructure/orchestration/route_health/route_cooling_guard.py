from __future__ import annotations

from typing import Any

from .route_key import route_key_from_route
from .. import scheduler_clock as clock


def routes_cooling_snapshot(config: Any, store: Any) -> dict[str, Any]:
    """Return a normalized view of configured proxy route cooldowns.

    This guard is intentionally infrastructure-level because both the producer
    loop and the API/SLA dashboard need the same answer:

    - producer: do not expand slots while every route is cooling;
    - dashboard: do not show a misleading "desired 10 slots" strategy when the
      real bottleneck is route cooldown.
    """

    try:
        route_state = (store.route_health() or {}).get("routes") or {}
        route_state = route_state if isinstance(route_state, dict) else {}
        rows: list[dict[str, Any]] = []
        routes = list(getattr(getattr(config, "proxy", None), "routes", []) or [])
        default_type = getattr(getattr(getattr(config, "proxy", None), "provider", None), "default_type", "")
        for route in routes:
            key = route_key_from_route(route, default_type)
            rec = route_state.get(key) if isinstance(route_state.get(key), dict) else {}
            cooldown_until = str(rec.get("cooldown_until") or "")
            wait = clock.seconds_until(cooldown_until)
            rows.append(
                {
                    "route_key": key,
                    "cooldown_until": cooldown_until,
                    "wait_seconds": round(wait, 1),
                    "cooling": wait > 0,
                    "consecutive_network_errors": int(rec.get("consecutive_network_errors") or 0),
                    "consecutive_hard_blocks": int(rec.get("consecutive_hard_blocks") or 0),
                    "consecutive_cf_challenges": int(rec.get("consecutive_cf_challenges") or 0),
                    "consecutive_rate_429": int(rec.get("consecutive_rate_429") or 0),
                    "last_outcome": str(rec.get("last_outcome") or ""),
                    "cooldown_reason": str(rec.get("cooldown_reason") or ""),
                    "last_recovered_at": str(rec.get("last_recovered_at") or rec.get("repaired_at") or ""),
                    "last_reason": str(rec.get("last_reason") or rec.get("cooldown_reason") or ""),
                    "last_event_at": str(rec.get("last_event_at") or ""),
                }
            )
        waits = [float(x.get("wait_seconds") or 0.0) for x in rows]
        all_cooling = bool(rows) and all(wait > 0 for wait in waits)
        min_wait = min([wait for wait in waits if wait > 0] or [0.0])
        max_wait = max(waits or [0.0])
        return {
            "all_routes_cooling": all_cooling,
            "min_wait_seconds": round(min_wait, 1),
            "max_wait_seconds": round(max_wait, 1),
            "routes": rows,
            "route_count": len(rows),
            "cooling_count": sum(1 for wait in waits if wait > 0),
        }
    except Exception as exc:
        return {
            "all_routes_cooling": False,
            "min_wait_seconds": 0.0,
            "max_wait_seconds": 0.0,
            "routes": [],
            "route_count": 0,
            "cooling_count": 0,
            "error": repr(exc),
        }
