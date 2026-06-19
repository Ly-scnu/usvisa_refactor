from __future__ import annotations

import argparse
import asyncio
from importlib import import_module
import os
import random
import threading
import time
from pathlib import Path
from typing import Any
from datetime import datetime

load_config = import_module("00_infrastructure.config.loader").load_config
_event_mod = import_module("00_infrastructure.events.event_bus")
Event = _event_mod.Event
EventBus = _event_mod.EventBus
Database = import_module("00_infrastructure.database.db").Database
iso_now = import_module("00_infrastructure.utils.time").iso_now
_proxy711 = import_module("01_proxy_management.providers.proxy711")
build_711_proxy = _proxy711.build_711_proxy
choose_route = _proxy711.choose_route
StateStore = import_module("00_infrastructure.runtime.state_store").StateStore
FileProcessLock = import_module("00_infrastructure.runtime.process_lock").FileProcessLock
SmartQueryGate = import_module("00_infrastructure.orchestration.query_gate").SmartQueryGate
SlaOrchestrator = import_module("00_infrastructure.orchestration.sla_orchestrator").SlaOrchestrator
SlotStartupPolicy = import_module("00_infrastructure.orchestration.sla.startup_policy").SlotStartupPolicy
SlotDrainPolicy = import_module("07_scheduler.drain_policy").SlotDrainPolicy
WaitingRoomPolicy = import_module("05_stage_components.stage02_waiting_room.policy").WaitingRoomPolicy
run_one_dragon_round = import_module("05_stage_components.pipeline").run_one_dragon_round
routes_cooling_snapshot = import_module("00_infrastructure.orchestration.route_health.route_cooling_guard").routes_cooling_snapshot


STAGE_ZH = {
    "proxy_acquired": "代理已获取",
    "cf_gate": "入口/CF阶段",
    "waiting_room": "等待室排队中",
    "direct_only_recycle": "直入槽进等待室，立即回收",
    "login": "登录阶段",
    "business_query": "业务查票阶段",
    "booking_ready": "命中目标准备提交",
    "real_one_dragon": "真实一条龙执行中",
    "route_cooling_wait": "代理路线冷却中",
    "network_error_wait": "网络错误冷却中",
    "failed_round_wait": "失败冷却中",
    "account_guard_wait": "账号保护暂停中",
    "round_done": "本轮完成",
    "round_recycle": "本轮回收换代理",
}


class WaitingRoomLimiter:
    def __init__(self, max_slots: int):
        self.max_slots = max_slots
        self.lock = threading.Lock()
        self.holders: set[str] = set()

    def enter(self, key: str) -> bool:
        with self.lock:
            if key in self.holders:
                return True
            if len(self.holders) >= self.max_slots:
                return False
            self.holders.add(key)
            return True

    def leave(self, key: str) -> None:
        with self.lock:
            self.holders.discard(key)

    def snapshot(self) -> list[str]:
        with self.lock:
            return sorted(self.holders)


class SlotRunner(threading.Thread):
    def __init__(self, slot_no: int, store: StateStore, event_bus: EventBus, limiter: WaitingRoomLimiter, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.slot_no = slot_no
        self.slot = f"slot_{slot_no:02d}"
        self.store = store
        self.config = store.config
        self.event_bus = event_bus
        self.limiter = limiter
        self.stop_event = stop_event
        self.round_no = 0
        self.seen_commands: set[str] = set()
        # Command files are append-only JSONL.  On a fresh producer restart,
        # old drain/kill commands must not be replayed against the new slot
        # generation; only commands appended after this runner starts count.
        try:
            self.store.read_new_commands(self.slot, self.seen_commands)
        except Exception:
            pass
        self.waiting_policy = WaitingRoomPolicy(set(self.config.slots.direct_only_slots), self.config.slots.waiting_room_slots)
        self.startup_policy = SlotStartupPolicy(self.config, self.store)
        self.last_round_ok: bool = False
        self.last_round_failure_reason: str = ""
        self.last_round_wait_seconds: float = 0.0
        self.drain_requested: bool = False
        try:
            self.store.clear_slot_drain(self.slot, reason="slot_runner_new_generation")
        except Exception:
            pass

    def is_direct_only(self) -> bool:
        return self.slot in set(self.config.slots.direct_only_slots)

    def publish(self, event_type: str, **payload: Any) -> None:
        self.event_bus.publish(Event(event_type, slot_id=self.slot, round_id=f"round_{self.round_no:04d}", payload=payload))

    def update(self, stage: str, **patch: Any) -> None:
        self.store.update_slot(
            self.slot,
            state="running",
            stage=stage,
            stage_zh=STAGE_ZH.get(stage, stage),
            round=self.round_no,
            **patch,
        )

    def consume_commands(self) -> str | None:
        for cmd in self.store.read_new_commands(self.slot, self.seen_commands):
            action = str(cmd.get("action") or "")
            self.publish("slot_command_received", action=action, command=cmd)
            if action in {"kill", "kill_round", "restart"}:
                return "command_kill"
            if action in {"drain", "stop_slot"}:
                self.drain_requested = True
                self.store.update_slot(
                    self.slot,
                    drain_requested=True,
                    drain_reason=str(cmd.get("reason") or "smart_drain"),
                    last_reason="smart_drain_requested",
                    last_reason_zh="智能调度请求排水：本轮安全结束后停止该槽",
                )
                return "command_drain"
        return None

    def result_display_patch(self, result: Any) -> dict[str, Any]:
        payload = getattr(result, "payload", None) or {}
        needs = str(payload.get("needs_recover") or payload.get("reason") or "") if isinstance(payload, dict) else ""
        if needs == "drain_requested" or bool(payload.get("safe_stop") if isinstance(payload, dict) else False):
            return {
                "last_reason": "drain_requested",
                "last_reason_zh": "智能排水：当前会话在安全边界结束，槽位将停止，不计为业务失败",
                "recovery_error_type": "",
                "recovery_action": "safe_drain",
                "recovery_component": "SlotDrainPolicy",
            }
        if needs == "login_timeout":
            return {
                "last_reason": "login_hard_timeout",
                "last_reason_zh": "登录阶段硬超时：本轮结束并换新代理/浏览器画像，避免槽位长时间假死",
                "recovery_error_type": "login_timeout",
                "recovery_action": "fresh_round",
                "recovery_component": "LoginStageHardTimeout",
            }
        if needs == "account_login_blocked":
            return {
                "last_reason": "account_login_blocked",
                "last_reason_zh": "账号级登录禁止：已触发全局保护暂停，不再并发重试账号密码",
                "recovery_error_type": "account_login_blocked",
                "recovery_action": "global_login_pause",
                "recovery_component": "AccountLoginGuard",
            }
        recovery = payload.get("recovery") if isinstance(payload, dict) else None
        if isinstance(recovery, dict):
            error_type = str(recovery.get("error_type") or "")
            action = str(recovery.get("action") or "")
            component = str(recovery.get("recovery_component") or recovery.get("component") or "")
            msg = str(recovery.get("message") or getattr(result, "message", "") or "")
            if error_type == "access_denied":
                msg = "Cloudflare 已阻止访问：已保存截图并立即回收当前代理/浏览器画像，下一轮自动重新开始"
            return {
                "last_reason": action or error_type or getattr(result, "message", ""),
                "last_reason_zh": msg,
                "recovery_error_type": error_type,
                "recovery_action": action,
                "recovery_component": component or ("AccessDeniedResetRecovery" if error_type == "access_denied" else ""),
            }
        return {
            "last_reason": getattr(result, "message", ""),
            "last_reason_zh": getattr(result, "message", ""),
        }

    def run_round(self) -> None:
        # A drain request belongs to the previous worker/round generation.  New
        # rounds start clean unless a fresh command is consumed in this runner.
        # This prevents persistent slot_status.json flags from making pipeline
        # stages return "stage skipped by drain request" forever after scale-up.
        if not self.drain_requested:
            try:
                self.store.clear_slot_drain(self.slot, reason=f"round_{self.round_no + 1:04d}_start")
            except Exception:
                pass
        self.round_no += 1
        started = time.time()
        started_mono = time.monotonic()
        started_iso = iso_now()
        if self.config.producer.real_browser_probe:
            self.publish("round_start", mode="real_one_dragon")
            self.update("real_one_dragon", elapsed_s=0, round_started_at=started_iso, waiting_acquired=False)
            result = asyncio.run(
                run_one_dragon_round(
                    config=self.config,
                    store=self.store,
                    event_bus=self.event_bus,
                    slot_id=self.slot,
                    round_no=self.round_no,
                    limiter=self.limiter,
                    route_index=self.slot_no + self.round_no,
                    round_started_at=started_iso,
                    round_started_monotonic=started_mono,
                )
            )
            display_patch = self.result_display_patch(result)
            payload = getattr(result, "payload", None) or {}
            if isinstance(payload, dict):
                self.last_round_failure_reason = "" if result.ok else str(payload.get("needs_recover") or payload.get("reason") or result.message or "")
                self.last_round_wait_seconds = float(payload.get("wait_seconds") or 0.0)
            else:
                self.last_round_failure_reason = "" if result.ok else str(getattr(result, "message", "") or "")
                self.last_round_wait_seconds = 0.0
            if isinstance(payload, dict) and (payload.get("safe_stop") or payload.get("needs_recover") == "drain_requested"):
                self.drain_requested = True
            self.update(
                "round_done" if result.ok else "round_recycle",
                elapsed_s=round(time.time() - started, 1),
                result_stage=result.stage,
                **display_patch,
            )
            self.last_round_ok = bool(result.ok)
            self.publish("round_finish", ok=result.ok, stage=result.stage, message=result.message)
            return
        route = choose_route(self.config.proxy, self.slot_no + self.round_no)
        proxy = build_711_proxy(self.config.proxy, route)
        self.update("proxy_acquired", elapsed_s=0, proxy_display=f"{proxy.country}/{proxy.asn or '-'}:{proxy.session_id}", waiting_acquired=False)
        self.publish("round_start", proxy_display=f"{proxy.country}/{proxy.asn or '-'}:{proxy.session_id}")

        # Standalone scaffold mode intentionally exercises scheduler, queue
        # policy, command handling, state, UI, and event flow without reading
        # the previous rewrite. Real browser/login stages are migrated next behind
        # this same contract.
        scripted_path = ["cf_gate"]
        if self.is_direct_only():
            scripted_path.append("waiting_room")
        else:
            scripted_path.append("waiting_room")
        scripted_path.extend(["login", "business_query", "round_recycle"])

        holder_key = f"{self.slot}/round_{self.round_no:04d}"
        waiting_entered_at: float | None = None
        for stage in scripted_path:
            if self.stop_event.is_set():
                break
            killed = self.consume_commands()
            if killed:
                self.update("round_recycle", last_reason=killed, elapsed_s=round(time.time() - started, 1))
                self.publish("round_killed", reason=killed)
                return
            elapsed = round(time.time() - started, 1)
            if stage == "waiting_room":
                decision = self.waiting_policy.decide_on_enter(self.slot, self.limiter.snapshot())
                if decision.reason == "direct_only_waiting_room":
                    self.update("direct_only_recycle", elapsed_s=elapsed, last_reason="direct_only_waiting_room", last_reason_zh="直入槽进入等待室，杀掉换代理")
                    self.publish("direct_only_waiting_room_kill")
                    return
                if decision.reason == "waiting_room_slots_full" or not self.limiter.enter(holder_key):
                    self.update("round_recycle", elapsed_s=elapsed, last_reason="waiting_room_slots_full", last_reason_zh=decision.message)
                    self.publish("waiting_room_slots_full", holders=self.limiter.snapshot())
                    return
                waiting_entered_at = time.time()
                self.update("waiting_room", elapsed_s=elapsed, waiting_acquired=True, holders=self.limiter.snapshot())
                self.publish("waiting_room_enter", holders=self.limiter.snapshot())
                while time.time() - waiting_entered_at < min(6, self.config.slots.queue_wait_seconds):
                    if self.stop_event.is_set() or self.consume_commands():
                        self.limiter.leave(holder_key)
                        self.update("round_recycle", waiting_acquired=False, last_reason="command_or_stop")
                        return
                    time.sleep(1)
                self.limiter.leave(holder_key)
                self.update(
                    "round_recycle",
                    waiting_acquired=False,
                    holders=self.limiter.snapshot(),
                    last_reason="queue_wait_timeout_demo",
                    last_reason_zh="独立框架演示：等待槽回收换代理",
                )
                self.publish("waiting_room_leave", holders=self.limiter.snapshot())
                return
            self.update(stage, elapsed_s=elapsed)
            self.publish("stage_enter", stage=stage)
            time.sleep(1.0 if self.config.producer.standalone_smoke else 3.0)

    def run(self) -> None:
        # Startup stagger is dynamic.  A fixed slot_no*30s delay made slot_08/
        # slot_10 sit idle for minutes while the hot query pool was empty.  The
        # policy keeps gentle staggering only when the pool is healthy and
        # compresses it during release windows or SLA gaps.
        stagger_decision = self.startup_policy.decide(slot_no=self.slot_no)
        wait_s = max(0.0, float(stagger_decision.wait_seconds or 0.0))
        if wait_s and self.slot_no > 1:
            start = time.monotonic()
            while not self.stop_event.is_set() and time.monotonic() - start < wait_s:
                if self.consume_commands() == "command_drain":
                    break
                self.store.update_slot(
                    self.slot,
                    state="running",
                    stage="round_recycle",
                    stage_zh="启动错峰等待",
                    round=self.round_no,
                    elapsed_s=round(time.monotonic() - start, 1),
                    last_reason="slot_start_stagger",
                    last_reason_zh=f"{stagger_decision.reason}：{round(time.monotonic() - start, 1)}s / {round(wait_s, 1)}s",
                    startup_stagger=stagger_decision.to_dict(),
                )
                time.sleep(min(1.0, max(0.2, wait_s - (time.monotonic() - start))))
        while not self.stop_event.is_set():
            guard_active, guard = _account_guard_active(self.store)
            if guard_active:
                _mark_account_guard_wait(self.store, self.slot_no, guard)
                self.publish("slot_drained", reason="account_login_guard_before_round", account_guard=guard)
                break
            command = self.consume_commands()
            if command == "command_drain":
                self.publish("slot_drained", reason="drain_requested_before_round_command")
                break
            if command == "command_kill":
                self.publish("slot_killed", reason="command_kill_before_round")
                break
            if self.drain_requested:
                self.publish("slot_drained", reason="drain_requested_before_round")
                break
            try:
                self.run_round()
            except Exception as exc:
                self.last_round_ok = False
                self.last_round_failure_reason = "slot_exception"
                self.last_round_wait_seconds = 0.0
                self.update("round_recycle", last_reason="slot_exception", last_reason_zh=repr(exc))
                self.publish("slot_exception", error=repr(exc))
            if self.stop_event.is_set():
                break
            command = self.consume_commands()
            if command == "command_drain":
                self.publish("slot_drained", reason="drain_requested_after_round_command")
                break
            if command == "command_kill":
                self.publish("slot_killed", reason="command_kill_after_round")
                break
            if self.drain_requested:
                self.publish("slot_drained", reason="drain_requested_after_round")
                break
            failed_cooldown = max(1.0, float(getattr(self.config.producer, "failed_round_cooldown_seconds", 20) or 20))
            if self.last_round_failure_reason == "route_cooling":
                failed_cooldown = max(failed_cooldown, min(300.0, max(15.0, self.last_round_wait_seconds or 60.0)))
            elif self.last_round_failure_reason == "network_error":
                failed_cooldown = max(failed_cooldown, float(getattr(self.config.producer, "network_error_round_cooldown_seconds", 60.0) or 60.0))
            sleep_s = 1.0 if self.last_round_ok else failed_cooldown
            slept = 0.0
            cooldown_started = time.monotonic()
            while not self.stop_event.is_set() and slept < sleep_s:
                command = self.consume_commands()
                if command in {"command_drain", "command_kill"}:
                    self.publish("slot_drained" if command == "command_drain" else "slot_killed", reason=f"{command}_during_cooldown")
                    self.drain_requested = True
                    break
                if not self.last_round_ok:
                    remaining = max(0.0, sleep_s - slept)
                    if self.last_round_failure_reason == "route_cooling":
                        wait_stage = "route_cooling_wait"
                        wait_msg = f"全部代理路线冷却中，当前槽等待 {round(remaining, 1)}s 后重试"
                    elif self.last_round_failure_reason == "network_error":
                        wait_stage = "network_error_wait"
                        wait_msg = f"网络错误冷却中，当前槽等待 {round(remaining, 1)}s 后重试"
                    else:
                        wait_stage = "failed_round_wait"
                        wait_msg = f"失败冷却中，当前槽等待 {round(remaining, 1)}s 后重试"
                    self.update(
                        wait_stage,
                        elapsed_s=round(time.monotonic() - cooldown_started, 1),
                        cooldown_remaining_s=round(remaining, 1),
                        last_reason=self.last_round_failure_reason,
                        last_reason_zh=wait_msg,
                    )
                step = min(1.0, sleep_s - slept)
                time.sleep(step)
                slept += step
        guard_active, guard = _account_guard_active(self.store)
        if guard_active:
            self.store.update_slot(
                self.slot,
                state="stopped",
                stage="account_guard_wait",
                stage_zh=STAGE_ZH["account_guard_wait"],
                last_reason="account_login_blocked",
                last_reason_zh=str(guard.get("reason_zh") or "账号级登录禁止，暂停登录重试"),
                account_guard_until=str(guard.get("block_until") or ""),
                updated_at=iso_now(),
            )
        else:
            self.store.update_slot(self.slot, state="stopped", stage="stopped", stage_zh="已停止", updated_at=iso_now())


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _account_guard_active(store: StateStore) -> tuple[bool, dict[str, Any]]:
    try:
        guard = store.account_guard()
    except Exception:
        guard = {}
    if not isinstance(guard, dict) or not bool(guard.get("active")):
        return False, guard if isinstance(guard, dict) else {}
    until = _parse_ts(guard.get("block_until"))
    if until:
        now = datetime.now(until.tzinfo).astimezone(until.tzinfo) if until.tzinfo else datetime.now()
        if until <= now:
            try:
                store.write_account_guard({**guard, "active": False, "expired_at": iso_now(), "expire_reason": "block_until_elapsed"})
            except Exception:
                pass
            return False, {**guard, "active": False}
    return True, guard


def _mark_account_guard_wait(store: StateStore, slot_no: int, guard: dict[str, Any]) -> None:
    store.update_slot(
        f"slot_{slot_no:02d}",
        state="running",
        stage="account_guard_wait",
        stage_zh=STAGE_ZH["account_guard_wait"],
        last_reason="account_login_blocked",
        last_reason_zh=str(guard.get("reason_zh") or "账号级登录禁止，暂停登录重试"),
        account_guard_until=str(guard.get("block_until") or ""),
    )


def _smart_desired_slots(config: Any, store: StateStore, active_count: int) -> tuple[int, dict[str, Any]]:
    smart = getattr(config, "smart_orchestrator", None)
    if not bool(getattr(smart, "enabled", False)):
        return int(config.slots.total_slots), {"enabled": False, "reason": "legacy_total_slots"}
    min_slots = max(2, int(getattr(smart, "min_slots", 2) or 2))
    max_slots = max(min_slots, min(10, int(getattr(smart, "max_slots", 10) or 10)))
    target_ready = max(min_slots, int(getattr(smart, "target_ready_sessions", 4) or 4))
    min_hot = max(1, int(getattr(smart, "min_hot_sessions", 2) or 2))
    soft_cap = max(min_slots, min(max_slots, int(getattr(smart, "soft_active_slots", target_ready + 1) or (target_ready + 1))))
    stale_s = max(60, int(getattr(smart, "scale_up_stale_success_seconds", 150) or 150))
    critical_stale_s = max(stale_s, int(getattr(smart, "critical_stale_success_seconds", 300) or 300))
    snap = SmartQueryGate(store, config).snapshot()
    sessions = snap.get("sessions") if isinstance(snap.get("sessions"), dict) else {}
    ready = sum(1 for s in sessions.values() if int(s.get("success_count") or 0) > 0 and str(s.get("state") or "") in {"ready_warm", "ready_hot"})
    successful = sum(1 for s in sessions.values() if int(s.get("success_count") or 0) > 0)
    recovering = sum(1 for s in sessions.values() if str(s.get("state") or "") == "recovering")
    cooling = 0
    for s in sessions.values():
        if int(s.get("success_count") or 0) <= 0:
            continue
        next_q = _parse_ts(s.get("next_query_at"))
        if not next_q:
            continue
        now_q = datetime.now(next_q.tzinfo).astimezone(next_q.tzinfo) if next_q.tzinfo else datetime.now()
        if next_q > now_q:
            cooling += 1
    last_success = _parse_ts(snap.get("last_success_at"))
    age = 999999.0
    if last_success:
        now = datetime.now(last_success.tzinfo).astimezone(last_success.tzinfo) if last_success.tzinfo else datetime.now()
        age = max(0.0, (now - last_success).total_seconds())

    # The business API gate already enforces the user's SLA (one successful
    # query per target interval).  Slot scaling is only a coverage mechanism:
    # keep a small hot/cooling reserve and add one worker only when the last
    # success is stale.  Do not fill all 10 slots just because warm sessions are
    # in their normal 3-4 minute reuse cooldown.
    desired = max(min_slots, min(active_count, soft_cap))
    reason = "hold_soft_pool"
    if active_count < min_slots:
        desired = min_slots
        reason = "below_min_slots"
    elif active_count >= soft_cap and age < critical_stale_s:
        desired = active_count
        reason = f"soft_cap_hold:active={active_count},age={int(age)}s"
    elif ready < min_hot and active_count < soft_cap:
        desired = max(desired, min(soft_cap, active_count + 1))
        reason = f"hot_reserve_low:{ready}/{min_hot}"
    elif age > stale_s and active_count < soft_cap:
        desired = max(desired, min(soft_cap, active_count + 1))
        reason = f"last_success_stale_soft:{int(age)}s"
    elif age > critical_stale_s and active_count < max_slots:
        desired = max(desired, min(max_slots, active_count + 1))
        reason = f"last_success_critical:{int(age)}s"
    elif ready < target_ready and active_count < soft_cap and successful == 0:
        desired = max(desired, min(soft_cap, active_count + 1))
        reason = f"bootstrap_ready_below_target:{ready}/{target_ready}"
    desired = min(max_slots, max(min_slots, desired))
    return desired, {
        "enabled": True,
        "reason": reason,
        "ready_sessions": ready,
        "successful_sessions": successful,
        "recovering_sessions": recovering,
        "cooling_sessions": cooling,
        "target_ready_sessions": target_ready,
        "min_hot_sessions": min_hot,
        "last_success_age_seconds": round(age, 1),
        "min_slots": min_slots,
        "max_slots": max_slots,
        "soft_active_slots": soft_cap,
        "critical_stale_success_seconds": critical_stale_s,
    }


def _proxy_routes_cooling(config: Any, store: StateStore) -> tuple[bool, float, list[dict[str, Any]]]:
    """Return whether every configured proxy route is currently cooled.

    When all routes are cooling, launching more browser workers only creates
    route_cooling rounds and UI noise.  The correct behavior is to keep a small
    standby pool and wait for the first route cooldown to expire.
    """

    snap = routes_cooling_snapshot(config, store)
    return (
        bool(snap.get("all_routes_cooling")),
        float(snap.get("min_wait_seconds") or 0.0),
        list(snap.get("routes") or []),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    args = ap.parse_args()
    root = Path(args.project_root)
    config = load_config(root)
    singleton_lock = FileProcessLock(config.data_dir / "runtime" / "locks" / "producer_worker.lock")
    if not singleton_lock.acquire(blocking=False):
        print(f"producer_worker already running for {root}; singleton lock is held", flush=True)
        return 0
    store = StateStore(config)
    try:
        store.init_slots()
        store.set_pipeline(running=True, pid=os.getpid(), mode="standalone")
        db = Database(config.data_dir / "database" / "app.db")
        event_bus = EventBus(db, config.data_dir / "logs" / "events.jsonl")
        event_bus.publish(Event("producer_worker_start", payload={"mode": "standalone", "singleton_lock": str(singleton_lock.path)}))
        stop_event = threading.Event()
        limiter = WaitingRoomLimiter(config.slots.waiting_room_slots)
        smart = getattr(config, "smart_orchestrator", None)
        smart_enabled = bool(getattr(smart, "enabled", False))
        # Initial process boot is cold production, not hot-query capacity.  If
        # min_slots=10, launching all 10 cold browsers at once pushes every
        # worker through CF/login together.  Start with the cold-start cap and
        # let the SLA loop add workers gradually.
        if smart_enabled:
            configured_min = int(getattr(smart, "min_slots", config.slots.total_slots) or config.slots.total_slots)
            initial_slots = max(1, min(configured_min, int(getattr(smart, "cold_start_active_slots", configured_min) or configured_min)))
        else:
            initial_slots = config.slots.total_slots
        max_slots = int(getattr(smart, "max_slots", initial_slots) or initial_slots) if smart_enabled else config.slots.total_slots
        max_slots = max(initial_slots, min(10, max_slots))
        threads: dict[int, SlotRunner] = {}
        last_slot_start_at: dict[int, float] = {}
        sla_orchestrator = SlaOrchestrator(store, config) if smart_enabled else None
        drain_policy = SlotDrainPolicy(config, store) if smart_enabled else None

        def start_slot(slot_no: int, reason: str) -> bool:
            guard_active, guard = _account_guard_active(store)
            if guard_active:
                _mark_account_guard_wait(store, slot_no, guard)
                event_bus.publish(Event("smart_slot_start_blocked", slot_id=f"slot_{slot_no:02d}", payload={"reason": "account_login_guard", "account_guard": guard}))
                return False
            if slot_no in threads and threads[slot_no].is_alive():
                return False
            restart_gap = max(0.0, float(getattr(smart, "slot_restart_cooldown_seconds", 15.0) or 15.0)) if smart_enabled else 0.0
            last_started = last_slot_start_at.get(slot_no, 0.0)
            if restart_gap and time.monotonic() - last_started < restart_gap:
                return False
            try:
                store.clear_slot_drain(f"slot_{slot_no:02d}", reason=f"start_slot:{reason}")
            except Exception:
                pass
            t = SlotRunner(slot_no, store, event_bus, limiter, stop_event)
            threads[slot_no] = t
            last_slot_start_at[slot_no] = time.monotonic()
            t.start()
            event_bus.publish(Event("smart_slot_started", slot_id=f"slot_{slot_no:02d}", payload={"slot_no": slot_no, "reason": reason, "active_slots": len([x for x in threads.values() if x.is_alive()])}))
            return True

        initial_guard_active, initial_guard = _account_guard_active(store)
        if initial_guard_active:
            for i in range(1, max_slots + 1):
                _mark_account_guard_wait(store, i, initial_guard)
            event_bus.publish(Event("account_login_guard_active", payload={"where": "producer_initial_start", "account_guard": initial_guard}))
        else:
            for i in range(1, initial_slots + 1):
                start_slot(i, "initial_min_slots" if smart_enabled else "legacy_total_slots")

        # Start cooldown after the initial min-slot pool is launched.  Otherwise a
        # stale historical success from a previous run can immediately add slot_03
        # on process boot, which makes the dashboard look chaotic again.
        last_scale_at = time.monotonic()
        last_drain_at = 0.0
        last_drain_skip_at = 0.0
        while True:
            time.sleep(1)
            guard_active, guard = _account_guard_active(store)
            if guard_active:
                active_guard = [slot_no for slot_no, t in threads.items() if t.is_alive()]
                for slot_no in active_guard:
                    store.append_command(f"slot_{slot_no:02d}", "drain", {"reason": "account_login_guard", "account_guard": guard})
                for i in range(1, max_slots + 1):
                    if i not in threads or not threads[i].is_alive():
                        _mark_account_guard_wait(store, i, guard)
                if active_guard:
                    event_bus.publish(Event("account_login_guard_active", payload={"where": "producer_loop", "active_slots": active_guard, "account_guard": guard}))
                continue
            if not smart_enabled:
                continue
            active = len([t for t in threads.values() if t.is_alive()])
            if sla_orchestrator is not None:
                decision = sla_orchestrator.decide(active_slots=active)
                desired = int(decision.desired_active_slots)
                meta = decision.to_dict()
                cooldown = max(10.0, float(decision.scale_cooldown_seconds or getattr(smart, "scale_up_cooldown_seconds", 60) or 60))
            else:
                desired, meta = _smart_desired_slots(config, store, active)
                cooldown = max(10.0, float(getattr(smart, "scale_up_cooldown_seconds", 60) or 60))
            pressure_level = str(meta.get("pressure_level") or "")
            min_for_decision = int(meta.get("min_slots") or getattr(smart, "min_slots", 2) or 2)
            normal_for_decision = int(meta.get("normal_active_slots") or getattr(smart, "normal_active_slots", min_for_decision) or min_for_decision)
            soft_for_decision = int(meta.get("soft_active_slots") or getattr(smart, "soft_active_slots", normal_for_decision) or normal_for_decision)
            all_routes_cooling, route_cool_wait, route_cool_rows = _proxy_routes_cooling(config, store)
            if all_routes_cooling:
                desired = min(desired, max(1, min_for_decision))
                meta = {
                    **meta,
                    "pressure_level": "proxy_routes_cooling",
                    "reason": f"全部代理路线冷却中，暂停扩槽，约 {round(route_cool_wait, 1)}s 后再补热池",
                    "all_proxy_routes_cooling": True,
                    "route_cool_wait_seconds": round(route_cool_wait, 1),
                    "route_cooling_rows": route_cool_rows,
                }
                pressure_level = "proxy_routes_cooling"
            peak_mode = str((meta.get("peak_mode") or {}).get("mode") if isinstance(meta.get("peak_mode"), dict) else meta.get("peak_mode") or "")
            evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
            inventory = evidence.get("inventory") if isinstance(evidence.get("inventory"), dict) else {}
            route_recovery_ramp = evidence.get("route_recovery_ramp") if isinstance(evidence.get("route_recovery_ramp"), dict) else {}
            ramp_active = bool(route_recovery_ramp.get("active"))
            missed = bool(evidence.get("missed"))
            inventory_health = str(inventory.get("health_level") or "")
            adaptive_cap = int(getattr(smart, "adaptive_inventory_active_slots", max(soft_for_decision, 6)) or max(soft_for_decision, 6))
            severe_cap = int(getattr(smart, "severe_gap_active_slots", max(adaptive_cap, 8)) or max(adaptive_cap, 8))
            # Inventory targets are logical waterlines.  Do not cap every
            # inventory decision at soft_active_slots: when the hot query pool is
            # missing, a fixed soft cap creates long空窗.  Instead use dynamic
            # caps from the SLA decision: healthy/standby deficits stay gentle,
            # hot deficit can expand to adaptive_cap, missed SLA to severe_cap,
            # and release-period prewarm/peak can go higher.
            if pressure_level.startswith("inventory_") and peak_mode not in {"prewarm", "peak"}:
                cap = soft_for_decision
                if inventory_health == "hot_low":
                    cap = max(cap, adaptive_cap)
                if missed:
                    cap = max(cap, severe_cap)
                desired = min(desired, max(min_for_decision, cap))
            urgent_levels = {
                "prewarm",
                "peak_prewarm",
                "peak",
                "peak_force_prewarm",
                "peak_force_full",
                "sla_missed",
                "waiting_room_pressure",
                "critical_gap",
                "inventory_fill",
                "inventory_hot_low",
                "inventory_login_standby_low",
                "inventory_candidate_low",
            }
            # Inventory fill is a waterline operation: it may need to start
            # several workers before a 60s SLA can be stable, but outside peak
            # windows it is capped by the soft pool to avoid CF storms.
            inventory_urgent = (not all_routes_cooling) and pressure_level.startswith("inventory_") and active < desired and (missed or inventory_health == "hot_low" or peak_mode in {"prewarm", "peak"})
            release_urgent = (
                (not all_routes_cooling)
                and pressure_level in {"prewarm", "peak_prewarm", "peak", "peak_force_prewarm", "peak_force_full"}
                and active < desired
            )
            urgent_prewarm = release_urgent or (pressure_level in urgent_levels and active < normal_for_decision) or inventory_urgent
            cooldown_ok = (time.monotonic() - last_scale_at >= cooldown) or urgent_prewarm
            if active < desired and active < max_slots and cooldown_ok:
                # During an explicit release/peak window the operator expects
                # all requested slots to be produced immediately.  The old
                # loop started only one slot per scheduler tick and then the
                # 300s scale cooldown made slots 03-10 remain "waiting to
                # start" through the critical minute.  Keep one-by-one scaling
                # for normal/inventory mode, but batch-fill up to desired in
                # peak/prewarm mode.
                started_any = False
                for slot_no in range(1, max_slots + 1):
                    if len([x for x in threads.values() if x.is_alive()]) >= desired:
                        break
                    if slot_no not in threads or not threads[slot_no].is_alive():
                        if start_slot(slot_no, str(meta.get("reason") or "smart_scale_up")):
                            started_any = True
                            last_scale_at = time.monotonic()
                            event_bus.publish(Event("smart_slot_scale", payload={**meta, "active_slots": active, "desired_slots": desired, "started_slot": slot_no}))
                            if peak_mode not in {"prewarm", "peak"} and pressure_level not in {"prewarm", "peak_prewarm", "peak"}:
                                break
            elif active > desired and drain_policy is not None:
                # Never drain while the operational inventory is below the
                # user's waterline.  A release-window cooldown is only an
                # observation period; if hot/query-ready sessions are still 0,
                # draining just-created candidates creates the空窗 the scheduler
                # is supposed to eliminate.
                inventory_deficit = int(inventory.get("total_deficit") or 0)
                hot_deficit = int(inventory.get("hot_deficit") or 0)
                # During CF/429/network/risk/churn storms, inventory is often
                # low exactly because every new worker is being challenged or
                # rate-limited.  Blocking drain in that state creates a
                # positive feedback loop: hot_low -> keep 8/10 slots -> more
                # CF/429 -> no hot pool.  Storm holds must be allowed to drain
                # back to normal even when inventory is below target.
                storm_hold = pressure_level in {
                    "risk_hold",
                    "network_hold",
                    "rate_limit_hold",
                    "cf_storm_hold",
                    "churn_hold",
                    "proxy_routes_cooling",
                }
                drain_blocked = (
                    not storm_hold
                    and (
                        (inventory_deficit > 0 and not all_routes_cooling and not ramp_active)
                        or (hot_deficit > 0 and not all_routes_cooling and not ramp_active)
                        or (inventory_health in {"hot_low", "login_standby_low", "candidate_low"} and not all_routes_cooling and not ramp_active)
                        or (bool(missed) and not all_routes_cooling and not ramp_active)
                        or (peak_mode in {"prewarm", "peak", "cooldown"} and not ramp_active)
                        or (pressure_level in {"inventory_fill", "inventory_hot_low", "inventory_login_standby_low", "inventory_candidate_low", "bootstrap", "sla_missed", "critical_gap"} and not all_routes_cooling and not ramp_active)
                    )
                )
                if drain_blocked:
                    if time.monotonic() - last_drain_skip_at >= 15.0:
                        event_bus.publish(Event("smart_slot_drain_skipped", payload={**meta, "active_slots": active, "desired_slots": desired, "reason": "inventory_or_peak_needs_capacity", "inventory_health": inventory_health, "inventory_deficit": inventory_deficit, "hot_deficit": hot_deficit}))
                        last_drain_skip_at = time.monotonic()
                    continue
                drain_check = max(5.0, float(getattr(smart, "drain_check_seconds", 30) or 30))
                if time.monotonic() - last_drain_at >= drain_check:
                    for cand in drain_policy.choose(active=active, desired=desired):
                        store.append_command(cand.slot_id, "drain", {"reason": cand.reason, "desired_slots": desired, "active_slots": active})
                        store.update_slot(cand.slot_id, drain_requested=True, drain_reason=cand.reason)
                        event_bus.publish(Event("smart_slot_drain_requested", slot_id=cand.slot_id, payload={**meta, "candidate": cand.to_dict(), "active_slots": active, "desired_slots": desired}))
                    last_drain_at = time.monotonic()
    except KeyboardInterrupt:
        try:
            stop_event.set()
        except Exception:
            pass
    finally:
        try:
            stop_event.set()
            for t in threads.values():
                t.join(timeout=5)
            store.set_pipeline(running=False, mode="standalone", stopped_at=iso_now())
            event_bus.publish(Event("producer_worker_stop"))
        finally:
            singleton_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
