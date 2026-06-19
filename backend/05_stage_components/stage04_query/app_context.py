from __future__ import annotations

import re
from typing import Any

from ..base import StageResult
from .events import emit
from .models import ScheduleContext
from .protocol import BUSINESS_APPD, browser_fetch, endpoint_url, hard_status, status_reason, transport_failed

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _valid_uuid(value: Any) -> str:
    s = str(value or "").strip()
    return s if UUID_RE.fullmatch(s) else ""


def _uuid_candidates_from_text(text: str) -> list[str]:
    """Extract only real GUIDs tied to application/primary-id semantics.

    The previous implementation used a generic 24+ hex search.  On the live
    applications page that can pick unrelated script/cache identifiers and then
    the page-view API returns a misleading 404.  Keep this deliberately strict:
    no semantic key, no candidate.
    """
    if not text:
        return []
    pats = [
        r"(?:applicationId|ApplicationID|primaryId|PrimaryId|appId|application_id)(?:%22|\\\"|\"|'|\s)*(?:%3A|:|=)(?:%22|\\\"|\"|'|\s)*([0-9a-fA-F-]{36})",
        r"(?:PrimaryId|primaryId|applicationId|ApplicationID|appId)=([0-9a-fA-F-]{36})",
        r"/schedule/[^'\"\s<>]{0,300}?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    ]
    out: list[str] = []
    for pat in pats:
        for m in re.finditer(pat, text, re.I):
            v = _valid_uuid(m.group(1))
            if v and v not in out:
                out.append(v)
    return out


async def discover_application_id(ctx: Any) -> tuple[str, dict[str, Any]]:
    page = ctx.page
    cfg = ctx.runtime_config
    meta: dict[str, Any] = {"source": "", "candidates": []}

    # 1) Explicit runtime value from the same live context.
    v = _valid_uuid(getattr(ctx, "app_id", ""))
    if v:
        meta.update({"source": "ctx_app_id", "candidates": [v]})
        return v, meta

    # 2) Browser state and DOM, with row-scoped applications table priority.
    try:
        dom = await page.evaluate(
            """
            () => {
              const norm = s => String(s || '').replace(/\\s+/g, ' ').trim();
              const attrs = el => {
                const out = [];
                if (!el) return out;
                for (const a of Array.from(el.attributes || [])) out.push(a.name + '=' + a.value);
                return out.join('\\n');
              };
              const rows = Array.from(document.querySelectorAll('table tr, [role="row"]')).map(row => {
                const text = norm(row.innerText || row.textContent);
                if (!(/\\bOpen\\b/i.test(text) && /(BEIJING|北京)/i.test(text))) return '';
                const bits = [text, attrs(row)];
                for (const el of Array.from(row.querySelectorAll('a[href],button,[onclick],input,[data-application-id],[data-id],[data-url]'))) {
                  bits.push(norm(el.innerText || el.textContent || el.value || ''));
                  bits.push(el.href || el.action || '');
                  bits.push(attrs(el));
                }
                return bits.join('\\n');
              }).filter(Boolean).join('\\n');
              const links = Array.from(document.querySelectorAll('a[href],form[action]')).map(x => x.href || x.action || '').join('\\n');
              const storage = (() => {
                const out = [];
                for (const s of [localStorage, sessionStorage]) {
                  for (let i = 0; i < s.length; i++) {
                    const k = s.key(i); out.push(k + '=' + s.getItem(k));
                  }
                }
                return out.join('\\n');
              })();
              return {url: location.href, rows, links, storage};
            }
            """
        )
        blocks = [
            ("applications_open_beijing_row", str((dom or {}).get("rows") or "")),
            ("url", str((dom or {}).get("url") or "")),
            ("storage", str((dom or {}).get("storage") or "")),
            ("links", str((dom or {}).get("links") or "")),
        ]
        all_candidates: list[str] = []
        for source, text in blocks:
            cands = _uuid_candidates_from_text(text)
            for c in cands:
                if c not in all_candidates:
                    all_candidates.append(c)
            if cands:
                meta.update({"source": source, "candidates": all_candidates[:10]})
                return cands[-1], meta
        meta["candidates"] = all_candidates[:10]
    except Exception as exc:
        meta["error"] = repr(exc)

    # 3) Config primary/applications list as a last safe fallback.  Kept at the
    # end so a fresh application id discovered from the active browser wins.
    for app in [getattr(getattr(cfg, "target", None), "primary_id", "")] + list(getattr(getattr(cfg, "target", None), "applications", []) or []):
        v = _valid_uuid(app)
        if v:
            meta.update({"source": "config_primary_or_applications", "candidates": [v]})
            return v, meta
    return "", meta


async def resolve_application_context(ctx: Any) -> tuple[StageResult | None, ScheduleContext | None, list[dict[str, Any]]]:
    page = ctx.page
    cfg = ctx.runtime_config
    ref = f"https://www.usvisascheduling.com/{cfg.target.lang}/schedule/"
    fetch_timeout_ms = int(getattr(getattr(cfg, "producer", None), "business_fetch_timeout_ms", 15000) or 15000)
    steps: list[dict[str, Any]] = []
    app_id, app_meta = await discover_application_id(ctx)
    steps.append({"name": "discover_application_id", "app_id": app_id, **app_meta})
    if not app_id:
        return StageResult(False, "business_query", "application id unresolved", {"steps": steps, "needs_recover": "application_context_unresolved"}, retryable=True), None, steps
    ctx.app_id = app_id

    rec, parsed = await browser_fetch(page, "GET", endpoint_url(cfg, "/api/v1/page-views/schedule/single-family-schedule-page-views", {"PrimaryId": app_id}), None, referrer=ref, timeout_ms=fetch_timeout_ms)
    steps.append({"name": "page_view_schedule", "status": rec.get("status"), "body_len": rec.get("text_len"), "app_id": app_id, "app_id_source": app_meta.get("source"), "parsed": parsed if isinstance(parsed, dict) else None})
    if transport_failed(rec):
        reason = status_reason(rec)
        return StageResult(False, "business_query", f"page view failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": reason}, retryable=True), None, steps
    if rec.get("status") in (401, 403) or (isinstance(parsed, dict) and parsed.get("canViewPage") is False):
        return StageResult(False, "business_query", "page view blocked", {"steps": steps, "needs_recover": "auth_or_cf"}, retryable=True), None, steps
    if hard_status(rec) >= 400:
        reason = status_reason(rec)
        needs = "rate_limit_429" if hard_status(rec) == 429 else reason
        return StageResult(False, "business_query", f"page view failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": needs}, retryable=True), None, steps

    rec, parsed = await browser_fetch(page, "POST", endpoint_url(cfg, "/api/v1/schedule-group/query-family-members-consular", appd=BUSINESS_APPD), {"primaryId": app_id, "visaClass": "all"}, referrer=ref, timeout_ms=fetch_timeout_ms)
    applications = [app_id]
    if isinstance(parsed, dict):
        apps = [str(x.get("ApplicationID")) for x in (parsed.get("Members") or []) if isinstance(x, dict) and x.get("ApplicationID")]
        if apps:
            applications = apps
            app_id = apps[0]
            ctx.app_id = app_id
            ctx.applications = apps
    steps.append({"name": "query_family_members", "status": rec.get("status"), "applications": applications})
    if transport_failed(rec):
        reason = status_reason(rec)
        return StageResult(False, "business_query", f"family members failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": reason}, retryable=True), None, steps
    if hard_status(rec) >= 400:
        reason = status_reason(rec)
        needs = "rate_limit_429" if hard_status(rec) == 429 else reason
        return StageResult(False, "business_query", f"family members failed: {reason}", {"steps": steps, "result_class": reason, "needs_recover": needs}, retryable=True), None, steps
    emit(ctx, "business_context_resolved", {"app_id": app_id, "applications_count": len(applications)})
    return None, ScheduleContext(app_id=app_id, applications=applications, referrer=ref, page_view=parsed if isinstance(parsed, dict) else {}, family_payload={}), steps
