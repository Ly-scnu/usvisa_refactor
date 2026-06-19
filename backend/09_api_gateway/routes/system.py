from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import container

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    cfg = container()["config"]
    return {"ok": True, "name": cfg.system.name, "api_port": cfg.system.api_port, "mode": "standalone"}


@router.get("/status")
def status():
    return container()["status"].snapshot()
