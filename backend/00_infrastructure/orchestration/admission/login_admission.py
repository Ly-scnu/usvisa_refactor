from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any
import uuid

clock = import_module("00_infrastructure.orchestration.scheduler_clock")


@dataclass
class LoginAdmissionDecision:
    ok: bool
    lease_id: str = ""
    wait_seconds: float = 0.0
    reason: str = ""
    message: str = ""
    state: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lease_id": self.lease_id,
            "wait_seconds": round(float(self.wait_seconds or 0), 1),
            "reason": self.reason,
            "message": self.message,
            "state": self.state or {},
        }


class LoginAdmissionController:
    """Global gate for account credential submission.

    This is intentionally scoped to the dangerous action only: filling and
    submitting account credentials.  Slots may still park on the login page;
    they just cannot all submit username/password at once.
    """

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _now(self) -> datetime:
        return datetime.now().astimezone()

    def _parse_ts(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    def _account_guard_active(self) -> tuple[bool, dict[str, Any]]:
        try:
            guard = self.store.account_guard()
        except Exception:
            guard = {}
        if not isinstance(guard, dict) or not bool(guard.get("active")):
            return False, guard if isinstance(guard, dict) else {}
        until = self._parse_ts(guard.get("block_until"))
        if until and until <= self._now():
            try:
                self.store.write_account_guard({**guard, "active": False, "expired_at": clock.now_iso(), "expire_reason": "block_until_elapsed"})
            except Exception:
                pass
            return False, {**guard, "active": False}
        return True, guard

    def _hot_count(self) -> int:
        try:
            slots = self.store.read_slots() or {}
        except Exception:
            slots = {}
        hot_pages = {"home", "schedule"}
        n = 0
        for rec in slots.values():
            if not isinstance(rec, dict):
                continue
            if str(rec.get("state") or "") != "running":
                continue
            page = str(rec.get("live_page_stage") or "")
            stage = str(rec.get("stage") or "")
            if page in hot_pages or stage == "business_query":
                n += 1
        return n

    def acquire(self, *, account_id: str, slot_id: str, round_id: str) -> LoginAdmissionDecision:
        if self.store is None:
            return LoginAdmissionDecision(True, lease_id=f"nogate-{uuid.uuid4().hex}", reason="no_store")

        guard_active, guard = self._account_guard_active()
        if guard_active:
            until = self._parse_ts(guard.get("block_until"))
            wait = max(60.0, (until - self._now()).total_seconds()) if until else 2700.0
            return LoginAdmissionDecision(False, wait_seconds=wait, reason="account_guard_active", message=str(guard.get("reason_zh") or "账号保护中"), state=guard)

        min_gap = max(60.0, float(getattr(self.cfg, "login_submit_min_gap_seconds", 600.0) or 600.0))
        lease_seconds = max(60.0, float(getattr(self.cfg, "login_submit_lease_seconds", 180.0) or 180.0))
        target_hot = max(0, int(getattr(self.cfg, "login_stop_when_hot_sessions", 3) or 3))
        hot = self._hot_count()
        if target_hot and hot >= target_hot:
            return LoginAdmissionDecision(False, wait_seconds=60.0, reason="hot_pool_sufficient", message=f"已有 {hot} 个首页/查询热会话，暂停新增登录", state={"hot_sessions": hot, "target_hot": target_hot})

        now = self._now()
        result_box: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            nonlocal result_box
            if not isinstance(state, dict):
                state = {}
            state.setdefault("accounts", {})
            acc = dict((state.get("accounts") or {}).get(account_id) or {})
            active = acc.get("active_lease") if isinstance(acc.get("active_lease"), dict) else {}
            active_until = self._parse_ts(active.get("expires_at")) if active else None
            if active and active_until and active_until > now and active.get("slot_id") != slot_id:
                wait = max(1.0, (active_until - now).total_seconds())
                result_box = {"ok": False, "wait_seconds": wait, "reason": "login_lease_busy", "message": f"登录令牌被 {active.get('slot_id')} 持有，门口等待", "state": {"active_lease": active, "hot_sessions": hot}}
                return result_box

            last_submit = self._parse_ts(acc.get("last_submit_at"))
            if last_submit:
                due = last_submit + timedelta(seconds=min_gap)
                if due > now:
                    wait = max(1.0, (due - now).total_seconds())
                    result_box = {"ok": False, "wait_seconds": wait, "reason": "login_submit_gap", "message": f"距离上次账号提交不足 {int(min_gap)}s，门口等待", "state": {"last_submit_at": acc.get("last_submit_at"), "next_allowed_at": due.isoformat(timespec="seconds"), "hot_sessions": hot}}
                    return result_box

            lease_id = uuid.uuid4().hex
            lease = {
                "lease_id": lease_id,
                "account_id": account_id,
                "slot_id": slot_id,
                "round_id": round_id,
                "acquired_at": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds"),
            }
            acc["active_lease"] = lease
            acc["last_acquired_at"] = lease["acquired_at"]
            state["accounts"][account_id] = acc
            result_box = {"ok": True, "lease_id": lease_id, "wait_seconds": 0.0, "reason": "granted", "message": "获得登录提交令牌", "state": {"active_lease": lease, "hot_sessions": hot}}
            return result_box

        try:
            self.store.mutate_login_admission(mutate)
        except Exception as exc:
            return LoginAdmissionDecision(False, wait_seconds=30.0, reason="admission_error", message=repr(exc), state={})
        return LoginAdmissionDecision(**result_box)

    def record_submit(self, *, account_id: str, lease_id: str, slot_id: str, clicked: bool) -> None:
        if not self.store or not clicked:
            return
        now = self._now().isoformat(timespec="seconds")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            state.setdefault("accounts", {})
            acc = dict((state.get("accounts") or {}).get(account_id) or {})
            acc["last_submit_at"] = now
            acc["last_submit_lease_id"] = lease_id
            acc["last_submit_slot_id"] = slot_id
            acc["submit_count"] = int(acc.get("submit_count") or 0) + 1
            state["accounts"][account_id] = acc
            return {"ok": True}

        try:
            self.store.mutate_login_admission(mutate)
        except Exception:
            pass

    def record_result(self, *, account_id: str, lease_id: str, slot_id: str, ok: bool, reason: str = "") -> None:
        if not self.store:
            return
        now = self._now().isoformat(timespec="seconds")

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            state.setdefault("accounts", {})
            acc = dict((state.get("accounts") or {}).get(account_id) or {})
            active = acc.get("active_lease") if isinstance(acc.get("active_lease"), dict) else {}
            if active.get("lease_id") == lease_id or active.get("slot_id") == slot_id:
                acc["active_lease"] = {}
            acc["last_result_at"] = now
            acc["last_result_ok"] = bool(ok)
            acc["last_result_reason"] = str(reason or "")[:200]
            state["accounts"][account_id] = acc
            return {"ok": True}

        try:
            self.store.mutate_login_admission(mutate)
        except Exception:
            pass
