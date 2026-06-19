from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import toml
from fastapi import APIRouter, Body, HTTPException

from ..dependencies import container
from .. import dependencies

AppConfig = dependencies.load_config.__globals__["AppConfig"]

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    cfg = container()["config"]
    return _config_to_public_dict(cfg)


@router.put("")
def save_config(payload: dict[str, Any] = Body(...)):
    """
    Persist the visual configuration editor back to config/*.toml.

    The API accepts the same shape returned by GET /api/config.  The payload is
    merged with current config, validated as AppConfig, then written to:
      - config/app.toml
      - config/accounts.toml
      - config/proxy.toml

    The in-process API container is updated immediately; a running producer
    worker still needs restart to pick up the saved TOML because workers load
    config at process start.
    """
    current = container()["config"]
    root = current.project_root
    merged = _config_to_full_dict(current)
    for section in ("system", "target", "slots", "producer", "smart_orchestrator", "booking", "accounts", "proxy"):
        if section in payload:
            merged[section] = payload[section]
    try:
        next_cfg = AppConfig.model_validate({"project_root": root, **merged})
    except Exception as exc:  # pydantic validation errors are returned as concise 400 text
        raise HTTPException(status_code=400, detail=f"invalid config: {exc}") from exc

    _write_config_files(root, next_cfg)
    _swap_runtime_config(next_cfg)
    return {"ok": True, "message": "config saved to config/*.toml", "config": _config_to_public_dict(next_cfg)}


@router.post("/reload")
def reload_config():
    root = container()["config"].project_root
    next_cfg = dependencies.load_config(root)
    _swap_runtime_config(next_cfg)
    return {"ok": True, "message": "config reloaded from config/*.toml", "config": _config_to_public_dict(next_cfg)}


def _config_to_public_dict(cfg: Any) -> dict[str, Any]:
    # Deliberately include editable credentials. This is a local sandbox UI and
    # config page is the source-of-truth editor requested by the operator.
    return {
        "system": cfg.system.model_dump(),
        "target": cfg.target.model_dump(),
        "slots": cfg.slots.model_dump(),
        "producer": cfg.producer.model_dump(),
        "smart_orchestrator": cfg.smart_orchestrator.model_dump(),
        "booking": cfg.booking.model_dump(),
        "accounts": [a.model_dump() for a in cfg.accounts],
        "proxy": {
            "provider": cfg.proxy.provider.model_dump(),
            "routes": [r.model_dump() for r in cfg.proxy.routes],
        },
    }


def _config_to_full_dict(cfg: Any) -> dict[str, Any]:
    return deepcopy(_config_to_public_dict(cfg))


def _write_config_files(root: Path, cfg: Any) -> None:
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    _atomic_toml_dump(
        cfg_dir / "app.toml",
        {
            "system": cfg.system.model_dump(),
            "target": cfg.target.model_dump(),
            "slots": cfg.slots.model_dump(),
            "producer": cfg.producer.model_dump(),
            "smart_orchestrator": cfg.smart_orchestrator.model_dump(),
            "booking": cfg.booking.model_dump(),
        },
    )
    _atomic_toml_dump(cfg_dir / "accounts.toml", {"accounts": [a.model_dump() for a in cfg.accounts]})
    _atomic_toml_dump(
        cfg_dir / "proxy.toml",
        {"provider": cfg.proxy.provider.model_dump(), "routes": [r.model_dump() for r in cfg.proxy.routes]},
    )


def _atomic_toml_dump(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        toml.dump(data, f)
    tmp.replace(path)


def _swap_runtime_config(next_cfg: Any) -> None:
    c = container()
    c["config"] = next_cfg
    # Update already constructed services so API status reflects changes
    # immediately without needing to restart uvicorn.
    if "store" in c:
        c["store"].config = next_cfg
    if "producer" in c:
        c["producer"].config = next_cfg
        c["producer"].pid_file = next_cfg.data_dir / "runtime" / "producer.pid"
        c["producer"].log_file = next_cfg.data_dir / "logs" / "producer_worker.log"
    if "status" in c:
        c["status"].config = next_cfg
