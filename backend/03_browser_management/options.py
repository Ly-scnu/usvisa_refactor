from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any


DEFAULT_CLOAK_BROWSER_ROOT = Path(r"D:\OpenSands\tools\CloakBrowser-main")


@dataclass
class BrowserLaunchOptions:
    project_root: Path
    slot_id: str
    round_id: str
    headless: bool = True
    proxy_url: str | None = None
    profile_scope: str = "round"
    cloak_browser_root: Path = DEFAULT_CLOAK_BROWSER_ROOT
    slow_mo_ms: int = 0


@dataclass
class BrowserBundle:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    user_data_dir: Path
    executable_path: Path

    async def close(self) -> None:
        for obj in (self.context, self.browser):
            try:
                close = getattr(obj, "close", None)
                if close:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            except Exception:
                pass
        try:
            stop = getattr(self.playwright, "stop", None)
            if stop:
                result = stop()
                if hasattr(result, "__await__"):
                    await result
        except Exception:
            pass


def find_cloak_chrome(root: Path = DEFAULT_CLOAK_BROWSER_ROOT) -> Path:
    root = Path(root)
    candidates = sorted(root.glob(".cloakbrowser-cache/chromium-*/chrome.exe"), reverse=True)
    if candidates:
        return candidates[0]
    direct = root / "chrome.exe"
    if direct.exists():
        return direct
    raise FileNotFoundError(f"CloakBrowser chrome.exe not found under {root}")


def playwright_proxy(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    if not u.scheme or not u.hostname:
        return None
    server = f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"
    out = {"server": server}
    if u.username:
        out["username"] = unquote(u.username)
    if u.password:
        out["password"] = unquote(u.password)
    return out


def profile_dir_for(opts: BrowserLaunchOptions) -> Path:
    base = opts.project_root / "storage" / "browser_profiles"
    if opts.profile_scope in {"slot_stable", "stable"}:
        return base / opts.slot_id / "stable"
    if opts.profile_scope == "slot":
        return base / opts.slot_id
    return base / opts.slot_id / opts.round_id
