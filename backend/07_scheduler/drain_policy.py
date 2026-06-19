from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


def _parse_slot_no(slot_id: str) -> int:
    try:
        return int(str(slot_id).split("_")[-1])
    except Exception:
        return 0


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except Exception:
        return None


@dataclass
class DrainCandidate:
    slot_id: str
    score: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SlotDrainPolicy:
    """Pick excess slot workers to drain without killing valuable warm sessions.

    Drain is a producer-level capacity tool.  It only requests a slot to stop at
    a safe boundary; the SlotRunner owns the actual exit so business/API actions
    are not interrupted mid-critical step.
    """

    PROTECTED_STAGES = {"business_query", "booking_ready", "waiting_room"}

    def __init__(self, config: Any, store: Any):
        self.config = config
        self.store = store
        self.cfg = getattr(config, "smart_orchestrator", None)

    def _live_session_key(self, slot_id: str, slot: dict[str, Any]) -> str:
        round_no = int(slot.get("round") or 0)
        started_at = str(slot.get("round_started_at") or "")
        if round_no > 0 and started_at:
            return f"{slot_id}/round_{round_no:04d}/{started_at}"
        return ""

    def choose(self, *, active: int, desired: int) -> list[DrainCandidate]:
        if active <= desired or not bool(getattr(self.cfg, "drain_enabled", True)):
            return []
        slots = self.store.read_slots() if self.store else {}
        scheduler = self.store.scheduler_state() if self.store else {}
        sessions = scheduler.get("sessions") if isinstance(scheduler.get("sessions"), dict) else {}
        active_query = scheduler.get("active_query") if isinstance(scheduler.get("active_query"), dict) else None
        active_query_slot = str(active_query.get("slot_id") or "") if active_query else ""
        candidates: list[DrainCandidate] = []
        for slot_id, slot in slots.items():
            if not isinstance(slot, dict) or slot.get("state") != "running":
                continue
            if slot_id == active_query_slot:
                continue
            stage = str(slot.get("stage") or "")
            if stage in self.PROTECTED_STAGES:
                continue
            key = self._live_session_key(slot_id, slot)
            sess = sessions.get(key) if key else None
            success_count = int(sess.get("success_count") or 0) if isinstance(sess, dict) else int(slot.get("session_successful_queries") or 0)
            score = _parse_slot_no(slot_id)
            reason = "高编号未产出槽，优先排水"
            if success_count > 0:
                score -= 100
                reason = "已有成功查询的可复用会话，默认保护，仅最后排水"
            if stage in {"round_recycle", "direct_only_recycle", "stopped", "pending"}:
                score += 20
                reason = "处于回收/等待边界，可安全排水"
            if str(slot.get("drain_requested") or "").lower() == "true" or slot.get("drain_requested") is True:
                score += 50
                reason = "已请求排水，继续等待槽位退出"
            candidates.append(DrainCandidate(slot_id, score, reason))
        candidates.sort(key=lambda x: (x.score, _parse_slot_no(x.slot_id)), reverse=True)
        return candidates[: max(0, active - desired)]
