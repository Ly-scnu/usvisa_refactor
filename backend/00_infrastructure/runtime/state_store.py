from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from ..config.models import AppConfig
from .process_lock import FileProcessLock
from ..utils.jsonio import append_jsonl, atomic_write_json, load_json, read_jsonl_tail
from ..utils.time import iso_now


class StateStore:
    """Project-local runtime state store.

    This is the only source read by the refactor dashboard. It never reads the
    old practical-test directory.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.runtime_dir = config.data_dir / "runtime"
        self.command_dir = self.runtime_dir / "commands"
        self.slot_status_path = self.runtime_dir / "slot_status.json"
        self.pipeline_status_path = self.runtime_dir / "pipeline_status.json"
        self.ticket_latest_path = self.runtime_dir / "latest_ticket.json"
        self.ticket_history_path = self.runtime_dir / "ticket_history.jsonl"
        self.query_success_records_path = self.runtime_dir / "query_success_records.jsonl"
        self.booking_signal_path = self.runtime_dir / "booking_signal.json"
        self.scheduler_state_path = self.runtime_dir / "smart_scheduler_state.json"
        self.query_handoff_path = self.runtime_dir / "query_handoff.json"
        self.route_health_path = self.runtime_dir / "route_health.json"
        self.account_guard_path = self.runtime_dir / "account_guard.json"
        self.login_admission_path = self.runtime_dir / "login_admission.json"
        self.scheduler_lock_path = self.runtime_dir / "locks" / "smart_scheduler_state.lock"
        self.route_health_lock_path = self.runtime_dir / "locks" / "route_health.lock"
        self.login_admission_lock_path = self.runtime_dir / "locks" / "login_admission.lock"
        self.sla_state_path = self.runtime_dir / "sla_orchestrator_state.json"
        self.availability_path = self.runtime_dir / "availability.txt"
        self.events_path = config.data_dir / "logs" / "events.jsonl"
        self.command_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def init_slots(self) -> None:
        with self._lock:
            slots: dict[str, Any] = {}
            smart = getattr(self.config, "smart_orchestrator", None)
            count = self.config.slots.total_slots
            if bool(getattr(smart, "enabled", False)):
                count = max(count, int(getattr(smart, "max_slots", count) or count))
            for i in range(1, count + 1):
                slot = f"slot_{i:02d}"
                slots[slot] = {
                    "slot": slot,
                    "state": "pending",
                    "stage": "pending",
                    "stage_zh": "等待启动",
                    "round": 0,
                    "elapsed_s": 0,
                    "waiting_acquired": False,
                    "updated_at": iso_now(),
                }
            atomic_write_json(self.slot_status_path, {"updated_at": iso_now(), "slots": slots})

    def read_slots(self) -> dict[str, Any]:
        data = load_json(self.slot_status_path, {}) or {}
        slots = data.get("slots") if isinstance(data, dict) else {}
        return slots if isinstance(slots, dict) else {}

    def update_slot(self, slot: str, **patch: Any) -> None:
        with self._lock:
            data = load_json(self.slot_status_path, {}) or {}
            slots = data.setdefault("slots", {})
            rec = dict(slots.get(slot) or {"slot": slot})
            rec.update(patch)
            rec["updated_at"] = iso_now()
            slots[slot] = rec
            data["updated_at"] = iso_now()
            atomic_write_json(self.slot_status_path, data)

    def clear_slot_drain(self, slot: str, *, reason: str = "new_generation") -> None:
        """Clear stale drain markers before a new slot generation/round starts.

        Drain is an instruction to stop the *current* worker at a safe boundary.
        The dashboard state file is persistent, so those flags must not leak
        into the next SlotRunner generation; otherwise Pipeline._drain_requested
        will skip every stage immediately after scale-up.
        """
        with self._lock:
            data = load_json(self.slot_status_path, {}) or {}
            slots = data.setdefault("slots", {})
            rec = dict(slots.get(slot) or {"slot": slot})
            rec["drain_requested"] = False
            rec["drain_reason"] = ""
            rec["drain_cleared_at"] = iso_now()
            rec["drain_clear_reason"] = reason
            rec["updated_at"] = iso_now()
            slots[slot] = rec
            data["updated_at"] = iso_now()
            atomic_write_json(self.slot_status_path, data)

    def scheduler_state(self) -> dict[str, Any]:
        data = load_json(self.scheduler_state_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def mutate_scheduler_state(self, mutator: Any) -> Any:
        """Atomically mutate smart scheduler state.

        The producer currently runs slots as threads in one process.  Keeping
        this mutation under the same StateStore lock is enough to prevent two
        slots from reserving the business API gate at the same time, while the
        JSON file keeps dashboard/API visibility and restart continuity.
        """
        with FileProcessLock(self.scheduler_lock_path):
            with self._lock:
                data = load_json(self.scheduler_state_path, {}) or {}
                if not isinstance(data, dict):
                    data = {}
                result = mutator(data)
                data["updated_at"] = iso_now()
                atomic_write_json(self.scheduler_state_path, data)
                return result

    def query_handoff(self) -> dict[str, Any]:
        data = load_json(self.query_handoff_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def write_query_handoff(self, payload: dict[str, Any]) -> None:
        rec = dict(payload or {})
        rec.setdefault("updated_at", iso_now())
        atomic_write_json(self.query_handoff_path, rec)

    def route_health(self) -> dict[str, Any]:
        data = load_json(self.route_health_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def account_guard(self) -> dict[str, Any]:
        data = load_json(self.account_guard_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def write_account_guard(self, payload: dict[str, Any]) -> None:
        rec = dict(payload or {})
        rec["updated_at"] = iso_now()
        atomic_write_json(self.account_guard_path, rec)

    def mutate_route_health(self, mutator: Any) -> Any:
        with FileProcessLock(self.route_health_lock_path):
            with self._lock:
                data = load_json(self.route_health_path, {}) or {}
                if not isinstance(data, dict):
                    data = {}
                result = mutator(data)
                data["updated_at"] = iso_now()
                atomic_write_json(self.route_health_path, data)
                return result

    def login_admission(self) -> dict[str, Any]:
        data = load_json(self.login_admission_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def mutate_login_admission(self, mutator: Any) -> Any:
        with FileProcessLock(self.login_admission_lock_path):
            with self._lock:
                data = load_json(self.login_admission_path, {}) or {}
                if not isinstance(data, dict):
                    data = {}
                result = mutator(data)
                data["updated_at"] = iso_now()
                atomic_write_json(self.login_admission_path, data)
                return result

    def sla_state(self) -> dict[str, Any]:
        data = load_json(self.sla_state_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def write_sla_state(self, payload: dict[str, Any]) -> None:
        rec = dict(payload or {})
        rec["updated_at"] = iso_now()
        atomic_write_json(self.sla_state_path, rec)

    def pipeline_status(self) -> dict[str, Any]:
        return load_json(self.pipeline_status_path, {"running": False}) or {"running": False}

    def set_pipeline(self, **patch: Any) -> None:
        with self._lock:
            data = self.pipeline_status()
            data.update(patch)
            data["updated_at"] = iso_now()
            atomic_write_json(self.pipeline_status_path, data)

    def latest_ticket(self) -> dict[str, Any]:
        return load_json(self.ticket_latest_path, {}) or {}

    def write_latest_ticket(self, payload: dict[str, Any]) -> None:
        rec = dict(payload or {})
        rec.setdefault("queried_at", iso_now())
        rec.setdefault("query_source", "business_query")
        rec.setdefault("days_count", len(rec.get("days") or []))
        rec.setdefault("slots_count", len(rec.get("slots") or []))
        rec.setdefault("matched_count", len(rec.get("matched_slots") or []))
        atomic_write_json(self.ticket_latest_path, rec)

        # The dashboard needs a ticket-pool timeline, not only the newest
        # successful payload.  Avoid appending the same live probe twice when
        # both stage04_query and pipeline persist the same result.
        last = (read_jsonl_tail(self.ticket_history_path, 1) or [{}])[-1]
        if last.get("ticket_query_id") and last.get("ticket_query_id") == rec.get("ticket_query_id"):
            return
        append_jsonl(self.ticket_history_path, rec)
        if bool(rec.get("valid_query_success")):
            self.append_query_success_record(rec)

    def ticket_history(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = read_jsonl_tail(self.ticket_history_path, limit)
        if rows:
            return rows
        latest = self.latest_ticket()
        if latest and (latest.get("days") is not None or latest.get("matched_slots") is not None or latest.get("slots") is not None):
            rec = dict(latest)
            observed = rec.get("ts") or ""
            if not observed:
                try:
                    for line in self.availability_text().splitlines():
                        if line.startswith("updated_at="):
                            observed = line.split("=", 1)[1].strip()
                            break
                except Exception:
                    observed = ""
            rec.setdefault("queried_at", observed or iso_now())
            try:
                for line in self.availability_text().splitlines():
                    if line.startswith("slot="):
                        parts = line.split()
                        for part in parts:
                            if part.startswith("slot="):
                                rec.setdefault("slot_id", part.split("=", 1)[1])
                            if part.startswith("round="):
                                rec.setdefault("round_id", part.split("=", 1)[1])
                            if part.startswith("live_round="):
                                rec.setdefault("live_round", part.split("=", 1)[1])
                        break
            except Exception:
                pass
            rec.setdefault("ticket_query_id", "legacy_latest_ticket")
            return [rec]
        return []

    def append_query_success_record(self, payload: dict[str, Any]) -> None:
        days = payload.get("normalized_days") or payload.get("days") or []
        if not days:
            return
        qid = str(payload.get("ticket_query_id") or "")
        last = (read_jsonl_tail(self.query_success_records_path, 1) or [{}])[-1]
        if qid and last.get("ticket_query_id") == qid:
            return
        seq = int(last.get("seq") or 0) + 1
        rec = {
            "seq": seq,
            "ts": iso_now(),
            "ticket_query_id": qid,
            "live_ticket_id": payload.get("live_ticket_id") or "",
            "slot_id": payload.get("slot_id") or "",
            "round_id": payload.get("round_id") or "",
            "round_started_at": payload.get("round_started_at") or "",
            "live_round": payload.get("live_round") or "",
            "queried_at": payload.get("queried_at") or iso_now(),
            "post_name": payload.get("post_name") or "",
            "post_id": payload.get("post_id") or "",
            "proxy_display": payload.get("proxy_display") or "",
            "proxy_session": payload.get("proxy_session") or "",
            "route": payload.get("route") or "",
            "route_key": payload.get("route_key") or "",
            "proxy": payload.get("proxy") if isinstance(payload.get("proxy"), dict) else {},
            "nearest_date": sorted([str(x)[:10] for x in days if str(x or "").strip()])[0] if days else "",
            "days_count": len(days),
            "days_preview": list(days)[:10],
            "target_hit": bool(payload.get("target_hit")),
            "clicked_date": bool(payload.get("clicked_date")),
            "matched_count": len(payload.get("matched_slots") or []),
            "flow_summary": " → ".join(
                [
                    str(step.get("name") or step.get("reason") or ((step.get("state") or {}).get("stage") if isinstance(step.get("state"), dict) else "") or "step")
                    for step in (payload.get("steps") or [])[-8:]
                    if isinstance(step, dict)
                ][-6:]
            ),
        }
        append_jsonl(self.query_success_records_path, rec)

    def query_success_records(self, *, limit: int = 100, after_seq: int = 0) -> list[dict[str, Any]]:
        rows = read_jsonl_tail(self.query_success_records_path, max(limit, 1000 if after_seq else limit))
        if after_seq:
            rows = [r for r in rows if int(r.get("seq") or 0) > after_seq]
        return rows[-limit:]

    def ticket_query_count_today(self) -> int:
        today = iso_now()[:10]
        return sum(1 for row in self.ticket_history(1000) if str(row.get("queried_at") or row.get("ts") or "").startswith(today))

    def booking_signal(self) -> dict[str, Any]:
        return load_json(self.booking_signal_path, {}) or {}

    def write_booking_signal(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.booking_signal_path, payload)

    def availability_text(self) -> str:
        try:
            return self.availability_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def write_availability_text(self, text: str) -> None:
        self.availability_path.parent.mkdir(parents=True, exist_ok=True)
        self.availability_path.write_text(text, encoding="utf-8", errors="replace")

    def append_command(self, slot: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        cmd = {"id": f"{iso_now()}_{slot}_{action}", "ts": iso_now(), "slot": slot, "action": action, **(payload or {})}
        append_jsonl(self.command_dir / f"{slot}.jsonl", cmd)
        return cmd

    def read_new_commands(self, slot: str, seen: set[str]) -> list[dict[str, Any]]:
        rows = read_jsonl_tail(self.command_dir / f"{slot}.jsonl", 200)
        out: list[dict[str, Any]] = []
        for row in rows:
            cid = str(row.get("id") or "")
            if cid and cid not in seen:
                seen.add(cid)
                out.append(row)
        return out

    def events_tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return read_jsonl_tail(self.events_path, limit)
