from __future__ import annotations

from importlib import import_module
from pathlib import Path

import uvicorn

load_config = import_module("00_infrastructure.config.loader").load_config

if __name__ == "__main__":
    cfg = load_config(Path(__file__).resolve().parents[1])
    uvicorn.run("09_api_gateway.main:app", host=cfg.system.api_host, port=cfg.system.api_port, reload=False)
