from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..database.db import Database
from ..utils.jsonio import append_jsonl, read_jsonl_tail, safe_json_data
from ..utils.time import iso_now


@dataclass
class Event:
    event_type: str
    slot_id: str | None = None
    session_id: str | None = None
    round_id: str | None = None
    stage: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "slot_id": self.slot_id,
            "session_id": self.session_id,
            "round_id": self.round_id,
            "stage": self.stage,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class EventBus:
    def __init__(self, db: Database, events_path: Path):
        self.db = db
        self.events_path = events_path
        self.subscribers: dict[str, list[Callable[[Event], None]]] = {}

    def publish(self, event: Event) -> Event:
        event.payload = safe_json_data(event.payload)
        row = event.to_dict()
        append_jsonl(self.events_path, row)
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO events(event_id,event_type,slot_id,session_id,round_id,stage,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.event_type,
                    event.slot_id,
                    event.session_id,
                    event.round_id,
                    event.stage,
                    json.dumps(safe_json_data(event.payload), ensure_ascii=False),
                    event.created_at,
                ),
            )
            conn.commit()
        for cb in self.subscribers.get(event.event_type, []):
            try:
                cb(event)
            except Exception:
                pass
        return event

    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        self.subscribers.setdefault(event_type, []).append(callback)

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return read_jsonl_tail(self.events_path, limit)
