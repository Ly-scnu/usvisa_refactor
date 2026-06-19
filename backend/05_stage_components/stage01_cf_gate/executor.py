from __future__ import annotations

import time
from importlib import import_module
from typing import Any

from ..base import StageResult

classify_page = import_module("03_browser_management.page_classifier").classify_page
save_screenshot = import_module("04_snapshot_system.manager").save_screenshot
_strategies = import_module("05_stage_components.stage01_cf_gate.strategies")
build_click_strategy = _strategies.build_strategy
CdpPreciseClickStrategy = _strategies.CdpPreciseClickStrategy

PASSIVE_INIT_SCRIPT = r"""
(() => {
  if (globalThis.__cfPassiveProbeInstalled) return;
  globalThis.__cfPassiveProbeInstalled = true;
  const events = globalThis.__cfPassiveEvents = globalThis.__cfPassiveEvents || [];
  const push = (kind, data = {}) => {
    try {
      const ev = Object.assign({
        kind,
        t: Math.round((performance && performance.now && performance.now()) || 0),
        ts: Date.now(),
        href: location.href,
        origin: location.origin,
        title: (document && document.title || '').slice(0, 160),
        in_iframe: globalThis.top !== globalThis,
      }, data || {});
      events.push(ev);
      while (events.length > 1000) events.shift();
    } catch (_) {}
  };
  const summarizeFrames = () => {
    try {
      if (globalThis.top !== globalThis || !document || !document.querySelectorAll) return [];
      return Array.from(document.querySelectorAll('iframe')).map((f, i) => {
        const r = f.getBoundingClientRect ? f.getBoundingClientRect() : {};
        return {
          i,
          src: String(f.src || '').slice(0, 1600),
          name: String(f.name || '').slice(0, 200),
          id: String(f.id || '').slice(0, 120),
          rect: { x: Math.round(r.x || 0), y: Math.round(r.y || 0), w: Math.round(r.width || 0), h: Math.round(r.height || 0) },
          visible: !!((r.width || 0) && (r.height || 0)),
        };
      });
    } catch (_) { return []; }
  };
  try {
    globalThis.addEventListener('message', (ev) => {
      try {
        const data = ev && ev.data;
        const s = typeof data === 'string' ? data : JSON.stringify(data || {});
        if (!/cloudflare|turnstile|challenge|interactive|complete|token|response|managed/i.test(s || '')) return;
        const flat = (data && typeof data === 'object') ? {
          event: data.event || '',
          source: data.source || '',
          widgetId: data.widgetId || '',
          keys: Object.keys(data).slice(0, 80),
          token_len: typeof data.token === 'string' ? data.token.length : 0,
          response_len: typeof data.response === 'string' ? data.response.length : 0,
        } : {};
        push('message_event_passive', { event_origin: ev && ev.origin || '', flat });
      } catch (e) { push('message_event_passive_error', { error: String(e) }); }
    }, true);
  } catch (e) { push('message_listener_install_error', { error: String(e) }); }
  try {
    let last = '';
    setInterval(() => {
      const frames = summarizeFrames();
      const sig = JSON.stringify(frames);
      if (sig !== last) {
        last = sig;
        push('iframe_snapshot', { frames });
      }
    }, 300);
  } catch (_) {}
  push('installed', { webdriver: navigator && navigator.webdriver, readyState: document && document.readyState });
})();
"""


async def _install_passive_probe(page: Any, events: list[dict[str, Any]]) -> None:
    if getattr(page, "_opensands_cf_probe_installed", False):
        return
    try:
        await page.add_init_script(PASSIVE_INIT_SCRIPT)
        setattr(page, "_opensands_cf_probe_installed", True)
        events.append({"event": "passive_probe_installed"})
    except Exception as exc:
        events.append({"event": "passive_probe_install_error", "error": repr(exc)})


async def _collect_passive_events(page: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        try:
            evs = await fr.evaluate("() => (globalThis.__cfPassiveEvents || []).slice(-1000)")
        except Exception:
            continue
        for ev in evs or []:
            if isinstance(ev, dict):
                ev["_frame_url"] = getattr(fr, "url", "")
                out.append(ev)
    return out


async def _latest_interactive_begin(page: Any) -> dict[str, Any] | None:
    evs = await _collect_passive_events(page)
    begins = [
        ev for ev in evs
        if ev.get("kind") == "message_event_passive"
        and (ev.get("flat") or {}).get("event") == "interactiveBegin"
        and (ev.get("flat") or {}).get("widgetId")
    ]
    return begins[-1] if begins else None


async def _find_turnstile_box(page: Any) -> dict[str, Any] | None:
    try:
        return await page.evaluate(
            """
            () => {
            const all = [];
            const walk = (root) => {
              for (const el of Array.from(root.querySelectorAll('*'))) {
                all.push(el);
                if (el.shadowRoot) walk(el.shadowRoot);
              }
            };
            walk(document);
            const iframes = all.filter(el => String(el.tagName || '').toLowerCase() === 'iframe');
            const candidates = iframes.map((f, i) => {
              const r = f.getBoundingClientRect();
              const src = String(f.src || '');
              const title = String(f.getAttribute('title') || '');
              let score = 0;
              if (src.includes('challenges.cloudflare.com')) score += 10;
              if (src.includes('turnstile')) score += 5;
              if (title.toLowerCase().includes('cloudflare') || title.toLowerCase().includes('challenge')) score += 8;
              if (r.width >= 200 && r.height >= 40 && r.width <= 500 && r.height <= 160) score += 2;
              return {i, src, title, score, box:{x:r.x,y:r.y,width:r.width,height:r.height}};
            }).filter(x => x.box.width > 0 && x.box.height > 0 && x.score > 0).sort((a,b)=>b.score-a.score);
            if (candidates[0]) return candidates[0];
            const textEls = all.map((el, i) => {
              const txt = (el.innerText || el.textContent || '').trim();
              const r = el.getBoundingClientRect();
              let score = 0;
              if (txt.includes('请验证您是真人')) score += 10;
              if (txt.toLowerCase().includes('verify you are human')) score += 10;
              if (txt.toLowerCase().includes('cloudflare')) score += 2;
              if (r.width >= 250 && r.height >= 40 && r.width <= 600 && r.height <= 220) score += 2;
              return {i, txt: txt.slice(0,120), score, box:{x:r.x,y:r.y,width:r.width,height:r.height}};
            }).filter(x => x.score >= 10 && x.box.width > 0 && x.box.height > 0).sort((a,b)=>b.score-a.score);
            return textEls[0] || null;
            }
            """
        )
    except Exception:
        return None


async def _find_protocol_frame_box(page: Any, widget_id: str = "") -> dict[str, Any] | None:
    """Old 01_cf_gate policy: after natural interactiveBegin, map iframe rect
    + fixed relative point (22,30), then send trusted CDP input.
    """
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    preferred: list[tuple[int, Any, str]] = []
    for fr in frames:
        url = str(getattr(fr, "url", "") or "")
        if "challenges.cloudflare.com" not in url or "/turnstile/" not in url:
            continue
        score = 0
        if widget_id and f"/rch/{widget_id}/" in url:
            score += 10
        if "/normal" in url or "/fbE/" in url:
            score += 2
        preferred.append((score, fr, url))
    preferred.sort(key=lambda x: x[0], reverse=True)
    for score, fr, url in preferred:
        try:
            el = await fr.frame_element()
            box = await el.bounding_box()
            if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                return {"url": url, "score": score, "box": box, "method": "frame_element"}
        except Exception:
            continue
    try:
        candidates = await page.evaluate(
            """(widget) => Array.from(document.querySelectorAll('iframe')).map((f, i) => {
                const r = f.getBoundingClientRect();
                const src = String(f.src || '');
                let score = 0;
                if (src.includes('challenges.cloudflare.com') && src.includes('/turnstile/')) score += 3;
                if (widget && src.includes('/rch/' + widget + '/')) score += 10;
                return {i, src, score, box:{x:r.x,y:r.y,width:r.width,height:r.height}};
            }).filter(x => x.box.width > 0 && x.box.height > 0).sort((a,b)=>b.score-a.score)""",
            widget_id,
        )
        if candidates:
            c = candidates[0]
            return {"url": c.get("src", ""), "score": c.get("score"), "box": c.get("box"), "dom_index": c.get("i"), "method": "dom_iframe"}
    except Exception:
        pass
    return None


class CfGateStage:
    stage_name = "cf_gate"

    def __init__(self, max_seconds: int | None = None, max_clicks: int | None = None):
        self.max_seconds = max_seconds
        self.max_clicks = max_clicks

    async def execute(self, ctx: Any) -> StageResult:
        page = ctx.page
        cfg = ctx.runtime_config
        max_seconds = int(self.max_seconds or cfg.slots.early_gate_timeout_seconds or 60)
        max_clicks = int(self.max_clicks or cfg.producer.max_cf_clicks or 99)
        start = time.monotonic()
        clicks = 0
        clicked_widgets: set[str] = set()
        events: list[dict[str, Any]] = []
        strategy_name = str(getattr(cfg.producer, "cf_click_strategy", "") or getattr(cfg.producer, "cf_click_mode", "") or "hybrid_cdp")
        click_strategy = build_click_strategy(strategy_name)
        fallback_strategy = CdpPreciseClickStrategy()
        target = f"https://www.usvisascheduling.com/{cfg.target.lang}/"
        await _install_passive_probe(page, events)
        try:
            if not str(page.url or "").startswith("https://www.usvisascheduling.com"):
                await page.goto(target, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            events.append({"event": "goto_error", "error": repr(exc)})
            state = await classify_page(page)
            if state.stage in {"network_error", "blank"}:
                return StageResult(
                    False,
                    self.stage_name,
                    "target navigation network error",
                    {"state": state.__dict__, "events": events, "reason": "network_error"},
                    retryable=True,
                )
        while time.monotonic() - start < max_seconds:
            state = await classify_page(page)
            events.append({"event": "classify", "stage": state.stage, "url": state.url, "title": state.title, "reason": state.reason})
            if state.stage in {"network_error", "blank"}:
                return StageResult(False, self.stage_name, "target navigation network error", {"state": state.__dict__, "events": events[-5:], "reason": "network_error"}, retryable=True)
            if state.stage == "access_denied":
                return StageResult(False, self.stage_name, "cloudflare access denied", {"state": state.__dict__, "events": events[-5:], "reason": "access_denied"}, retryable=True)
            if state.stage == "rate_limit_1015":
                return StageResult(
                    False,
                    self.stage_name,
                    "cloudflare 1015 rate limited",
                    {
                        "state": state.__dict__,
                        "events": events[-5:],
                        "reason": "ban_1015",
                        "needs_recover": "ban_1015",
                        "official_error": {
                            "code": "1015",
                            "headline": "You are being rate limited",
                            "meaning": "Cloudflare temporary rate limit / ban page",
                        },
                    },
                    retryable=True,
                )
            if state.stage not in {"cf_challenge"}:
                return StageResult(True, self.stage_name, f"gate passed: {state.stage}", {"state": state.__dict__, "clicks": clicks, "events": events})
            begin = await _latest_interactive_begin(page)
            if begin and clicks < max_clicks:
                widget = str((begin.get("flat") or {}).get("widgetId") or "")
                if widget in clicked_widgets:
                    events.append({"event": "protocol_widget_already_clicked", "widgetId": widget})
                else:
                    info = await _find_protocol_frame_box(page, widget)
                    if info:
                        result = await click_strategy.click(page, info, widget_id=widget, click_index=clicks + 1)
                        events.append({"event": "cf_click_attempt", "configured_strategy": strategy_name, "result": result.to_event(), "frame": info})
                        if not result.ok and result.strategy != fallback_strategy.name:
                            fallback = await fallback_strategy.click(page, info, widget_id=widget, click_index=clicks + 1)
                            events.append({"event": "cf_click_fallback_attempt", "result": fallback.to_event(), "frame": info})
                            result = fallback
                        if result.ok:
                            clicks += 1
                            clicked_widgets.add(widget)
                            events.append({"event": "protocol_cdp_click", "widgetId": widget, "x": result.x, "y": result.y, "strategy": result.strategy, "frame": info, "clicks": clicks})
                        else:
                            events.append({"event": "protocol_cdp_click_error", "widgetId": widget, "error": result.error, "strategy": result.strategy, "frame": info})
                    else:
                        events.append({"event": "protocol_no_frame_box", "widgetId": widget})
            else:
                # Critical difference from the broken refactor: do not click
                # the managed "正在验证/Verifying..." spinner.  Old 01_cf_gate
                # only clicks after interactiveBegin; otherwise it waits for
                # CF to render a real Turnstile iframe or auto-redirect.
                frame_box = await _find_turnstile_box(page)
                events.append({"event": "waiting_for_interactiveBegin", "has_dom_box": bool(frame_box), "clicks": clicks})
            await page.wait_for_timeout(1800)
        shot = ""
        if ctx.project_root and not cfg.producer.cf_no_screenshots:
            shot = await save_screenshot(page, ctx.project_root, ctx.slot_id, ctx.round_id, self.stage_name)
        return StageResult(False, self.stage_name, "cf gate timeout", {"clicks": clicks, "events": events[-20:], "screenshot": shot}, retryable=True)
