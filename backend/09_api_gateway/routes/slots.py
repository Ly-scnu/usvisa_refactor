from __future__ import annotations

from fastapi import APIRouter

from ..dependencies import container

router = APIRouter(prefix="/api/slots", tags=["slots"])


@router.get("")
def list_slots():
    return {"slots": list(container()["store"].read_slots().values())}


@router.post("/{slot_id}/commands/{action}")
def command(slot_id: str, action: str):
    action_map = {
        "snapshot": "snapshot_now",
        "reload": "reload_now",
        "kill": "kill_round",
        "restart": "kill_round",
        "headed": "set_headed",
        "headless": "set_headless",
        "show": "show_window",
        "hide": "hide_window",
    }
    return container()["producer"].slot_command(slot_id, action_map.get(action, action))
