from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from importlib import import_module
from typing import Any
from datetime import datetime, timedelta

from ..base import StageResult

classify_page = import_module("03_browser_management.page_classifier").classify_page
LoginCfReentryHandler = import_module("05_stage_components.stage03_login.cf_reentry").LoginCfReentryHandler
LoginAdmissionController = import_module("00_infrastructure.orchestration.admission.login_admission").LoginAdmissionController


ATLAS_PROVIDER = (
    "https%3A%2F%2Fatlasauth.b2clogin.com%2Ftfp%2F"
    "f50ebcfb-eadd-41d8-9099-a7049d073f5c%2F"
    "b2c_1a_atoproduction_atlas_susi%2Fv2.0%2F"
)


async def _fill_if_exists(page: Any, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.fill(value, timeout=5000)
                return True
        except Exception:
            continue
    return False


async def _fast_fill_credentials(page: Any, username: str, password: str) -> dict[str, Any]:
    """Fill visible B2C credentials in one DOM roundtrip.

    Playwright `locator.fill()` is reliable but can spend seconds on selector
    retries.  For this flow speed matters: once the B2C form is visible, set
    both fields and dispatch input/change immediately, then use the normal
    submit button.
    """
    try:
        return await page.evaluate(
            """([u,p]) => {
              const qs = (s) => document.querySelector(s);
              const ue = qs('#signInName,input[name=signInName],input[type=email],input[name=loginfmt],input[autocomplete=username]');
              const pe = qs('#password,input[name=password]');
              const setv = (el,v) => {
                if (!el) return false;
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.blur();
                return true;
              };
              return {
                user_set: setv(ue, u),
                password_set: setv(pe, p),
                user: ue ? ue.value : null,
                password_len: pe && pe.value ? pe.value.length : 0
              };
            }""",
            [username, password],
        )
    except Exception:
        return {}


async def _click_first(page: Any, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def _install_single_submit_guard(page: Any) -> None:
    """Client-side fuse: only let one credential submit/click pass on B2C.

    The server-side LoginAdmissionController prevents multiple slots from
    submitting at once, but it cannot protect against duplicate click events,
    page script retries, or manual double-clicks inside the same lease.  This
    guard is deliberately local to the current B2C page and only blocks the
    second and later submit/click events.
    """
    try:
        await page.evaluate(
            """() => {
              if (window.__osLoginSingleSubmitGuardInstalled) return true;
              window.__osLoginSingleSubmitGuardInstalled = true;
              window.__osLoginSubmitted = false;
              const isSubmitTarget = (el) => {
                if (!el) return false;
                const tag = (el.tagName || '').toLowerCase();
                const type = (el.getAttribute && (el.getAttribute('type') || '').toLowerCase()) || '';
                const id = (el.id || '').toLowerCase();
                const txt = ((el.innerText || el.value || '') + '').toLowerCase();
                return id === 'next' || id === 'continue' ||
                  (tag === 'button' && (!type || type === 'submit')) ||
                  (tag === 'input' && type === 'submit') ||
                  txt.includes('sign in') || txt.includes('登录');
              };
              const disableSubmitters = () => {
                document.querySelectorAll('#next,#continue,button,input[type=submit]').forEach(el => {
                  if (isSubmitTarget(el)) {
                    try { el.disabled = true; } catch(e) {}
                    try { el.setAttribute('aria-disabled', 'true'); } catch(e) {}
                    try { el.style.pointerEvents = 'none'; } catch(e) {}
                  }
                });
              };
              document.addEventListener('click', (ev) => {
                const target = ev.target && ev.target.closest ? ev.target.closest('button,input,a,#next,#continue') : ev.target;
                if (!isSubmitTarget(target)) return;
                if (window.__osLoginSubmitted) {
                  ev.preventDefault();
                  ev.stopImmediatePropagation();
                  return false;
                }
                window.__osLoginSubmitted = true;
                setTimeout(disableSubmitters, 0);
              }, true);
              document.addEventListener('submit', (ev) => {
                if (window.__osLoginSubmitted) {
                  ev.preventDefault();
                  ev.stopImmediatePropagation();
                  return false;
                }
                window.__osLoginSubmitted = true;
                setTimeout(disableSubmitters, 0);
              }, true);
              return true;
            }"""
        )
    except Exception:
        pass


async def _click_text_or_href(page: Any) -> bool:
    selectors = [
        "a[href*='ExternalLogin']",
        "button[formaction*='ExternalLogin']",
        "input[type='submit'][value*='Sign']",
        "button:has-text('Sign in')",
        "a:has-text('Sign in')",
        "button:has-text('登录')",
        "a:has-text('登录')",
        "button:has-text('继续')",
        "a:has-text('继续')",
    ]
    return await _click_first(page, selectors)


async def _save_debug_artifact(ctx: Any, label: str, events: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Persist minimal DOM/screenshot evidence for the current login blocker."""
    out: dict[str, str] = {}
    try:
        root = Path(ctx.project_root or ctx.runtime_config.project_root)
        safe_round = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(ctx.round_id or "round"))
        safe_slot = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(ctx.slot_id or "slot"))
        debug_dir = root / "storage" / "debug_login" / safe_slot / safe_round
        debug_dir.mkdir(parents=True, exist_ok=True)
        html_path = debug_dir / f"{label}.html"
        png_path = debug_dir / f"{label}.png"
        json_path = debug_dir / f"{label}.json"
        try:
            html_path.write_text(await ctx.page.content(), encoding="utf-8")
            out["html"] = str(html_path.relative_to(root)).replace("\\", "/")
        except Exception as exc:
            out["html_error"] = repr(exc)
        try:
            await ctx.page.screenshot(path=str(png_path), full_page=True)
            out["screenshot"] = str(png_path.relative_to(root)).replace("\\", "/")
        except Exception as exc:
            out["screenshot_error"] = repr(exc)
        try:
            import json

            json_path.write_text(
                json.dumps(
                    {
                        "url": str(ctx.page.url or ""),
                        "title": await ctx.page.title(),
                        "events_tail": (events or [])[-20:],
                        "inputs": await _input_inventory(ctx.page),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            out["meta"] = str(json_path.relative_to(root)).replace("\\", "/")
        except Exception as exc:
            out["meta_error"] = repr(exc)
    except Exception as exc:
        out["artifact_error"] = repr(exc)
    return out


async def _input_inventory(page: Any) -> list[dict[str, Any]]:
    try:
        return await page.evaluate(
            """() => Array.from(document.querySelectorAll('input,button,a,select,textarea')).slice(0, 120).map((el) => ({
              tag: el.tagName.toLowerCase(),
              id: el.id || '',
              name: el.getAttribute('name') || '',
              type: el.getAttribute('type') || '',
              // Do not persist values: this inventory is written to debug
              // artifacts and can otherwise leak username/password/KBA answers.
              text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 120),
              has_value: !!el.value,
              href: el.getAttribute('href') || '',
              action: el.getAttribute('formaction') || ''
            }))"""
        )
    except Exception:
        return []


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _norm_question(s: str) -> str:
    text = _norm(s)
    text = re.sub(r"security\s+question\s*\d+\s*:?", " ", text)
    text = re.sub(r"安全问题\s*\d+\s*[:：]?", " ", text)
    text = re.sub(r"answer\s*[:：]?", " ", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _signin_url(cfg: Any) -> str:
    lang = cfg.target.lang
    return f"https://www.usvisascheduling.com/{lang}/SignIn?ReturnUrl=%2F{lang}%2F"


def _external_login_url(cfg: Any) -> str:
    lang = cfg.target.lang
    return (
        f"https://www.usvisascheduling.com/{lang}/Account/Login/ExternalLogin"
        f"?returnUrl=%2F{lang}%2F&provider={ATLAS_PROVIDER}"
    )


def _drain_requested(ctx: Any) -> bool:
    """Return True when the scheduler has asked this slot to drain.

    Drain is a safe orchestration stop, not a login failure.  The producer may
    mark `drain_requested` in the shared slot-status JSON while this coroutine
    is inside a long Playwright/login loop, so the login stage must poll it
    directly instead of waiting for the SlotRunner thread to consume commands
    after the round finishes.
    """
    try:
        if bool(getattr(ctx, "drain_requested", False)):
            return True
        store = getattr(ctx, "store", None)
        if not store:
            return False
        slot = (store.read_slots() or {}).get(getattr(ctx, "slot_id", ""), {}) or {}
        return bool(slot.get("drain_requested"))
    except Exception:
        return False


def _drain_result(ctx: Any, events: list[dict[str, Any]], where: str) -> StageResult:
    payload = {
        "needs_recover": "drain_requested",
        "reason": "drain_requested",
        "where": where,
        "events": events[-30:],
        "safe_stop": True,
    }
    try:
        if getattr(ctx, "store", None):
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="drain_requested",
                last_reason_zh=f"智能排水：登录阶段在 {where} 收到排水请求，本轮安全结束",
            )
    except Exception:
        pass
    return StageResult(False, "login", "login interrupted by drain request", payload, retryable=False)


async def _account_blocked_result(ctx: Any, state: Any, events: list[dict[str, Any]], *, where: str) -> StageResult:
    """Stop credential retries when the identity provider says the account is blocked.

    This is account-level, not route-level.  Retrying across 10 different IPs
    only creates more failed credential submissions and keeps the hot pool
    permanently at zero, so write a global guard consumed by the producer.
    """
    artifacts = await _save_debug_artifact(ctx, f"account_login_blocked_{where}", events)
    now = datetime.now().astimezone()
    runtime_cfg = getattr(ctx, "runtime_config", None)
    smart_cfg = getattr(runtime_cfg, "smart_orchestrator", None) or getattr(runtime_cfg, "smart", None)
    default_cooldown = float(getattr(smart_cfg, "account_login_block_cooldown_seconds", 43200.0) or 43200.0)
    schedule = list(getattr(smart_cfg, "account_login_block_cooldown_schedule_seconds", []) or [])
    prev_guard: dict[str, Any] = {}
    try:
        if getattr(ctx, "store", None):
            prev = ctx.store.account_guard()
            if isinstance(prev, dict):
                prev_guard = prev
    except Exception:
        prev_guard = {}
    prev_count = int(prev_guard.get("consecutive_block_count") or 0) if str(prev_guard.get("reason") or "") == "account_login_blocked" else 0
    consecutive_block_count = max(1, prev_count + 1)
    if schedule:
        idx = min(consecutive_block_count - 1, len(schedule) - 1)
        cooldown_s = float(schedule[idx] or default_cooldown)
    else:
        cooldown_s = default_cooldown
    cooldown_s = max(3600.0, cooldown_s)
    guard = {
        "active": True,
        "reason": "account_login_blocked",
        "reason_zh": "账号级禁止登录：页面提示 Login not allowed / automation policy，暂停全局登录重试",
        "detected_at": now.isoformat(timespec="seconds"),
        "block_until": (now + timedelta(seconds=cooldown_s)).isoformat(timespec="seconds"),
        "cooldown_seconds": cooldown_s,
        "cooldown_strategy": "escalating_account_guard_after_b2c_login_not_allowed",
        "consecutive_block_count": consecutive_block_count,
        "previous_detected_at": str(prev_guard.get("detected_at") or ""),
        "previous_block_until": str(prev_guard.get("block_until") or ""),
        "slot_id": str(getattr(ctx, "slot_id", "") or ""),
        "round_id": str(getattr(ctx, "round_id", "") or ""),
        "state": getattr(state, "__dict__", {}),
        "artifacts": artifacts,
    }
    try:
        if getattr(ctx, "store", None):
            ctx.store.write_account_guard(guard)
            ctx.store.update_slot(
                ctx.slot_id,
                last_reason="account_login_blocked",
                last_reason_zh=guard["reason_zh"],
                recovery_error_type="account_login_blocked",
                recovery_action="global_login_pause",
                recovery_component="AccountLoginGuard",
            )
    except Exception:
        pass
    return StageResult(
        False,
        "login",
        "account login blocked",
        {
            "state": getattr(state, "__dict__", {}),
            "events": events[-40:],
            "needs_recover": "account_login_blocked",
            "reason": "account_login_blocked",
            "artifacts": artifacts,
            "account_guard": guard,
            "safe_stop": True,
        },
        retryable=False,
    )


async def _ensure_actionable_login_page(page: Any, cfg: Any, events: list[dict[str, Any]]) -> None:
    """Drive portal SignIn/idp spinner to the actual Atlas B2C self-asserted form.

    The portal often renders only a localized "please wait" page at /SignIn.
    Filling selectors there will always fail.  The stable path is:
    SignIn -> ExternalLogin -> atlasauth.b2clogin.com selfAsserted -> #signInName/#password.
    """
    for step in range(1, 5):
        st = await classify_page(page)
        events.append({"event": "ensure_login_entry", "step": step, "state": st.__dict__})
        if st.stage in {"cf_challenge", "waiting_room", "access_denied", "rate_limit_1015", "network_error", "blank", "security_questions", "home", "schedule", "account_login_blocked"}:
            return
        try:
            if await page.locator("#signInName").count() > 0 and await page.locator("#password").count() > 0:
                return
        except Exception:
            pass
        url_l = (st.url or "").lower()
        title_l = (st.title or "").lower()
        if "atlasauth.b2clogin.com" in url_l or st.stage == "idp_loading":
            await page.wait_for_timeout(900)
            continue
        if "signin" in url_l:
            clicked = await _click_text_or_href(page)
            events.append({"event": "signin_surface_click", "clicked": clicked})
            if clicked:
                await page.wait_for_timeout(1000)
                continue
            try:
                await page.goto(_external_login_url(cfg), wait_until="domcontentloaded", timeout=60000)
                events.append({"event": "goto_external_login", "url": str(page.url or "")})
                await page.wait_for_timeout(1000)
                continue
            except Exception as exc:
                events.append({"event": "goto_external_login_error", "error": repr(exc)})
                return
        if "usvisascheduling.com" in url_l:
            try:
                await page.goto(_signin_url(cfg), wait_until="domcontentloaded", timeout=60000)
                events.append({"event": "goto_signin", "url": str(page.url or "")})
            except Exception as exc:
                events.append({"event": "goto_signin_error", "error": repr(exc)})
                return
            await page.wait_for_timeout(1000)
            continue
        await page.wait_for_timeout(800)


async def _wait_for_login_transition(page: Any, *, timeout_ms: int = 9000, interval_ms: int = 500) -> Any:
    """Poll page classifier after submit instead of sleeping a fixed 5s."""
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000)
    last = await classify_page(page)
    while time.monotonic() < deadline:
        st = await classify_page(page)
        last = st
        if st.stage in {"security_questions", "home", "schedule", "cf_challenge", "waiting_room", "access_denied", "rate_limit_1015", "network_error", "blank", "login_failed", "account_login_blocked"}:
            return st
        try:
            if await page.locator("#signInName").count() == 0 and "b2clogin" not in str(page.url).lower():
                return st
        except Exception:
            pass
        await page.wait_for_timeout(interval_ms)
    return last


async def _extract_security_question_fields(page: Any) -> list[dict[str, Any]]:
    try:
        fields = await page.evaluate(
            r"""() => {
              const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
              const visible = (el) => {
                const st = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return st && st.visibility !== 'hidden' && st.display !== 'none' && r.width > 0 && r.height > 0;
              };
              const css = (s) => (window.CSS && CSS.escape) ? CSS.escape(String(s || '')) : String(s || '').replace(/["\\]/g, '\\$&');
              const textOf = (el) => clean(el && (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder')) || '');
              const inputs = Array.from(document.querySelectorAll('input,textarea'));
              const labels = Array.from(document.querySelectorAll('label,div,span,p')).filter(visible).map((el) => {
                const r = el.getBoundingClientRect();
                return { el, text: textOf(el), x: r.x, y: r.y, bottom: r.bottom, w: r.width, h: r.height };
              }).filter((x) => x.text);
              return inputs.map((input, index) => {
                const id = input.id || '';
                const name = input.getAttribute('name') || '';
                const type = (input.getAttribute('type') || 'text').toLowerCase();
                const key = `${index}:${id}:${name}`;
                const lower = `${id} ${name} ${type}`.toLowerCase();
                const idName = `${id} ${name}`.toLowerCase();
                if (!visible(input) || input.disabled || input.readOnly) return null;
                if (['hidden','submit','button','checkbox','radio','email'].includes(type)) return null;
                if (type === 'password' && !/kba|kbq|response|answer/i.test(lower)) return null;
                if (/signin|loginfmt|username|email|^password$/.test(idName)) return null;
                const r = input.getBoundingClientRect();
                const associated = id ? textOf(document.querySelector(`label[for="${css(id)}"]`)) : '';
                const num = (id.match(/kba(\d+)_response/i) || name.match(/kba(\d+)_response/i) || [])[1] || '';
                const linkedQuestion = num ? textOf(document.querySelector(`#kbq${css(num)}ReadOnly, [id="kbq${css(num)}ReadOnly"]`)) : '';
                const prev = textOf(input.previousElementSibling);
                const parent = textOf(input.parentElement);
                const grand = textOf(input.parentElement && input.parentElement.parentElement);
                const nearCandidates = labels
                  .filter((l) => l.bottom <= r.top + 4 && (r.top - l.bottom) < 220 && Math.abs((l.x + l.w / 2) - (r.x + r.width / 2)) < Math.max(360, r.width));
                const preferred = nearCandidates.filter((l) => /what\s+is|\?|？|maiden|sibling|favorite|food/i.test(l.text));
                const near = (preferred.length ? preferred : nearCandidates)
                  .sort((a,b) => (r.top - a.bottom) - (r.top - b.bottom))[0];
                const fallback = (parent && parent.length < 260) ? parent : ((grand && grand.length < 260) ? grand : '');
                const rawQuestion = clean(linkedQuestion || associated || (near && near.text) || prev || fallback);
                const looksKba = /kba|kbq|question|security|response|answer|安全|问题/i.test(`${lower} ${rawQuestion}`);
                if (!looksKba) return null;
                let selector = '';
                if (id) selector = `#${css(id)}`;
                else if (name) selector = `${input.tagName.toLowerCase()}[name="${css(name)}"]`;
                return { index, key, selector, id, name, type, question_text: rawQuestion.slice(0, 600), has_value: !!input.value };
              }).filter(Boolean);
            }"""
        )
        return [x for x in (fields or []) if isinstance(x, dict)]
    except Exception:
        return []


def _field_question_signature(fields: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for field in fields:
        q = _norm_question(str(field.get("question_text") or ""))
        if q:
            parts.append(q[:160])
    return "|".join(parts)


def _security_fields_ready(fields: list[dict[str, Any]], required: int) -> bool:
    if len(fields) < max(1, min(required, 2)):
        return False
    meaningful = 0
    for field in fields:
        q = _norm_question(str(field.get("question_text") or ""))
        # B2C sometimes exposes the KBA input before the question text has
        # rendered and the page title is still "Loading...".  Treat those as
        # not ready; otherwise the login stage records a false
        # "security questions not matched" failure and burns a good session.
        if len(q) >= 8 and q not in {"loading", "user details self asserted"}:
            meaningful += 1
    return meaningful >= max(1, min(required, len(fields)))


async def _wait_for_security_question_fields(page: Any, required: int, *, timeout_s: float = 12.0) -> dict[str, Any]:
    """Wait until KBA questions are actually rendered and stable.

    The classifier can correctly identify a security-question form while the
    B2C self-asserted HTML still says "Loading..." and the question labels have
    not been injected yet.  Matching aliases at that moment creates false
    failures.  This helper waits for two consecutive compatible snapshots or
    returns the best available evidence for diagnostics.
    """
    deadline = time.monotonic() + max(1.0, float(timeout_s or 12.0))
    last_sig = ""
    stable_hits = 0
    best_fields: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            title = str(await page.title() or "")
        except Exception:
            title = ""
        fields = await _extract_security_question_fields(page)
        sig = _field_question_signature(fields)
        ready = _security_fields_ready(fields, required)
        snapshots.append(
            {
                "title": title[:80],
                "fields_count": len(fields),
                "signature": sig[:260],
                "ready": ready,
                "questions": [str(x.get("question_text") or "")[:160] for x in fields[:3]],
            }
        )
        if fields and (not best_fields or len(sig) > len(_field_question_signature(best_fields))):
            best_fields = fields
        if ready and sig and sig == last_sig:
            stable_hits += 1
        elif ready:
            stable_hits = 1
        else:
            stable_hits = 0
        last_sig = sig
        if stable_hits >= 2:
            return {"ok": True, "fields": fields, "stable": True, "snapshots": snapshots[-6:]}
        await page.wait_for_timeout(650)
    return {"ok": _security_fields_ready(best_fields, required), "fields": best_fields, "stable": False, "snapshots": snapshots[-8:]}


def _match_security_question(question_text: str, questions: list[Any], used: set[str]) -> Any | None:
    qt = _norm_question(question_text)
    if not qt:
        return None
    best: tuple[int, Any] | None = None
    for q in questions:
        qid = str(getattr(q, "id", "") or "")
        if qid in used:
            continue
        aliases = list(getattr(q, "aliases", []) or [])
        candidates = aliases + [qid.replace("_", " ")]
        score = 0
        for alias in candidates:
            a = _norm_question(alias)
            if not a:
                continue
            if a in qt or qt in a:
                score = max(score, 100 + len(a))
            else:
                words = [w for w in a.split() if len(w) > 2]
                if words:
                    hits = sum(1 for w in words if w in qt)
                    if hits:
                        score = max(score, hits * 10)
        if score and (best is None or score > best[0]):
            best = (score, q)
    return best[1] if best else None


async def _answer_security_questions(page: Any, account: Any, cfg: Any | None = None) -> dict[str, Any]:
    """Fill only the two questions actually rendered by the page.

    The site has three configured KBA answers but asks two random questions and
    may shuffle order.  Therefore this must never map kba1/kba2/kba3 to fixed
    configured rows.  It extracts the visible question text around each input
    and fills the nearest input with the matching configured answer.
    """
    questions = list(getattr(account, "security_questions", []) or [])
    required = int(getattr(account, "required_security_questions", 2) or 2)
    stable_wait_s = 12.0
    try:
        stable_wait_s = float(getattr(getattr(cfg, "slots", None), "security_question_stable_wait_seconds", 12.0) or 12.0)
    except Exception:
        stable_wait_s = 12.0
    fields_info = await _wait_for_security_question_fields(page, required, timeout_s=stable_wait_s)
    fields = list(fields_info.get("fields") or [])
    answered: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    used_questions: set[str] = set()
    fills: list[dict[str, Any]] = []
    for field in fields:
        matched = _match_security_question(str(field.get("question_text") or ""), questions, used_questions)
        if not matched:
            unmatched.append(
                {
                    "input": str(field.get("id") or field.get("name") or field.get("index") or ""),
                    "question_text": str(field.get("question_text") or "")[:240],
                    "reason": "no_alias_match",
                }
            )
            continue
        qid = str(getattr(matched, "id", "") or "")
        used_questions.add(qid)
        fills.append({"index": field.get("index"), "answer": getattr(matched, "answer", "")})
        answered.append(
            {
                "input": str(field.get("id") or field.get("name") or field.get("index") or ""),
                "question_id": qid,
                "question_text": str(field.get("question_text") or "")[:240],
                "method": "question_text_match",
            }
        )

    enough = len(answered) >= min(required, max(1, len(fields)))
    if fills and enough and not unmatched:
        await page.evaluate(
            """(fills) => {
              const inputs = Array.from(document.querySelectorAll('input,textarea'));
              for (const f of fills) {
                const el = inputs[f.index];
                if (!el) continue;
                el.focus();
                el.value = f.answer;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.blur();
              }
            }""",
            fills,
        )
        await _click_first(page, ["#continue", "button[type='submit']", "input[type='submit']", "button:has-text('Continue')", "button:has-text('继续')"])
    return {
        "ok": bool(enough and not unmatched),
        "answered": answered,
        "count": len(answered),
        "fields_count": len(fields),
        "required": required,
        "unmatched": unmatched,
        "stable": bool(fields_info.get("stable")),
        "render_wait_ok": bool(fields_info.get("ok")),
        "render_snapshots": fields_info.get("snapshots") or [],
    }


async def _handle_security_answer_not_ok(
    ctx: Any,
    st: Any,
    events: list[dict[str, Any]],
    cf_reentry: Any,
    cf_reentry_attempts: int,
    *,
    loop_no: int,
    where: str,
) -> tuple[StageResult | None, int]:
    """Disambiguate real KBA mismatch from page transition/CF re-entry.

    After the first KBA submit, the B2C page can briefly remain classified as
    security_questions while the DOM has already navigated to Cloudflare or an
    IDP callback spinner.  Returning "security questions not matched" in that
    situation is misleading and burns a recoverable session.  Re-classify the
    page and route CF/idp transitions back into the login loop.
    """
    page = ctx.page
    current = await classify_page(page)
    events.append(
        {
            "event": "security_not_ok_reclassified",
            "where": where,
            "previous_state": getattr(st, "__dict__", {}),
            "current_state": current.__dict__,
        }
    )
    if current.stage == "cf_challenge":
        cf_reentry_attempts += 1
        handled = await cf_reentry.maybe_handle(ctx, current, events, attempt_no=cf_reentry_attempts, where=f"{where}_cf_reentry")
        if handled.get("handled") and handled.get("ok"):
            await page.wait_for_timeout(800)
            return None, cf_reentry_attempts
        artifacts = await _save_debug_artifact(ctx, f"blocked_cf_after_security_not_ok_loop_{loop_no}", events)
        return (
            StageResult(
                False,
                "login",
                "login interrupted by cf_challenge after security answers",
                {
                    "state": current.__dict__,
                    "events": events,
                    "needs_recover": "cf_challenge",
                    "artifacts": artifacts,
                    "security_answers_ok_before_block": any(e.get("event") == "security_answered" and e.get("ok") for e in events),
                },
                retryable=True,
            ),
            cf_reentry_attempts,
        )
    if current.stage in {"idp_loading", "login", "home", "schedule"}:
        await page.wait_for_timeout(1200 if current.stage == "idp_loading" else 500)
        return None, cf_reentry_attempts
    if current.stage == "account_login_blocked":
        return (await _account_blocked_result(ctx, current, events, where=f"security_not_ok_loop_{loop_no}"), cf_reentry_attempts)
    if current.stage in {"access_denied", "rate_limit_1015", "network_error", "blank", "login_failed"}:
        artifacts = await _save_debug_artifact(ctx, f"blocked_{current.stage}_after_security_not_ok_loop_{loop_no}", events)
        return (
            StageResult(
                False,
                "login",
                f"login blocked by {current.stage}",
                {"state": current.__dict__, "events": events, "needs_recover": current.stage, "artifacts": artifacts},
                retryable=True,
            ),
            cf_reentry_attempts,
        )
    artifacts = await _save_debug_artifact(ctx, f"security_questions_unmatched_loop_{loop_no}", events)
    return (
        StageResult(
            False,
            "login",
            "security questions not matched by page text",
            {
                "state": current.__dict__,
                "events": events,
                "artifacts": artifacts,
                "needs_recover": "security_questions",
            },
            retryable=True,
        ),
        cf_reentry_attempts,
    )


class LoginStage:
    stage_name = "login"

    async def execute(self, ctx: Any) -> StageResult:
        timeout_s = max(30, int(getattr(ctx.runtime_config.slots, "login_wait_seconds", 150) or 150) + 10)
        try:
            return await asyncio.wait_for(self._execute_inner(ctx), timeout=timeout_s)
        except asyncio.TimeoutError:
            try:
                if getattr(ctx, "store", None):
                    ctx.store.update_slot(
                        ctx.slot_id,
                        last_reason="login_hard_timeout",
                        last_reason_zh=f"登录阶段硬超时：{timeout_s}s 内未完成，结束本轮换代理/画像，避免槽位假死",
                        recovery_error_type="login_timeout",
                        recovery_action="fresh_round",
                        recovery_component="LoginStageHardTimeout",
                    )
            except Exception:
                pass
            return StageResult(
                False,
                self.stage_name,
                "login hard timeout",
                {
                    "needs_recover": "login_timeout",
                    "reason": "login_timeout",
                    "hard_timeout_seconds": timeout_s,
                    "fresh_round": True,
                },
                retryable=False,
            )

    async def _execute_inner(self, ctx: Any) -> StageResult:
        page = ctx.page
        cfg = ctx.runtime_config
        account = ctx.account or (cfg.accounts[0] if cfg.accounts else None)
        if not account:
            return StageResult(False, self.stage_name, "no account configured", retryable=False)
        events: list[dict[str, Any]] = []
        start = time.monotonic()
        cf_reentry = LoginCfReentryHandler(
            max_attempts=int(getattr(cfg.slots, "login_cf_reentry_attempts", 3) or 3),
            max_seconds=max(45, int(getattr(cfg.slots, "early_gate_timeout_seconds", 60) or 60) + 15),
        )
        login_admission = LoginAdmissionController(getattr(ctx, "store", None), cfg)
        cf_reentry_attempts = 0
        st = await classify_page(page)
        if st.stage == "account_login_blocked":
            return await _account_blocked_result(ctx, st, events, where="entry")
        if st.stage not in {"home", "schedule", "login", "security_questions", "idp_loading"}:
            try:
                await page.goto(_signin_url(cfg), wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                events.append({"event": "signin_goto_error", "error": repr(exc)})
        max_submit_attempts = max(1, int(cfg.slots.login_submit_retries or 3))
        submit_attempts = 0
        loop_no = 0
        while submit_attempts < max_submit_attempts and (time.monotonic() - start) <= int(cfg.slots.login_wait_seconds or 150):
            loop_no += 1
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_begin")
            attempt = submit_attempts + 1
            await _ensure_actionable_login_page(page, cfg, events)
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_after_ensure_login_entry")
            st = await classify_page(page)
            events.append({"event": "login_loop", "loop": loop_no, "submit_attempt": attempt, "state": st.__dict__})
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_after_classify")
            if st.stage == "account_login_blocked":
                return await _account_blocked_result(ctx, st, events, where=f"loop_{loop_no}_before_submit")
            if st.stage in {"cf_challenge", "waiting_room", "access_denied", "rate_limit_1015", "network_error", "blank", "login_failed"}:
                if st.stage == "cf_challenge":
                    cf_reentry_attempts += 1
                    handled = await cf_reentry.maybe_handle(ctx, st, events, attempt_no=cf_reentry_attempts, where=f"loop_{loop_no}_blocked")
                    if handled.get("handled") and handled.get("ok"):
                        await page.wait_for_timeout(800)
                        continue
                artifacts = await _save_debug_artifact(ctx, f"blocked_{st.stage}_loop_{loop_no}", events)
                answered_ok = any(e.get("event") == "security_answered" and e.get("ok") for e in events)
                msg = f"login interrupted by {st.stage} after security answers" if answered_ok and st.stage in {"cf_challenge", "access_denied", "rate_limit_1015"} else f"login blocked by {st.stage}"
                return StageResult(
                    False,
                    self.stage_name,
                    msg,
                    {
                        "state": st.__dict__,
                        "events": events,
                        "needs_recover": st.stage,
                        "artifacts": artifacts,
                        "security_answers_ok_before_block": answered_ok,
                    },
                    retryable=True,
                )
            if st.stage in {"home", "schedule"} and "b2clogin" not in st.url.lower() and "signin" not in st.url.lower():
                return StageResult(True, self.stage_name, "already logged in", {"state": st.__dict__, "events": events})
            if st.stage == "idp_loading":
                events.append({"event": "idp_loading_wait", "loop": loop_no, "wait_ms": 3000})
                await page.wait_for_timeout(3000)
                continue
            if st.stage == "security_questions":
                if _drain_requested(ctx):
                    return _drain_result(ctx, events, f"loop_{loop_no}_before_security_answers")
                sec = await _answer_security_questions(page, account, cfg)
                events.append({"event": "security_answered", **sec})
                if not sec.get("ok"):
                    handled_result, cf_reentry_attempts = await _handle_security_answer_not_ok(
                        ctx,
                        st,
                        events,
                        cf_reentry,
                        cf_reentry_attempts,
                        loop_no=loop_no,
                        where=f"loop_{loop_no}_security_branch",
                    )
                    if handled_result is not None:
                        return handled_result
                    continue
                    artifacts = await _save_debug_artifact(ctx, f"security_questions_unmatched_loop_{loop_no}", events)
                    return StageResult(
                        False,
                        self.stage_name,
                        "security questions not matched by page text",
                        {"state": st.__dict__, "events": events, "artifacts": artifacts, "needs_recover": "security_questions"},
                        retryable=True,
                    )
                await page.wait_for_timeout(3000)
                continue
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_before_credentials")
            lease_id = ""
            door_wait_deadline = time.monotonic() + max(1.0, float(getattr(cfg.slots, "login_wait_seconds", 150) or 150))
            while not lease_id:
                decision = login_admission.acquire(
                    account_id=str(getattr(account, "id", "") or "main"),
                    slot_id=str(getattr(ctx, "slot_id", "") or ""),
                    round_id=str(getattr(ctx, "round_id", "") or ""),
                )
                events.append({"event": "login_admission", **decision.to_dict()})
                if decision.ok:
                    lease_id = decision.lease_id
                    break
                if _drain_requested(ctx):
                    return _drain_result(ctx, events, f"loop_{loop_no}_login_admission_wait")
                if decision.reason == "account_guard_active":
                    return StageResult(
                        False,
                        self.stage_name,
                        "account login guard active",
                        {
                            "needs_recover": "account_login_blocked",
                            "reason": "account_login_blocked",
                            "safe_stop": True,
                            "events": events[-30:],
                            "account_guard": decision.state or {},
                        },
                        retryable=False,
                    )
                if time.monotonic() >= door_wait_deadline:
                    return StageResult(
                        False,
                        self.stage_name,
                        "login parked at door waiting for admission",
                        {
                            "needs_recover": "login_admission_wait",
                            "reason": decision.reason,
                            "safe_stop": True,
                            "events": events[-30:],
                            "admission": decision.to_dict(),
                        },
                        retryable=False,
                    )
                wait_cap = max(1.0, float(getattr(getattr(cfg, "smart_orchestrator", None), "login_door_wait_poll_seconds", 10.0) or 10.0))
                wait_s = max(1.0, min(wait_cap, float(decision.wait_seconds or wait_cap)))
                try:
                    if getattr(ctx, "store", None):
                        ctx.store.update_slot(
                            ctx.slot_id,
                            stage="login",
                            stage_zh="登录门口等待",
                            last_reason=f"login_admission_wait:{decision.reason}",
                            last_reason_zh=decision.message or "等待登录提交令牌，不填账号密码",
                            login_admission=decision.to_dict(),
                        )
                except Exception:
                    pass
                await page.wait_for_timeout(int(wait_s * 1000))
            values = await _fast_fill_credentials(page, account.username, account.password)
            filled_user = bool(values.get("user_set"))
            filled_pwd = bool(values.get("password_set"))
            if not (filled_user and filled_pwd):
                filled_user = await _fill_if_exists(page, ["#signInName", "input[name='signInName']", "input[type='email']", "input[name='loginfmt']", "input[autocomplete='username']"], account.username)
                filled_pwd = await _fill_if_exists(page, ["#password", "input[name='password']"], account.password)
                values = await _fast_fill_credentials(page, account.username, account.password)
            events.append({
                "event": "credentials_filled",
                "attempt": attempt,
                "user_field_found": filled_user,
                "password_field_found": filled_pwd,
                "user_value_ok": values.get("user") == account.username,
                "password_len_ok": int(values.get("password_len") or 0) == len(account.password),
            })
            if values.get("user") != account.username or int(values.get("password_len") or 0) != len(account.password):
                login_admission.record_result(account_id=str(getattr(account, "id", "") or "main"), lease_id=lease_id, slot_id=str(getattr(ctx, "slot_id", "") or ""), ok=False, reason="credential_fill_failed")
                artifacts = await _save_debug_artifact(ctx, f"fill_failed_loop_{loop_no}", events)
                events.append({"event": "login_debug_artifacts", "artifacts": artifacts})
                await page.wait_for_timeout(2500)
                continue
            await _install_single_submit_guard(page)
            clicked = await _click_first(page, ["#next", "#continue", "button[type='submit']", "input[type='submit']", "button:has-text('Sign in')", "button:has-text('登录')"])
            events.append({"event": "submit_clicked", "attempt": attempt, "clicked": clicked})
            login_admission.record_submit(
                account_id=str(getattr(account, "id", "") or "main"),
                lease_id=lease_id,
                slot_id=str(getattr(ctx, "slot_id", "") or ""),
                clicked=bool(clicked),
            )
            submit_attempts += 1
            st2 = await _wait_for_login_transition(page, timeout_ms=9000, interval_ms=450)
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_after_submit_transition")
            if st2.stage == "security_questions":
                sec = await _answer_security_questions(page, account, cfg)
                events.append({"event": "security_answered", **sec})
                if not sec.get("ok"):
                    handled_result, cf_reentry_attempts = await _handle_security_answer_not_ok(
                        ctx,
                        st2,
                        events,
                        cf_reentry,
                        cf_reentry_attempts,
                        loop_no=loop_no,
                        where=f"loop_{loop_no}_after_submit_security",
                    )
                    if handled_result is not None:
                        return handled_result
                    continue
                    artifacts = await _save_debug_artifact(ctx, f"security_questions_unmatched_loop_{loop_no}", events)
                    return StageResult(
                        False,
                        self.stage_name,
                        "security questions not matched by page text",
                        {"state": st2.__dict__, "events": events, "artifacts": artifacts, "needs_recover": "security_questions"},
                        retryable=True,
                    )
                st3 = await _wait_for_login_transition(page, timeout_ms=10000, interval_ms=500)
            else:
                st3 = st2
            if _drain_requested(ctx):
                return _drain_result(ctx, events, f"loop_{loop_no}_after_security_transition")
            if st3.stage == "account_login_blocked":
                login_admission.record_result(account_id=str(getattr(account, "id", "") or "main"), lease_id=lease_id, slot_id=str(getattr(ctx, "slot_id", "") or ""), ok=False, reason="account_login_blocked")
                return await _account_blocked_result(ctx, st3, events, where=f"loop_{loop_no}_after_submit")
            if st3.stage in {"home", "schedule"} and "signin" not in st3.url.lower() and "b2clogin" not in st3.url.lower():
                login_admission.record_result(account_id=str(getattr(account, "id", "") or "main"), lease_id=lease_id, slot_id=str(getattr(ctx, "slot_id", "") or ""), ok=True, reason="login_reached_portal")
                return StageResult(True, self.stage_name, "login reached portal", {"state": st3.__dict__, "events": events})
            if st3.stage == "cf_challenge":
                cf_reentry_attempts += 1
                handled = await cf_reentry.maybe_handle(ctx, st3, events, attempt_no=cf_reentry_attempts, where=f"loop_{loop_no}_after_submit")
                if handled.get("handled") and handled.get("ok"):
                    await page.wait_for_timeout(800)
                    continue
            if st3.stage in {"access_denied", "rate_limit_1015", "network_error", "blank", "login_failed"}:
                login_admission.record_result(account_id=str(getattr(account, "id", "") or "main"), lease_id=lease_id, slot_id=str(getattr(ctx, "slot_id", "") or ""), ok=False, reason=st3.stage)
                artifacts = await _save_debug_artifact(ctx, f"blocked_{st3.stage}_after_submit_loop_{loop_no}", events)
                answered_ok = any(e.get("event") == "security_answered" and e.get("ok") for e in events)
                msg = f"login interrupted by {st3.stage} after security answers" if answered_ok and st3.stage in {"cf_challenge", "access_denied", "rate_limit_1015"} else f"login blocked by {st3.stage}"
                return StageResult(
                    False,
                    self.stage_name,
                    msg,
                    {
                        "state": st3.__dict__,
                        "events": events,
                        "needs_recover": st3.stage,
                        "artifacts": artifacts,
                        "security_answers_ok_before_block": answered_ok,
                        "fresh_round": st3.stage in {"rate_limit_1015", "access_denied"},
                    },
                    retryable=True,
                )
            if time.monotonic() - start > int(cfg.slots.login_wait_seconds or 150):
                break
        final = await classify_page(page)
        if final.stage == "account_login_blocked":
            return await _account_blocked_result(ctx, final, events, where="final")
        artifacts = await _save_debug_artifact(ctx, "login_not_completed", events)
        return StageResult(False, self.stage_name, "login not completed", {"state": final.__dict__, "events": events[-30:], "artifacts": artifacts}, retryable=True)
