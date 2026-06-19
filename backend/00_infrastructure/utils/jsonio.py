from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def safe_json_data(value: Any, *, max_depth: int = 8) -> Any:
    """Return a JSON-safe copy of arbitrary runtime payloads.

    Runtime events can accidentally receive Exception/Page/Context objects or
    circular dict/list references from recovery paths.  Those must never break
    the scheduler/dashboard persistence layer; keep useful scalar data and
    degrade unsupported objects to repr().
    """

    seen: set[int] = set()

    def walk(obj: Any, depth: int) -> Any:
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if depth <= 0:
            return repr(obj)
        oid = id(obj)
        if isinstance(obj, (dict, list, tuple, set)):
            if oid in seen:
                return "<circular>"
            seen.add(oid)
            try:
                if isinstance(obj, dict):
                    out: dict[str, Any] = {}
                    for k, v in obj.items():
                        key = str(k)
                        out[key] = walk(v, depth - 1)
                    return out
                return [walk(v, depth - 1) for v in obj]
            finally:
                seen.discard(oid)
        try:
            json.dumps(obj)
            return obj
        except Exception:
            return repr(obj)

    return walk(value, max_depth)


def load_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig", errors="ignore"))
    except Exception:
        return default


def atomic_write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(safe_json_data(data), ensure_ascii=False, indent=2)
    last: Exception | None = None
    for i in range(20):
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.time_ns()}.{i}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
            return
        except PermissionError as exc:
            last = exc
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            time.sleep(0.03 * (i + 1))
    if last:
        raise last


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(safe_json_data(row), ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl_tail(path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    if limit <= 0:
        return []
    # Do not read large runtime logs fully.  events.jsonl can grow to hundreds
    # of MB during long runs; loading it all made /api/tickets/analytics and
    # the WebSocket status endpoint stall for tens of seconds.  Read backwards
    # until enough newline-delimited records are available.
    try:
        chunk_size = 1024 * 64
        data = bytearray()
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            newline_count = 0
            while pos > 0 and newline_count <= limit:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                data[:0] = chunk
                newline_count = data.count(b"\n")
        raw_lines = data.splitlines()[-limit:]
    except Exception:
        raw_lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    out: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out
