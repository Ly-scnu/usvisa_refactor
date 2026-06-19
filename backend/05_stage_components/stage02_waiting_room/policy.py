from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WaitingRoomDecision:
    allow_wait: bool
    kill_now: bool
    reason: str
    message: str
    max_wait_seconds: int = 0
    max_waiting_slots: int = 0
    tension: str = "normal"


class WaitingRoomPolicy:
    """Pure policy for queue handling.

    - Waiting room is a cost pool, not a target state.
    - Dynamic budget prevents all slots from sitting in queue.
    - Critical windows wait shortly instead of zero-wait thrashing.
    - Direct-only slots must never sit in the waiting room.
    - Extra queue contenders are recycled immediately.
    """

    def __init__(
        self,
        direct_only_slots: set[str],
        max_waiting_slots: int,
        *,
        min_wait_seconds: int = 10,
        default_wait_seconds: int = 30,
        max_wait_seconds: int = 60,
        loose_max_wait_seconds: int = 180,
        peak_budget_slots: int = 0,
        prewarm_budget_slots: int = 0,
        stale_budget_slots: int = 0,
        loose_budget_slots: int = 0,
    ):
        self.direct_only_slots = direct_only_slots
        self.max_waiting_slots = max(0, int(max_waiting_slots or 0))
        self.min_wait_seconds = max(1, int(min_wait_seconds or 10))
        self.default_wait_seconds = max(self.min_wait_seconds, int(default_wait_seconds or 30))
        self.max_wait_seconds = max(self.default_wait_seconds, int(max_wait_seconds or 60))
        self.loose_max_wait_seconds = max(self.max_wait_seconds, int(loose_max_wait_seconds or 180))
        self.peak_budget_slots = max(0, int(peak_budget_slots or 0))
        self.prewarm_budget_slots = max(0, int(prewarm_budget_slots or 0))
        self.stale_budget_slots = max(0, int(stale_budget_slots or 0))
        self.loose_budget_slots = max(0, int(loose_budget_slots or 0))

    def dynamic_budget(
        self,
        *,
        active_slots: int,
        hot_query_sessions: int,
        seconds_since_success: float,
        peak_mode: str = "normal",
    ) -> tuple[int, int, str]:
        active = max(0, int(active_slots or 0))
        hot = max(0, int(hot_query_sessions or 0))
        stale = float(seconds_since_success if seconds_since_success is not None else 999999.0)
        peak = str(peak_mode or "normal")

        # Backward-compatible pure policy usage in unit tests or callers that
        # have not supplied inventory evidence yet.
        if active <= 0:
            return self.max_waiting_slots, self.loose_max_wait_seconds, "configured"

        # 放票窗口不是完全 0 秒重刷，但只能留一个短探针，避免 10 个
        # 槽全坐 waiting room。peak 最紧，prewarm/cooldown 稍松。
        if peak == "peak":
            budget = self.peak_budget_slots if self.peak_budget_slots > 0 else (1 if active >= 8 else 0)
            return min(budget, self.max_waiting_slots), self.min_wait_seconds, "peak"
        if peak in {"prewarm", "cooldown"}:
            budget = self.prewarm_budget_slots if self.prewarm_budget_slots > 0 else (1 if active >= 5 else 0)
            return min(budget, self.max_waiting_slots), self.default_wait_seconds, peak

        # 热池不足或已经断档时，waiting room 预算应极低。这里仍允许
        # 大并发时 1 个短等，避免一秒不等导致无意义 kill 风暴。
        if hot < 2 or stale > 60:
            budget = self.stale_budget_slots if self.stale_budget_slots > 0 else (1 if active >= 8 else 0)
            return min(budget, self.max_waiting_slots), self.default_wait_seconds, "hot_low_or_stale"

        if active <= 4:
            return 0, self.default_wait_seconds, "small_pool_no_queue"
        if active <= 7:
            return min(1, self.max_waiting_slots), self.max_wait_seconds, "medium_pool"

        # 最松条件：热池充足且最近成功未断档，最多 2 个等待，时间不超
        # 3 分钟。即使配置 waiting_room_slots 更大，也不放开到 3+。
        loose_cap = self.loose_budget_slots if self.loose_budget_slots > 0 else 2
        holders = min(loose_cap, self.max_waiting_slots)
        wait = self.loose_max_wait_seconds if hot >= 4 and stale <= 60 else self.max_wait_seconds
        return holders, wait, "loose_hot_pool" if wait == self.loose_max_wait_seconds else "large_pool"

    def decide_on_enter(
        self,
        slot_id: str,
        current_holders: list[str],
        *,
        active_slots: int = 0,
        hot_query_sessions: int = 0,
        seconds_since_success: float = 999999.0,
        peak_mode: str = "normal",
    ) -> WaitingRoomDecision:
        budget, wait_seconds, tension = self.dynamic_budget(
            active_slots=active_slots,
            hot_query_sessions=hot_query_sessions,
            seconds_since_success=seconds_since_success,
            peak_mode=peak_mode,
        )
        if slot_id in self.direct_only_slots:
            return WaitingRoomDecision(False, True, "direct_only_waiting_room", "直入槽进入等待室，杀掉换代理", 0, budget, tension)
        if budget <= 0:
            return WaitingRoomDecision(False, True, "waiting_room_dynamic_zero_budget", f"等待室动态预算为 0（{tension}），换代理/画像继续刷", 0, budget, tension)
        if len(current_holders) >= budget:
            return WaitingRoomDecision(False, True, "waiting_room_slots_full", f"等待室动态并发满：{len(current_holders)}/{budget}（{tension}），换代理/画像", 0, budget, tension)
        return WaitingRoomDecision(True, False, "waiting_room_enter", f"进入等待室短等：最多 {wait_seconds}s（预算 {len(current_holders)+1}/{budget}，{tension}）", wait_seconds, budget, tension)
