from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from ...utils.jsonio import atomic_write_json, load_json
from ...utils.time import iso_now


POLLUTED_OUTCOMES = {"ban_1015", "rate_limit_429", "access_denied", "network_error"}


def is_polluted_proxy_acquire_record(rec: dict[str, Any]) -> bool:
    """True for old records caused by scanning route_health metadata.

    Evidence: events showed ``stage=proxy_acquire``, ``ok=True``,
    ``message=proxy acquired`` recorded as 1015/429/access_denied.  That is not
    a real official response and should not keep a route in cooldown.
    """
    detail = rec.get("last_detail") if isinstance(rec.get("last_detail"), dict) else {}
    return (
        str(detail.get("stage") or "") == "proxy_acquire"
        and bool(detail.get("ok")) is True
        and str(detail.get("message") or "") == "proxy acquired"
        and str(rec.get("last_outcome") or "") in POLLUTED_OUTCOMES
    )


def repair_polluted_route_health_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repaired = deepcopy(state or {})
    routes = repaired.get("routes") if isinstance(repaired.get("routes"), dict) else {}
    changes: list[dict[str, Any]] = []
    for key, rec in list(routes.items()):
        if not isinstance(rec, dict) or not is_polluted_proxy_acquire_record(rec):
            continue
        before = {
            "route_key": key,
            "last_outcome": rec.get("last_outcome"),
            "cooldown_until": rec.get("cooldown_until"),
            "cooldown_reason": rec.get("cooldown_reason"),
            "consecutive_hard_blocks": rec.get("consecutive_hard_blocks"),
            "consecutive_network_errors": rec.get("consecutive_network_errors"),
        }
        rec["last_outcome"] = "repaired_proxy_acquire_pollution"
        rec["cooldown_until"] = ""
        rec["cooldown_reason"] = ""
        rec["cooldown_seconds"] = 0
        rec["consecutive_hard_blocks"] = 0
        rec["consecutive_network_errors"] = 0
        rec["repaired_at"] = iso_now()
        rec["last_recovered_at"] = rec["repaired_at"]
        rec["repair_reason"] = "proxy_acquire ok=True was previously misclassified by payload text scan"
        changes.append(before)
    if changes:
        repaired["updated_at"] = iso_now()
        repaired.setdefault("repairs", [])
        repaired["repairs"] = (repaired["repairs"] + [{"at": iso_now(), "changes": changes}])[-50:]
    return repaired, changes


def repair_soft_cf_hard_cooldowns_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Clear stale hard cooldowns whose latest evidence is recoverable.

    Older logic used ``consecutive_hard_blocks >= 1`` regardless of the current
    outcome.  After a real 1015/access_denied, later ``cf_challenge`` events
    from in-flight browsers could keep pushing the same route 30 minutes out.
    Those latest records are soft/recoverable and should not hold a hard route
    ban.
    """

    repaired = deepcopy(state or {})
    routes = repaired.get("routes") if isinstance(repaired.get("routes"), dict) else {}
    changes: list[dict[str, Any]] = []
    soft_outcomes = {"cf_challenge", "rate_limit_429", "network_error", "success"}
    for key, rec in list(routes.items()):
        if not isinstance(rec, dict):
            continue
        if str(rec.get("cooldown_reason") or "") != "hard_block_streak":
            continue
        if str(rec.get("last_outcome") or "") not in soft_outcomes:
            continue
        before = {
            "route_key": key,
            "last_outcome": rec.get("last_outcome"),
            "cooldown_until": rec.get("cooldown_until"),
            "cooldown_reason": rec.get("cooldown_reason"),
            "consecutive_hard_blocks": rec.get("consecutive_hard_blocks"),
        }
        rec["cooldown_until"] = ""
        rec["cooldown_reason"] = ""
        rec["cooldown_seconds"] = 0
        rec["consecutive_hard_blocks"] = 0
        if str(rec.get("last_outcome") or "") in {"cf_challenge", "rate_limit_429", "success"}:
            rec["consecutive_network_errors"] = 0
        rec["repaired_at"] = iso_now()
        rec["last_recovered_at"] = rec["repaired_at"]
        rec["repair_reason"] = "latest route outcome is soft/recoverable; stale hard-block cooldown cleared"
        changes.append(before)
    if changes:
        repaired["updated_at"] = iso_now()
        repaired.setdefault("repairs", [])
        repaired["repairs"] = (repaired["repairs"] + [{"at": iso_now(), "kind": "soft_cf_hard_cooldown", "changes": changes}])[-50:]
    return repaired, changes


def repair_route_health_file(path: Path) -> list[dict[str, Any]]:
    data = load_json(path, {}) or {}
    if not isinstance(data, dict):
        return []
    repaired, changes = repair_polluted_route_health_state(data)
    repaired, soft_changes = repair_soft_cf_hard_cooldowns_state(repaired)
    changes = changes + soft_changes
    if not changes:
        return []
    backup = path.with_name(path.name + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    atomic_write_json(backup, data)
    atomic_write_json(path, repaired)
    return changes
