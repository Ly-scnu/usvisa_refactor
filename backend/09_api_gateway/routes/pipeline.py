from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import container

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.post("/start")
def start():
    return container()["producer"].start()


@router.post("/stop")
def stop():
    return container()["producer"].stop()


@router.post("/restart")
def restart():
    return container()["producer"].restart()
