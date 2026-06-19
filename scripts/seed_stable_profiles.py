"""Seed stable browser profiles from the most recent successful query rounds.

Why: account-level B2C blocks are triggered by credential submissions.  The
safest recovery path is to reuse already-authenticated browser state and avoid
submitting credentials unless absolutely necessary.

This script is offline: it only copies local Chromium profile directories.  It
never opens the site and never submits credentials.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SUCCESS = ROOT / "storage" / "runtime" / "query_success_records.jsonl"
PROFILES = ROOT / "storage" / "browser_profiles"


def load_successes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not SUCCESS.exists():
        return rows
    for line in SUCCESS.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("ok") is True or obj.get("valid_query_success") is True or int(obj.get("days_count") or 0) > 0:
            slot = str(obj.get("slot_id") or "")
            rnd = str(obj.get("round_id") or "")
            if slot and rnd:
                rows.append(obj)
    rows.sort(key=lambda x: str(x.get("queried_at") or x.get("created_at") or ""), reverse=True)
    return rows


def copy_profile(src: Path, dst: Path, *, overwrite: bool) -> bool:
    if not src.exists() or not (src / "Default").exists():
        return False
    if dst.exists() and overwrite:
        shutil.rmtree(dst)
    if dst.exists() and not overwrite:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("Singleton*", "*.tmp", "CrashpadMetrics-active.pma")
    shutil.copytree(src, dst, ignore=ignore)
    return True


def main() -> None:
    successes = load_successes()
    used_slots: set[str] = set()
    made: list[dict[str, str]] = []
    for obj in successes:
        slot = str(obj.get("slot_id") or "")
        rnd = str(obj.get("round_id") or "")
        if not slot or not rnd or slot in used_slots:
            continue
        src = PROFILES / slot / rnd
        dst = PROFILES / slot / "stable"
        # Always refresh stable from the newest known-successful round.  The
        # stable profile is the one used by profile_scope=slot_stable.
        if copy_profile(src, dst, overwrite=True):
            used_slots.add(slot)
            made.append({
                "slot": slot,
                "round": rnd,
                "queried_at": str(obj.get("queried_at") or ""),
                "src": str(src),
                "dst": str(dst),
            })
        if len(made) >= 10:
            break
    out = ROOT / "storage" / "runtime" / "seeded_stable_profiles.json"
    out.write_text(json.dumps({"count": len(made), "profiles": made}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(made), "profiles": made}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
