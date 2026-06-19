from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

from .options import BrowserBundle, BrowserLaunchOptions, find_cloak_chrome, playwright_proxy, profile_dir_for


async def launch_cloak_browser(opts: BrowserLaunchOptions) -> BrowserBundle:
    """Launch the required CloakBrowser Chromium through Playwright.

    This is the only browser launcher used by the real one-dragon pipeline.
    """
    executable = find_cloak_chrome(opts.cloak_browser_root)
    user_data_dir = profile_dir_for(opts)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    launch_args: list[str] = [
        "--window-size=1365,900",
    ]

    # Use the CloakBrowser Python wrapper, not raw Playwright.  The wrapper
    # applies the source-level fingerprint/stealth flags and proxy handling
    # that the original project depended on; raw Playwright leaves the managed
    # Cloudflare challenge spinning forever in this sandbox.
    root = Path(opts.cloak_browser_root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Force the wrapper to use the binary from this configured tools directory.
    # Otherwise cloakbrowser.ensure_binary() can choose the user's default
    # ~/.cloakbrowser cache even though the wrapper source was imported from
    # D:\OpenSands\tools\CloakBrowser-main.
    os.environ["CLOAKBROWSER_BINARY_PATH"] = str(executable)
    from cloakbrowser import launch_persistent_context_async  # type: ignore

    context = await launch_persistent_context_async(
        str(user_data_dir),
        headless=opts.headless,
        proxy=opts.proxy_url,
        args=launch_args,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        viewport={"width": 1365, "height": 900},
        humanize=True,
        human_preset="careful",
        ignore_https_errors=True,
    )
    browser = getattr(context, "browser", None)
    page = context.pages[0] if context.pages else await context.new_page()
    return BrowserBundle(playwright=None, browser=browser, context=context, page=page, user_data_dir=user_data_dir, executable_path=executable)
