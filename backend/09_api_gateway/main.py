from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .dependencies import container
from .routes import cleanup, config, events, pipeline, slots, system, tickets

app = FastAPI(title="美签 US Visa Refactor API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(pipeline.router)
app.include_router(slots.router)
app.include_router(events.router)
app.include_router(config.router)
app.include_router(tickets.router)
app.include_router(cleanup.router)

_root = Path(__file__).resolve().parents[2]
_storage = _root / "storage"
_storage.mkdir(parents=True, exist_ok=True)
app.mount("/storage", StaticFiles(directory=str(_storage)), name="storage")


@app.websocket("/ws/status")
async def status_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            snapshot = await asyncio.to_thread(lambda: container()["status"].snapshot())
            await ws.send_json(snapshot)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@app.get("/")
def root():
    return {"ok": True, "service": "usvisa_refactor", "docs": "/docs", "health": "/api/system/health"}
