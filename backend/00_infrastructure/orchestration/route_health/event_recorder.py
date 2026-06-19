from __future__ import annotations

from typing import Any

from .circuit_breaker import RouteCircuitBreaker
from .route_key import route_key_from_material
from .scoreboard import RouteScoreboard


def _payload_state(payload: dict[str, Any]) -> str:
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    return str(state.get("stage") or state.get("reason") or "").lower()


def _classify(stage_name: str, result: Any) -> str:
    """Classify route outcome from trusted current-stage evidence only.

    Do NOT scan the entire payload.  ``proxy_acquire`` payload can contain
    route_health.skipped_cooling with old 1015/429/access_denied text; scanning
    it caused successful proxy acquisition to poison the current route.
    """
    payload = getattr(result, "payload", {}) or {}
    payload = payload if isinstance(payload, dict) else {}
    ok = bool(getattr(result, "ok", False))
    message = str(getattr(result, "message", "") or "").lower()
    needs = str(payload.get("needs_recover") or payload.get("reason") or "").lower()
    current_state = _payload_state(payload)
    trusted = " ".join([message, needs, current_state]).lower()

    if stage_name == "proxy_acquire":
        # Proxy acquisition success only means the proxy URL/material was
        # produced.  It is not an official-site response and must never be
        # classified as 1015/429/access_denied based on embedded route metadata.
        if ok:
            return ""
        if any(x in trusted for x in ("network_error", "err_proxy", "err_tunnel", "proxy_error")):
            return "network_error"
        return ""

    if stage_name == "business_query" and ok and bool(payload.get("valid_query_success")):
        return "success"

    if ok:
        # Successful non-business stages should not count as route failures.
        return ""

    if needs == "account_login_blocked" or "account_login_blocked" in trusted:
        # Account-level login ban is not evidence that the current proxy route
        # is bad.  Penalising routes here makes the selector churn through more
        # IPs while the account itself is refusing login.
        return ""

    if needs in {"rate_limit_1015", "ban_1015"} or "1015" in trusted or "rate_limit_1015" in trusted or "ban_1015" in trusted:
        return "ban_1015"
    if needs in {"access_denied", "1020"} or "1020" in trusted or "access_denied" in trusted or "access denied" in trusted:
        return "access_denied"
    if needs in {"rate_limit_429", "rate_limited"} or "429" in trusted or "rate_limit_429" in trusted or "rate_limited" in trusted:
        return "rate_limit_429"
    if needs == "network_error" or "network_error" in trusted or "net::" in trusted or "err_tunnel" in trusted or "err_proxy" in trusted:
        return "network_error"
    if needs in {"cf_challenge", "auth_or_cf", "auth_or_cf_block"} or "cf_challenge" in trusted or "cloudflare" in trusted or "turnstile" in trusted:
        return "cf_challenge"
    return ""


def record_stage_result(store: Any, proxy_material: Any, stage_name: str, result: Any) -> dict[str, Any] | None:
    if not store or proxy_material is None:
        return None
    key = route_key_from_material(proxy_material)
    if not key:
        return None
    outcome = _classify(stage_name, result)
    if not outcome:
        return None
    detail = {
        "stage": stage_name,
        "ok": bool(getattr(result, "ok", False)),
        "message": str(getattr(result, "message", ""))[:300],
    }
    scoreboard = RouteScoreboard()
    breaker = RouteCircuitBreaker()

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        rec = scoreboard.update(state, key, outcome, detail=detail)
        breaker.apply(rec, outcome)
        state.setdefault("events", [])
        state["events"] = (state["events"] + [{**detail, "route_key": key, "outcome": outcome}])[-200:]
        return {"route_key": key, "outcome": outcome, "record": dict(rec)}

    return store.mutate_route_health(mutate)
