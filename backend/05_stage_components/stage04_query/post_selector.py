from __future__ import annotations

from typing import Any

from ..base import StageResult
from .events import emit, slot_patch
from .models import PostSelection, ScheduleContext
from .protocol import BUSINESS_APPD, browser_fetch, choose_post, endpoint_url, hard_status, status_reason, transport_failed


async def click_post_in_page(page: Any, post_id: str, post_name: str, cfg: Any) -> tuple[bool, str]:
    aliases = [x for x in ([post_name, getattr(cfg.target, "post_name", "")] + list(getattr(cfg.target, "post_aliases", []) or [])) if x]
    aliases = list(dict.fromkeys([str(x) for x in aliases if str(x).strip()]))
    selectors: list[str] = []
    for text in aliases:
        selectors.extend([
            f"option:has-text('{text}')",
            f"text={text}",
            f"button:has-text('{text}')",
            f"li:has-text('{text}')",
            f"label:has-text('{text}')",
            f"span:has-text('{text}')",
        ])
    if post_id:
        selectors.extend([f"option[value='{post_id}']", f"[data-value='{post_id}']", f"[value='{post_id}']"])

    # Try normal Playwright-style selection first.
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                tag = ""
                try:
                    tag = await loc.evaluate("el => el.tagName")
                except Exception:
                    pass
                if str(tag).lower() == "option":
                    value = await loc.get_attribute("value")
                    parent = page.locator("select").first
                    if value and await parent.count() > 0:
                        await parent.select_option(value=value, timeout=3000)
                    else:
                        await loc.click(timeout=3000)
                else:
                    await loc.click(timeout=4000)
                await page.wait_for_timeout(900)
                return True, sel
        except Exception:
            pass

    # CDP/DOM fallback: find a select/option or clickable node by visible text/value.
    try:
        clicked = await page.evaluate(
            """
            ({postId, aliases}) => {
              const clean = (s) => String(s || '').trim().toLowerCase();
              const wants = aliases.map(clean).filter(Boolean);
              for (const sel of Array.from(document.querySelectorAll('select'))) {
                const opts = Array.from(sel.options || []);
                const opt = opts.find(o => (postId && String(o.value) === String(postId)) || wants.some(w => clean(o.textContent).includes(w)));
                if (opt) {
                  sel.value = opt.value;
                  sel.dispatchEvent(new Event('input', {bubbles:true}));
                  sel.dispatchEvent(new Event('change', {bubbles:true}));
                  return 'select:' + (opt.textContent || opt.value || '').slice(0,80);
                }
              }
              const nodes = Array.from(document.querySelectorAll('button,li,a,label,span,div,[role="option"],[data-value]'));
              const el = nodes.find(n => (postId && String(n.getAttribute('data-value') || n.getAttribute('value') || '') === String(postId)) || wants.some(w => clean(n.innerText || n.textContent).includes(w)));
              if (!el) return '';
              el.scrollIntoView({block:'center', inline:'center'});
              el.click();
              return 'click:' + ((el.innerText || el.textContent || el.getAttribute('data-value') || el.tagName || '').slice(0,80));
            }
            """,
            {"postId": post_id, "aliases": aliases},
        )
        if clicked:
            await page.wait_for_timeout(900)
            return True, str(clicked)
    except Exception:
        pass
    return False, "not_clicked"


async def select_target_post(ctx: Any, schedule: ScheduleContext, steps: list[dict[str, Any]]) -> tuple[StageResult | None, PostSelection | None]:
    page = ctx.page
    cfg = ctx.runtime_config
    fetch_timeout_ms = int(getattr(getattr(cfg, "producer", None), "business_fetch_timeout_ms", 15000) or 15000)
    emit(ctx, "business_post_selecting", {"wanted": getattr(cfg.target, "post_name", "BEIJING")})
    rec, parsed = await browser_fetch(page, "POST", endpoint_url(cfg, "/api/v1/schedule-group/query-consular-posts", appd=BUSINESS_APPD), {"applicationId": schedule.app_id}, referrer=schedule.referrer, timeout_ms=fetch_timeout_ms)
    posts = parsed.get("Posts") if isinstance(parsed, dict) else []
    post_id, post_name, post_source, clean_posts = choose_post(posts or [], cfg)
    ctx.post_id = post_id
    ctx.post_name = post_name
    steps.append({"name": "query_consular_posts", "status": rec.get("status"), "post_id": post_id, "post_name": post_name, "post_source": post_source, "posts_count": len(clean_posts)})
    if transport_failed(rec):
        reason = status_reason(rec)
        return StageResult(False, "business_query", f"posts failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": reason}, retryable=True), None
    if hard_status(rec) >= 400:
        reason = status_reason(rec)
        needs = "rate_limit_429" if hard_status(rec) == 429 else reason
        return StageResult(False, "business_query", f"posts failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": needs}, retryable=True), None
    if not post_id:
        return StageResult(False, "business_query", "post id unresolved", {"steps": steps, "posts": clean_posts}, retryable=True), None
    # Protocol-first mode: the official page ultimately uses the postId in the
    # AJAX payload.  After query-consular-posts returns the target post, a DOM
    # click is not required for days-only querying and can add render/waf risk.
    # Keep the old DOM click available via config for parity/debug sessions.
    if bool(getattr(getattr(cfg, "producer", None), "protocol_only_post_selection", True)):
        clicked, method = False, "protocol_only_post_selection"
    else:
        clicked, method = await click_post_in_page(page, post_id, post_name, cfg)
    selection = PostSelection(post_id=post_id, post_name=post_name, source=post_source, posts=clean_posts, clicked=clicked, click_method=method)
    emit(ctx, "business_post_selected", {"post_id": post_id, "post_name": post_name, "source": post_source, "clicked": clicked, "method": method})
    slot_patch(ctx, reason="post_selected", reason_zh=f"已协议选择目标使馆：{post_name or post_id}", post_id=post_id, post_name=post_name)
    return None, selection
