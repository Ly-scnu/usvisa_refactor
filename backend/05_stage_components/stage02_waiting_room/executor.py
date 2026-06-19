from __future__ import annotations

import time
from importlib import import_module
from typing import Any

from ..base import StageResult
from .policy import WaitingRoomPolicy

classify_page = import_module("03_browser_management.page_classifier").classify_page
PeakWindowPolicy = import_module("00_infrastructure.orchestration.sla.peak_windows").PeakWindowPolicy
SessionInventoryAnalyzer = import_module("00_infrastructure.orchestration.sla.session_inventory").SessionInventoryAnalyzer
clock = import_module("00_infrastructure.orchestration.scheduler_clock")


class WaitingRoomStage:
    stage_name = "waiting_room"

    def __init__(self, limiter: Any | None = None):
        self.limiter = limiter

    def _status(self, ctx: Any, **patch: Any) -> None:
        if getattr(ctx, "store", None):
            ctx.store.update_slot(ctx.slot_id, **patch)

    async def execute(self, ctx: Any) -> StageResult:
        cfg = ctx.runtime_config
        page = ctx.page
        state = await classify_page(page)
        if state.stage != "waiting_room":
            return StageResult(True, self.stage_name, "not in waiting room", {"state": state.__dict__, "waited": False})
        try:
            peak = PeakWindowPolicy(cfg).current()
        except Exception:
            peak = None
        peak_mode = str(getattr(peak, "mode", "") or "")
        holders = self.limiter.snapshot() if self.limiter else []
        smart = getattr(cfg, "smart_orchestrator", None)
        slots_snapshot = ctx.store.read_slots() if getattr(ctx, "store", None) else {}
        active_slots = sum(1 for s in (slots_snapshot or {}).values() if isinstance(s, dict) and s.get("state") == "running")
        hot_sessions = 0
        try:
            hot_sessions = int(SessionInventoryAnalyzer(ctx.store, cfg).snapshot(active_count=active_slots).hot_query_sessions)
        except Exception:
            hot_sessions = 0
        seconds_since_success = 999999.0
        try:
            sched = ctx.store.scheduler_state() if getattr(ctx, "store", None) else {}
            last_success = clock.parse_ts(sched.get("last_success_at"))
            if last_success:
                seconds_since_success = max(0.0, (clock.now_dt() - last_success).total_seconds())
        except Exception:
            pass
        policy = WaitingRoomPolicy(
            set(cfg.slots.direct_only_slots),
            cfg.slots.waiting_room_slots,
            min_wait_seconds=int(getattr(smart, "waiting_room_min_wait_seconds", 10) or 10),
            default_wait_seconds=int(getattr(smart, "waiting_room_default_wait_seconds", 30) or 30),
            max_wait_seconds=int(getattr(smart, "waiting_room_max_wait_seconds", 60) or 60),
            loose_max_wait_seconds=min(int(cfg.slots.queue_wait_seconds), int(getattr(smart, "waiting_room_loose_max_wait_seconds", 180) or 180)),
            peak_budget_slots=int(getattr(smart, "waiting_room_peak_budget_slots", 0) or 0),
            prewarm_budget_slots=int(getattr(smart, "waiting_room_prewarm_budget_slots", 0) or 0),
            stale_budget_slots=int(getattr(smart, "waiting_room_stale_budget_slots", 0) or 0),
            loose_budget_slots=int(getattr(smart, "waiting_room_loose_budget_slots", 0) or 0),
        )
        decision = policy.decide_on_enter(
            ctx.slot_id,
            holders,
            active_slots=active_slots,
            hot_query_sessions=hot_sessions,
            seconds_since_success=seconds_since_success,
            peak_mode=peak_mode,
        )
        if decision.kill_now:
            cooldown = int(getattr(cfg.slots, "direct_only_recycle_cooldown_seconds", 120) or 120)
            if decision.reason == "direct_only_waiting_room" and cooldown > 0:
                self._status(
                    ctx,
                    waiting_acquired=False,
                    last_reason="direct_only_waiting_room_cooldown",
                    last_reason_zh=f"直入槽进入等待室：不占等待池，先冷却 {cooldown}s 后回收，避免 kill 风暴",
                    elapsed_s=0,
                )
                start = time.monotonic()
                while time.monotonic() - start < cooldown:
                    await page.wait_for_timeout(2500)
                    waited = round(time.monotonic() - start, 1)
                    if int(waited) % 10 == 0:
                        self._status(
                            ctx,
                            elapsed_s=waited,
                            last_reason="direct_only_waiting_room_cooldown",
                            last_reason_zh=f"直入槽等待室冷却中：{waited}s / {cooldown}s",
                        )
            return StageResult(False, self.stage_name, decision.message, {"decision": decision.__dict__, "state": state.__dict__}, retryable=True)
        holder = f"{ctx.slot_id}/{ctx.round_id}"
        if self.limiter and not self.limiter.enter(holder):
            return StageResult(False, self.stage_name, "等待室并发满，杀掉换代理", {"holders": self.limiter.snapshot()}, retryable=True)
        start = time.monotonic()
        self._status(
            ctx,
            waiting_acquired=True,
            elapsed_s=0,
            holders=self.limiter.snapshot() if self.limiter else [holder],
            last_reason="waiting_room_enter",
            last_reason_zh=decision.message,
            waiting_room_budget=decision.max_waiting_slots,
            waiting_room_max_wait_seconds=decision.max_wait_seconds,
            waiting_room_tension=decision.tension,
        )
        try:
            last_status_second = -1
            max_wait = int(decision.max_wait_seconds or cfg.slots.queue_wait_seconds)
            while time.monotonic() - start < max_wait:
                await page.wait_for_timeout(2500)
                waited = round(time.monotonic() - start, 1)
                # Keep dashboard truthful during the 180s hold instead of
                # leaving elapsed_s/waiting_acquired stale from the previous
                # CF result.
                if int(waited) // 5 != last_status_second // 5:
                    last_status_second = int(waited)
                    self._status(
                        ctx,
                        waiting_acquired=True,
                        elapsed_s=waited,
                        holders=self.limiter.snapshot() if self.limiter else [holder],
                        last_reason="waiting_room_holding",
                        last_reason_zh=f"等待室短等中：{waited}s / {max_wait}s（预算 {decision.max_waiting_slots}，{decision.tension}）",
                        waiting_room_budget=decision.max_waiting_slots,
                        waiting_room_max_wait_seconds=max_wait,
                        waiting_room_tension=decision.tension,
                    )
                st = await classify_page(page)
                if st.stage != "waiting_room":
                    return StageResult(True, self.stage_name, "left waiting room", {"state": st.__dict__, "wait_seconds": round(time.monotonic() - start, 1)})
            return StageResult(False, self.stage_name, "waiting room dynamic max wait timeout", {"wait_seconds": round(time.monotonic() - start, 1), "decision": decision.__dict__}, retryable=True)
        finally:
            if self.limiter:
                self.limiter.leave(holder)
            self._status(ctx, waiting_acquired=False, holders=self.limiter.snapshot() if self.limiter else [])
