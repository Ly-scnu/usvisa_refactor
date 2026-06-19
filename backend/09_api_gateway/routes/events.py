from __future__ import annotations

from fastapi import APIRouter, Query

from ..dependencies import container

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def events(limit: int = Query(100, ge=1, le=500)):
    c = container()
    return {"events": c["event_bus"].tail(limit)}
