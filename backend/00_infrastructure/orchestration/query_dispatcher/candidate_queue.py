from __future__ import annotations

from typing import Any

from .slot_health import SlotHealthAnalyzer

try:
    from ..session_reuse.reuse_health import ReuseHealthScorer
except Exception:  # pragma: no cover - import fallback for unusual test loaders
    ReuseHealthScorer = None


class CandidateQueue:
    """Build primary/backup candidates for dashboard and gate decisions."""

    def __init__(self, limit: int = 3):
        self.limit = max(1, int(limit or 3))
        self.analyzer = SlotHealthAnalyzer()
        self.reuse_scorer = ReuseHealthScorer() if ReuseHealthScorer else None

    def build(self, slots: dict[str, Any], sessions: dict[str, Any]) -> list[dict[str, Any]]:
        by_slot_session: dict[str, dict[str, Any]] = {}
        for session in sessions.values():
            if not isinstance(session, dict):
                continue
            slot_id = str(session.get("slot_id") or "")
            if not slot_id:
                continue
            old = by_slot_session.get(slot_id) or {}
            if str(session.get("last_completed_at") or session.get("last_reserved_at") or "") >= str(old.get("last_completed_at") or old.get("last_reserved_at") or ""):
                by_slot_session[slot_id] = session

        rows: list[dict[str, Any]] = []
        for slot_id, slot in slots.items():
            if not isinstance(slot, dict):
                continue
            sess = by_slot_session.get(slot_id)
            h = self.analyzer.score(slot, sess)
            row = {"slot_id": slot_id, **h.to_dict(), "next_query_at": (sess or {}).get("next_query_at", "")}
            if self.reuse_scorer:
                rh = self.reuse_scorer.score(slot, sess).to_dict()
                row.update(
                    {
                        "reuse_score": rh.get("reuse_score"),
                        "pool_role": rh.get("pool_role"),
                        "scheduler_status": rh.get("scheduler_status"),
                        "scheduler_status_zh": rh.get("scheduler_status_zh"),
                        "scheduler_reason": rh.get("reason"),
                        "query_eligible": rh.get("query_eligible"),
                    }
                )
                # Candidate selection is now hot-session first.  Cold sessions
                # may still be usable, but they should not outrank a proven
                # successful session that is ready to reuse.
                row["score"] = int(rh.get("reuse_score") or row.get("score") or 0)
                row["usable"] = bool(rh.get("query_eligible"))
                row["reason"] = str(rh.get("reason") or row.get("reason") or "")
            rows.append(row)
        rows.sort(
            key=lambda x: (
                1 if x.get("usable") else 0,
                1 if x.get("pool_role") == "hot_query" else 0,
                int(x.get("score") or 0),
                str(x.get("slot_id") or ""),
            ),
            reverse=True,
        )
        usable_rows = [r for r in rows if r.get("usable")]
        out = []
        for idx, row in enumerate(usable_rows[: self.limit]):
            role = "primary" if idx == 0 else f"backup_{idx}"
            out.append({**row, "role": role})
        return out
