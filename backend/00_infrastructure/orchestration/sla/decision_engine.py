from __future__ import annotations

from typing import Any

from .models import EventPressure, SessionPoolStats, SlaDecision


class SlaDecisionEngine:
    """Convert pool + event pressure into desired slot count.

    The engine focuses on the user's SLA: produce one successful days query per
    configurable interval when possible.  It does not execute browser actions.
    """

    def __init__(self, config: Any):
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _bounds(self) -> tuple[int, int, int, int]:
        min_slots = max(2, int(getattr(self.cfg, "min_slots", 2) or 2))
        max_slots = max(min_slots, min(10, int(getattr(self.cfg, "max_slots", 10) or 10)))
        normal = max(min_slots, min(max_slots, int(getattr(self.cfg, "normal_active_slots", 3) or 3)))
        soft = max(normal, min(max_slots, int(getattr(self.cfg, "soft_active_slots", 4) or 4)))
        return min_slots, max_slots, normal, soft

    def decide(self, pool: SessionPoolStats, pressure: EventPressure) -> SlaDecision:
        min_slots, max_slots, normal, soft = self._bounds()
        active = max(0, int(pool.active_slots or 0))
        prewarm_s = max(5, int(getattr(self.cfg, "prewarm_window_seconds", 45) or 45))
        grace_s = max(0, int(getattr(self.cfg, "missed_target_grace_seconds", 30) or 30))
        stale_s = max(pool.target_interval_seconds + grace_s, float(getattr(self.cfg, "scale_up_stale_success_seconds", 180) or 180))
        critical_s = max(stale_s, float(getattr(self.cfg, "critical_stale_success_seconds", 600) or 600))
        cooldown_s = max(10.0, float(getattr(self.cfg, "scale_up_cooldown_seconds", 180) or 180))
        adaptive_cap = max(soft, min(max_slots, int(getattr(self.cfg, "adaptive_inventory_active_slots", 6) or 6)))
        severe_cap = max(adaptive_cap, min(max_slots, int(getattr(self.cfg, "severe_gap_active_slots", 8) or 8)))
        candidates = int(pool.hot_sessions or 0) + int(pool.query_wait_sessions or 0)
        desired = max(min_slots, min(active or min_slots, soft))
        level = "normal"
        reason = "SLA 正常保持：有基础槽位生产，未触发加压"

        missed = pool.seconds_since_success > (pool.target_interval_seconds + grace_s)
        very_stale = pool.seconds_since_success > critical_s
        near_target = pool.seconds_to_target <= prewarm_s
        insufficient_candidates = candidates < 1
        insufficient_hot_pool = (pool.hot_sessions + pool.cooling_sessions + pool.query_wait_sessions) < max(2, min(pool.needed_hot_sessions, soft))
        peak_mode = pool.peak_mode or {}
        peak_state = str(peak_mode.get("mode") or "normal")
        peak_desired = int(peak_mode.get("desired_active_slots") or 0)
        peak_min_hot = int(peak_mode.get("min_hot_sessions") or 0)
        inventory = pool.inventory or {}
        inventory_enabled = bool(inventory.get("enabled", True))
        inventory_deficit = int(inventory.get("total_deficit") or 0)
        inventory_floor = int(inventory.get("desired_inventory_slots") or 0)
        inventory_health = str(inventory.get("health_level") or "unknown")
        hot_supply = (
            int(inventory.get("hot_query_sessions") or 0)
            + int(inventory.get("ready_query_sessions") or 0)
            + int(inventory.get("cooling_success_sessions") or 0)
            + int(inventory.get("recovering_success_sessions") or 0)
        )
        cold_start_cap = max(min_slots, min(max_slots, int(getattr(self.cfg, "cold_start_active_slots", normal) or normal)))
        release_cold_start_cap = max(cold_start_cap, min(max_slots, int(getattr(self.cfg, "release_cold_start_active_slots", soft) or soft)))
        cold_starting_without_hot = inventory_enabled and inventory_health == "hot_low" and hot_supply <= 0

        force_release_full = bool(getattr(self.cfg, "release_force_full_fanout", False))

        if active < min_slots:
            desired = min_slots
            level = "bootstrap"
            reason = f"活跃槽 {active} 低于最小槽 {min_slots}，补足基础生产池"
        elif force_release_full and peak_state in {"prewarm", "peak"}:
            peak_target = min(max_slots, max(min_slots, peak_desired or max_slots))
            desired = peak_target
            level = "peak_force_full" if peak_state == "peak" else "peak_force_prewarm"
            need_text = f"，目标热会话≥{peak_min_hot}" if peak_min_hot else ""
            reason = f"{peak_mode.get('reason') or '高峰窗口'}：用户强制大放票模式，忽略近期 churn/429/1015 hold，直接拉到 {desired} 槽{need_text}"
        elif pressure.bottleneck == "risk":
            desired = max(min_slots, min(active, normal))
            level = "risk_hold"
            reason = "1015/access_denied 风险压力高：不加并发，保持/回落到普通槽位，优先让错误恢复组件处理"
        elif pressure.bottleneck == "network":
            desired = max(min_slots, min(active, normal))
            level = "network_hold"
            reason = "网络/代理错误压力高：扩槽只会放大失败，先保持/回落到普通并发并等待路线冷却或代理恢复"
        elif pressure.bottleneck == "rate_limit":
            desired = max(min_slots, min(active, normal))
            level = "rate_limit_hold"
            reason = "429/业务限流压力高：加槽不能解决业务 API 限流，保持/回落到普通并发并拉长恢复窗口"
        elif pressure.bottleneck == "cf_challenge":
            desired = max(min_slots, min(active, normal))
            level = "cf_storm_hold"
            reason = "CF/人机挑战形成风暴：继续扩槽会放大浏览器重启、登录回跳和路线惩罚，先保持/回落到普通并发"
        elif pressure.bottleneck == "churn":
            desired = max(min_slots, min(active, normal))
            level = "churn_hold"
            reason = "短窗口内 round/browser/recovery 频繁重启且无成功查询：判定为生产池抖动，先回落并发避免自激振荡"
        elif peak_state in {"prewarm", "peak"}:
            # Peak/release windows should move toward the configured target,
            # not add +1 forever until max_slots.  A release window with
            # desired_active_slots=8 must stabilize at 8; noon fixed windows can
            # still request 10 explicitly.
            peak_target = min(max_slots, max(min_slots, peak_desired or soft))
            if cold_starting_without_hot:
                desired = min(peak_target, max(min_slots, release_cold_start_cap))
                level = "peak_cold_start_cap"
                reason = (
                    f"{peak_mode.get('reason') or '高峰窗口'}：当前没有可复用热查询会话，"
                    f"先把冷启动/登录/CF生产限制在 {desired} 槽；等 hot_query 出现后再放大查询并发"
                )
            else:
                desired = peak_target
                level = "peak" if peak_state == "peak" else "peak_prewarm"
                need_text = f"，目标热会话≥{peak_min_hot}" if peak_min_hot else ""
                reason = f"{peak_mode.get('reason') or '高峰窗口'}：临时提高到 {desired} 槽{need_text}，避免放票窗口无会话可查"
        elif peak_state == "cooldown" and active > normal and not (inventory_enabled and inventory_deficit > 0):
            desired = max(normal, active - 1)
            level = "peak_cooldown_drain"
            reason = f"{peak_mode.get('reason') or '高峰结束'}：库存水位健康，允许逐步排水到普通并发"
        elif inventory_enabled and inventory_deficit > 0:
            inventory_cap = adaptive_cap
            if missed:
                inventory_cap = severe_cap
            if very_stale:
                inventory_cap = max_slots
            # A hot-pool deficit is the user's real空窗 source.  Expand
            # gradually beyond the normal soft pool, but do not blindly jump to
            # 10 outside放票期 unless the success gap is already critical.
            if inventory_health == "hot_low":
                if cold_starting_without_hot:
                    desired = min(max_slots, max(min_slots, min(active + 1, cold_start_cap)))
                else:
                    desired = min(max_slots, max(active + 1, min(inventory_floor or adaptive_cap, inventory_cap)))
            else:
                desired = min(max_slots, max(min_slots, min(inventory_floor or soft, adaptive_cap)))
            level = "inventory_fill"
            if inventory_health == "hot_low":
                level = "inventory_hot_low"
            elif inventory_health == "login_standby_low":
                level = "inventory_login_standby_low"
            elif inventory_health == "candidate_low":
                level = "inventory_candidate_low"
            if cold_starting_without_hot:
                reason = f"{inventory.get('reason') or '库存水位不足'}；当前没有热查询会话，冷启动生产限制在 {desired} 槽，避免把冷浏览器并发放大成 CF/login 风暴"
            else:
                reason = f"{inventory.get('reason') or '库存水位不足'}；动态补位到 {desired} 槽（普通软池={soft}，缺热池上限={adaptive_cap}，断档上限={severe_cap}）"
        elif near_target and insufficient_candidates:
            desired = min(max_slots, max(active + 1, soft if pressure.bottleneck == "waiting_room" else normal))
            level = "prewarm"
            reason = f"距离下一目标查询 {pool.seconds_to_target}s，缺少 hot/query_wait 会话，提前补槽"
        elif pressure.bottleneck == "waiting_room" and (missed or insufficient_hot_pool):
            desired = min(max_slots, max(active + 1, soft + 1))
            level = "waiting_room_pressure"
            reason = "最近等待室占比高且热会话不足/已错过目标窗口：提高并发挤出可用会话"
        elif missed and insufficient_hot_pool:
            desired = min(max_slots, max(active + 1, soft))
            level = "sla_missed"
            reason = f"距离上次成功 {int(pool.seconds_since_success)}s，超过目标间隔且热池不足，提升到软池"
        elif very_stale:
            desired = min(max_slots, max(active + 1, soft + 1))
            level = "critical_gap"
            reason = f"成功查询断档 {int(pool.seconds_since_success)}s，进入严重断档补位"
        elif pool.stable_successes >= int(getattr(self.cfg, "stable_success_window_count", 5) or 5) and active > normal:
            desired = max(normal, active - 1)
            level = "stable_reduce"
            reason = "最近多次查询间隔稳定，允许后续逐步回落到普通槽位"
        elif insufficient_hot_pool and active < soft and pool.seconds_since_success > stale_s:
            desired = min(soft, active + 1)
            level = "reserve_low"
            reason = "热池/冷却池储备不足且成功间隔变长，补一个生产槽"

        desired = max(min_slots, min(max_slots, int(desired)))
        return SlaDecision(
            enabled=bool(getattr(self.cfg, "enabled", True)),
            desired_active_slots=desired,
            min_slots=min_slots,
            max_slots=max_slots,
            normal_active_slots=normal,
            soft_active_slots=soft,
            pressure_level=level,
            bottleneck=pressure.bottleneck,
            reason=reason,
            should_scale_up=desired > active,
            scale_cooldown_seconds=cooldown_s,
            target_interval_seconds=pool.target_interval_seconds,
            next_target_query_at=pool.next_target_query_at,
            seconds_to_target=pool.seconds_to_target,
            evidence={
                "active_slots": active,
                "candidates": candidates,
                "needed_hot_sessions": pool.needed_hot_sessions,
                "hot_sessions": pool.hot_sessions,
                "query_wait_sessions": pool.query_wait_sessions,
                "cooling_sessions": pool.cooling_sessions,
                "producing_sessions": pool.producing_sessions,
                "recovering_sessions": pool.recovering_sessions,
                "seconds_since_success": pool.seconds_since_success,
                "near_target": near_target,
                "missed": missed,
                "insufficient_hot_pool": insufficient_hot_pool,
                "hot_supply": hot_supply,
                "cold_starting_without_hot": cold_starting_without_hot,
                "cold_start_cap": cold_start_cap,
                "release_cold_start_cap": release_cold_start_cap,
                "event_pressure": pressure.to_dict(),
                "peak_mode": peak_mode,
                "inventory": inventory,
            },
            peak_mode=peak_mode,
        )
