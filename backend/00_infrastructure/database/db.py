from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT,
  event_type TEXT NOT NULL,
  slot_id TEXT,
  session_id TEXT,
  round_id TEXT,
  stage TEXT,
  payload TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_slot ON events(slot_id);
CREATE TABLE IF NOT EXISTS slots (
  slot_id TEXT PRIMARY KEY,
  status TEXT,
  current_stage TEXT,
  payload TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY,
  session_id TEXT,
  post_name TEXT,
  post_id TEXT,
  query_date TEXT,
  query_time TEXT,
  entries_available INTEGER,
  hit_target INTEGER,
  booking_result TEXT,
  payload TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT,
  message TEXT,
  context TEXT,
  created_at TEXT
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
