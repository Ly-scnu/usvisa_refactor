from __future__ import annotations

from pathlib import Path
from typing import Any

import toml

from .models import AppConfig


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return toml.load(str(path))


def load_config(project_root: Path | None = None) -> AppConfig:
    if project_root is not None:
        root = Path(project_root)
    else:
        here = Path(__file__).resolve()
        root = here.parents[4]
        for p in here.parents:
            if (p / "config" / "app.toml").exists():
                root = p
                break
    cfg_dir = root / "config"
    app_raw = _read_toml(cfg_dir / "app.toml")
    accounts_raw = _read_toml(cfg_dir / "accounts.toml")
    proxy_raw = _read_toml(cfg_dir / "proxy.toml")
    config = AppConfig.model_validate(
        {
            "project_root": root,
            "system": app_raw.get("system", {}),
            "target": app_raw.get("target", {}),
            "slots": app_raw.get("slots", {}),
            "producer": app_raw.get("producer", {}),
            "smart_orchestrator": app_raw.get("smart_orchestrator", {}),
            "booking": app_raw.get("booking", {}),
            "accounts": accounts_raw.get("accounts", []),
            "proxy": proxy_raw,
        }
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config
