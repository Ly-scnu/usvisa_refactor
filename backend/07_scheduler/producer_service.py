from __future__ import annotations

from importlib import import_module
import json
import os
import subprocess
import sys
import time
from typing import Any

AppConfig = import_module("00_infrastructure.config.models").AppConfig
_event_mod = import_module("00_infrastructure.events.event_bus")
Event = _event_mod.Event
EventBus = _event_mod.EventBus
_jsonio = import_module("00_infrastructure.utils.jsonio")
atomic_write_json = _jsonio.atomic_write_json
load_json = _jsonio.load_json
StateStore = import_module("00_infrastructure.runtime.state_store").StateStore


class ProducerService:
    def __init__(self, config: AppConfig, event_bus: EventBus, store: StateStore):
        self.config = config
        self.event_bus = event_bus
        self.store = store
        self.pid_file = config.data_dir / "runtime" / "producer.pid"
        self.log_file = config.data_dir / "logs" / "producer_worker.log"
        self.proc: subprocess.Popen[Any] | None = None

    def _pid_running(self, pid: int) -> bool:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], text=True, capture_output=True, timeout=5)
            return str(pid) in out.stdout
        except Exception:
            return False

    def _worker_pids_by_cmdline(self) -> list[int]:
        """Find producer workers for this project root.

        The API pid file can miss manually started workers.  Those extra
        workers caused duplicate slot runners and broke the business query
        gate, so stop/restart must consider command line evidence too.
        """
        root = str(self.config.project_root)
        try:
            ps = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match '07_scheduler.producer_worker' } | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            )
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], text=True, capture_output=True, timeout=10)
            if not out.stdout.strip():
                return []
            data = json.loads(out.stdout)
            rows = data if isinstance(data, list) else [data]
            pids: list[int] = []
            for row in rows:
                cmd = str(row.get("CommandLine") or "")
                if root in cmd:
                    pids.append(int(row.get("ProcessId")))
            return sorted(set(pids))
        except Exception:
            return []

    def is_running(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return True
        pid = int(load_json(self.pid_file, 0) or 0) if self.pid_file.exists() else 0
        return bool((pid and self._pid_running(pid)) or self._worker_pids_by_cmdline())

    def start(self) -> dict[str, Any]:
        if self.is_running():
            return {"ok": True, "already_running": True, "pid": load_json(self.pid_file, None)}
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_file.open("a", encoding="utf-8", errors="replace")
        cmd = [sys.executable, "-m", "07_scheduler.producer_worker", "--project-root", str(self.config.project_root)]
        backend_dir = str(self.config.project_root / "backend")
        env = os.environ.copy()
        env["PYTHONPATH"] = backend_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.config.project_root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        atomic_write_json(self.pid_file, self.proc.pid)
        self.store.set_pipeline(running=True, pid=self.proc.pid, mode="standalone")
        self.event_bus.publish(Event("pipeline_started", payload={"pid": self.proc.pid, "mode": "standalone"}))
        return {"ok": True, "pid": self.proc.pid, "mode": "standalone"}

    def stop(self) -> dict[str, Any]:
        pid = self.proc.pid if self.proc and self.proc.poll() is None else int(load_json(self.pid_file, 0) or 0)
        pids = sorted(set(([pid] if pid else []) + self._worker_pids_by_cmdline()))
        if not pids:
            self.store.set_pipeline(running=False, mode="standalone")
            return {"ok": True, "already_stopped": True}
        errors: list[str] = []
        for one in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(one), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=20)
            except Exception as exc:
                errors.append(f"{one}:{repr(exc)}")
        self.store.set_pipeline(running=False, pid=pids[-1], stopped_pids=pids, mode="standalone")
        self.event_bus.publish(Event("pipeline_stopped", payload={"pids": pids, "mode": "standalone", "errors": errors}))
        return {"ok": not errors, "pid": pids[-1], "pids": pids, "errors": errors}

    def restart(self) -> dict[str, Any]:
        stopped = self.stop()
        time.sleep(1)
        started = self.start()
        return {"ok": bool(started.get("ok")), "stopped": stopped, "started": started}

    def slot_command(self, slot: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        slot_id = slot if slot.startswith("slot_") else f"slot_{int(slot):02d}"
        cmd = self.store.append_command(slot_id, action, payload)
        self.event_bus.publish(Event("slot_command_queued", slot_id=slot_id, payload=cmd))
        return {"ok": True, "cmd": cmd}
