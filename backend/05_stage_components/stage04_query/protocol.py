from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote, urlencode

BUSINESS_APPD = "8f4b3551-7b54-f111-bec6-001dd8084f56"


def normalize_date_s(v: Any) -> str:
    s = str(v or "")
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else s[:10]


def entry_available(e: dict[str, Any]) -> bool:
    for key in ("entries_available", "EntriesAvailable", "available"):
        try:
            if int(e.get(key) or 0) > 0:
                return True
        except Exception:
            pass
    return bool(e.get("num") or e.get("Num") or e.get("id") or e.get("ID"))


def in_target_window(date_s: str, cfg: Any) -> bool:
    ds = normalize_date_s(date_s)
    if not ds:
        return False
    start = str(getattr(cfg.target, "start_date", "") or "")[:10]
    cutoff = str(getattr(cfg.target, "cutoff_date", "") or getattr(cfg.target, "end_date", "") or "")[:10]
    if start and ds < start:
        return False
    return not cutoff or ds <= cutoff


def choose_post(posts: list[dict[str, Any]], cfg: Any) -> tuple[str, str, str, list[dict[str, Any]]]:
    clean: list[dict[str, Any]] = []
    aliases = [str(x).lower() for x in ([getattr(cfg.target, "post_name", "")] + list(getattr(cfg.target, "post_aliases", []) or [])) if x]
    wanted_id = str(getattr(cfg.target, "post_id", "") or "")
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("ID") or p.get("Id") or p.get("id") or p.get("PostID") or "")
        name = str(p.get("Name") or p.get("name") or p.get("PostName") or "")
        row = {"id": pid, "name": name, "raw": p}
        clean.append(row)
        if wanted_id and pid == wanted_id:
            return pid, name, "configured_id", clean
    for row in clean:
        low = row["name"].lower()
        if any(a and a in low for a in aliases):
            return row["id"], row["name"], "alias", clean
    if clean:
        return clean[0]["id"], clean[0]["name"], "fallback_first", clean
    return "", "", "not_found", clean


def hard_status(rec: dict[str, Any]) -> int:
    try:
        return int(rec.get("status") or 0)
    except Exception:
        return 0


def transport_failed(rec: dict[str, Any]) -> bool:
    try:
        status = int(rec.get("status") or 0)
    except Exception:
        status = 0
    return status <= 0 and bool(rec.get("error") or rec.get("aborted") or rec.get("timeout_ms"))


def status_reason(rec: dict[str, Any]) -> str:
    status = hard_status(rec)
    if transport_failed(rec):
        return "failed_to_fetch"
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "auth_or_cf_block"
    if status >= 500:
        return "server_error"
    if status >= 400:
        return f"http_{status}"
    return "ok"


def endpoint_url(cfg: Any, path: str, params: dict[str, Any] | str | None = None, *, appd: str | None = None) -> str:
    """Build the site's official custom-actions endpoint.

    The browser page does not call ``/{lang}/api/v1/...`` directly.  It calls
    ``/{lang}/custom-actions/`` with ``route=<api path>`` and, for schedule
    group operations, ``appd=<guid>``.  Hitting the direct API path returns the
    site's HTML 404 page; that was the root cause of the refactor's
    ``page view failed: http_404`` during live validation.
    """
    if isinstance(params, str) and appd is None:
        appd = params
        params = None
    qs: dict[str, Any] = {"route": path, "cacheString": str(int(time.time() * 1000))}
    if appd:
        qs["appd"] = appd
    if isinstance(params, dict):
        qs["parameters"] = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    return f"https://www.usvisascheduling.com/{cfg.target.lang}/custom-actions/?" + urlencode(qs, safe="/")


async def browser_fetch(page: Any, method: str, url: str, parameters: dict[str, Any] | None = None, *, referrer: str, timeout_ms: int = 15000) -> tuple[dict[str, Any], Any]:
    payload = None
    if parameters is not None:
        payload = "parameters=" + quote(json.dumps(parameters, ensure_ascii=False, separators=(",", ":")), safe="")
    rec = await page.evaluate(
        """
        async ({method, url, payload, referrer, timeoutMs}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort('business_fetch_timeout'), Math.max(1000, Number(timeoutMs || 15000)));
          const init = {method, credentials:'include', cache:'no-store', referrer, referrerPolicy:'strict-origin-when-cross-origin', headers:{'Accept':'application/json, text/javascript, */*; q=0.01','X-Requested-With':'XMLHttpRequest'}};
          init.signal = controller.signal;
          if (payload !== null) { init.headers['Content-Type']='application/x-www-form-urlencoded; charset=UTF-8'; init.body=payload; }
          try {
            const r = await fetch(url, init);
            const text = await r.text();
            return {status:r.status, url:r.url, text, text_len:text.length, content_type:r.headers.get('content-type') || '', timeout_ms: timeoutMs};
          } catch (e) {
            const name = String((e && e.name) || '');
            const msg = String((e && e.message) || e || '');
            return {status:0, url, text:'', text_len:0, content_type:'', error: name + ': ' + msg, aborted: name === 'AbortError', timeout_ms: timeoutMs};
          } finally {
            clearTimeout(timer);
          }
        }
        """,
        {"method": method, "url": url, "payload": payload, "referrer": referrer, "timeoutMs": int(timeout_ms or 15000)},
    )
    parsed = None
    txt = rec.get("text") or ""
    try:
        parsed = json.loads(txt)
    except Exception:
        m = re.search(r"({.*}|\[.*\])", txt, re.S)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except Exception:
                parsed = None
    return rec, parsed
