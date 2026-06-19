from __future__ import annotations

from dataclasses import asdict
from importlib import import_module

from ..base import StageResult

_proxy711 = import_module("01_proxy_management.providers.proxy711")
build_711_proxy = _proxy711.build_711_proxy
choose_route = _proxy711.choose_route
choose_route_with_health = _proxy711.choose_route_with_health
build_recent_stage_scores = import_module("00_infrastructure.orchestration.route_health.stage_scoreboard").build_recent_stage_scores
route_key_from_material = import_module("00_infrastructure.orchestration.route_health.route_key").route_key_from_material
SessionContext = import_module("02_session_context.context").SessionContext


class ProxyAcquireStage:
    stage_name = "proxy_acquire"

    def __init__(self, route_index: int = 0):
        self.route_index = route_index

    async def execute(self, ctx: SessionContext) -> StageResult:
        config = ctx.runtime_config
        try:
            route_health_state = ctx.store.route_health() if getattr(ctx, "store", None) else {}
        except Exception:
            route_health_state = {}
        try:
            score_snapshot = build_recent_stage_scores(ctx.store, config) if getattr(ctx, "store", None) else {}
        except Exception as exc:
            score_snapshot = {"enabled": False, "error": repr(exc), "routes": {}}
        route_load_snapshot = {}
        try:
            slots = ctx.store.read_slots() if getattr(ctx, "store", None) else {}
            counts: dict[str, int] = {}
            active_total = 0
            for slot in (slots or {}).values():
                if not isinstance(slot, dict) or str(slot.get("state") or "") != "running":
                    continue
                key = str(slot.get("route_key") or "")
                if not key:
                    continue
                active_total += 1
                counts[key] = counts.get(key, 0) + 1
            route_load_snapshot = {
                "counts": counts,
                "active_total": active_total,
                "max_share": float(getattr(getattr(config, "smart_orchestrator", None), "route_max_active_share", 0.70) or 0.70),
            }
        except Exception as exc:
            route_load_snapshot = {"error": repr(exc)}
        route, health_meta = choose_route_with_health(config.proxy, self.route_index, route_health_state, score_snapshot, route_load_snapshot)
        if score_snapshot.get("error"):
            health_meta.setdefault("route_score_error", score_snapshot.get("error"))
        if bool(health_meta.get("all_routes_cooling")):
            skipped = health_meta.get("skipped_cooling") if isinstance(health_meta.get("skipped_cooling"), list) else []
            wait = min([float(x.get("wait_seconds") or 0) for x in skipped if isinstance(x, dict) and float(x.get("wait_seconds") or 0) > 0] or [60.0])
            return StageResult(
                ok=False,
                stage=self.stage_name,
                message=f"all proxy routes cooling, wait {round(wait, 1)}s before next production attempt",
                payload={"needs_recover": "route_cooling", "reason": "route_cooling", "route_health": health_meta, "wait_seconds": wait, "fresh_round": True},
                retryable=False,
            )
        material = build_711_proxy(config.proxy, route)
        ctx.proxy = material.proxy_url
        ctx.proxy_material = material
        route_key = route_key_from_material(material)
        if getattr(ctx, "store", None):
            try:
                ctx.store.update_slot(
                    ctx.slot_id,
                    proxy_display=f"{material.country}/{material.asn or '-'}:{material.session_id}",
                    proxy_session=material.session_id,
                    route_key=route_key,
                    route=("/".join([material.country, material.asn]).strip("/")),
                )
            except Exception:
                pass
        return StageResult(
            ok=True,
            stage=self.stage_name,
            message="proxy acquired",
            payload={"proxy_display": f"{material.country}/{material.asn or '-'}:{material.session_id}", "proxy": asdict(material), "route_key": route_key, "route_health": health_meta},
        )
