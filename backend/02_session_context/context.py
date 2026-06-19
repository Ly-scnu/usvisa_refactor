from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from importlib import import_module

iso_now = import_module("00_infrastructure.utils.time").iso_now


@dataclass
class SessionContext:
    slot_id: str
    round_id: str
    session_id: str | None = None
    proxy: str | None = None
    current_stage: str = "created"
    cookies: list[dict[str, Any]] = field(default_factory=list)
    local_storage: dict[str, Any] = field(default_factory=dict)
    csrf_token: str | None = None
    app_id: str | None = None
    applications: list[str] = field(default_factory=list)
    post_id: str | None = None
    post_name: str | None = None
    runtime_config: Any | None = None
    account: Any | None = None
    proxy_material: Any | None = None
    browser_bundle: Any | None = None
    browser: Any | None = None
    browser_context: Any | None = None
    page: Any | None = None
    project_root: Path | None = None
    event_bus: Any | None = None
    store: Any | None = None
    booking_signal: dict[str, Any] | None = None
    last_business_result: dict[str, Any] | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    round_started_at: str = field(default_factory=iso_now)
    round_started_monotonic: float = field(default_factory=time.monotonic)

    def round_elapsed_s(self) -> float:
        """Elapsed seconds of the current scheduler round.

        Dashboard `elapsed_s` is round-level time, not per-stage wait time.
        Stage-local durations should use `stage_elapsed_s` when needed.
        """
        return round(max(0.0, time.monotonic() - self.round_started_monotonic), 1)

    def get_protocol_headers(self) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.usvisascheduling.com",
            "referer": "https://www.usvisascheduling.com/zh-CN/schedule/",
        }
        if self.csrf_token:
            headers["x-csrf-token"] = self.csrf_token
        return headers

    async def close(self) -> None:
        if self.browser_bundle:
            await self.browser_bundle.close()
