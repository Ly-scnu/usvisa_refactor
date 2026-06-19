from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any

iso_now = import_module("00_infrastructure.utils.time").iso_now


async def _wait_visual_stable(page: Any, *, stable_ms: int = 650, max_ms: int = 2600) -> None:
    """Wait briefly until the current browser page is visually worth capturing.

    A plain ``page.screenshot`` right after a stage transition can catch a
    half-rendered page (blank shell, spinner-only frame, or pre-paint DOM).  The
    dashboard uses screenshots as evidence, so the snapshot layer owns a small
    reusable stability wait instead of scattering hard sleeps across stages.

    The wait is intentionally bounded: CF/waiting-room pages can keep network
    activity alive, so we only wait for DOM readiness, two paint frames, fonts if
    available, and a short mutation-quiet window.
    """
    try:
        if callable(getattr(page, "is_closed", None)) and page.is_closed():
            return
    except Exception:
        return
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=min(max_ms, 2000))
    except Exception:
        pass
    try:
        await page.evaluate(
            """
            async ({ stableMs, maxMs }) => {
              const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
              const raf = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
              const deadline = performance.now() + maxMs;
              try { await raf(); await raf(); } catch (_) {}
              if (document.fonts && document.fonts.ready) {
                await Promise.race([document.fonts.ready.catch(() => {}), sleep(500)]);
              }
              let lastMutation = performance.now();
              const root = document.documentElement || document.body || document;
              const obs = new MutationObserver(() => { lastMutation = performance.now(); });
              try { obs.observe(root, { subtree: true, childList: true, attributes: true, characterData: true }); } catch (_) {}
              try {
                while (performance.now() < deadline) {
                  const body = document.body;
                  const ready = document.readyState !== 'loading' && !!body && body.getBoundingClientRect().width > 0;
                  if (ready && performance.now() - lastMutation >= stableMs) break;
                  await sleep(100);
                }
              } finally {
                try { obs.disconnect(); } catch (_) {}
              }
            }
            """,
            {"stableMs": stable_ms, "maxMs": max_ms},
        )
    except Exception:
        try:
            await page.wait_for_timeout(min(stable_ms, 900))
        except Exception:
            pass


async def save_screenshot(
    page: Any,
    root: Path,
    slot_id: str,
    round_id: str,
    stage: str,
    *,
    stable_ms: int = 800,
    max_ms: int = 3200,
) -> str:
    day = iso_now()[:10]
    out_dir = Path(root) / "storage" / "screenshots" / day / slot_id / round_id
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_stage = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in stage)[:80]
    path = out_dir / f"{iso_now().replace(':','').replace('+','_')}_{safe_stage}.png"
    try:
        await _wait_visual_stable(page, stable_ms=stable_ms, max_ms=max_ms)
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    return str(path)


async def save_live_screenshot(page: Any, root: Path, slot_id: str, round_id: str, stage: str) -> str:
    """Overwrite the one live snapshot for a slot.

    The dashboard uses this path as the current visual truth.  It is
    intentionally not timestamped, so long-running CF/waiting/login/business
    loops do not fill storage.  Retained evidence screenshots still use
    ``save_screenshot`` on failures and round close.
    """
    out_dir = Path(root) / "storage" / "live_snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_slot = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in slot_id)[:80]
    path = out_dir / f"{safe_slot}.png"
    meta_path = out_dir / f"{safe_slot}.json"
    ts = iso_now()
    try:
        await _wait_visual_stable(page, stable_ms=550, max_ms=2200)
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    try:
        meta_path.write_text(
            json.dumps(
                {
                    "slot": slot_id,
                    "round": round_id,
                    "stage": stage,
                    "updated_at": ts,
                    "path": f"storage/live_snapshots/{safe_slot}.png",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return str(path)
