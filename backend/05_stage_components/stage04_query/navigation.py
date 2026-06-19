from __future__ import annotations

from importlib import import_module
from typing import Any

from .events import emit, slot_patch
from .models import BLOCKING_STAGES

classify_page = import_module("03_browser_management.page_classifier").classify_page


def home_url(cfg: Any) -> str:
    return f"https://www.usvisascheduling.com/{cfg.target.lang}/"


def schedule_url(cfg: Any) -> str:
    return f"https://www.usvisascheduling.com/{cfg.target.lang}/schedule/"


async def click_manage_application(page: Any) -> tuple[bool, str]:
    selectors = [
        "a:has-text('安排预约')",
        "button:has-text('安排预约')",
        "a:has-text('申请人安排预约')",
        "button:has-text('申请人安排预约')",
        "a:has-text('预约')",
        "button:has-text('预约')",
        "a:has-text('Schedule Appointment')",
        "button:has-text('Schedule Appointment')",
        "a:has-text('Appointment')",
        "button:has-text('Appointment')",
        "a[href*='schedule']",
        "a[href*='appointment']",
        "button[onclick*='schedule']",
        "button[onclick*='appointment']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                before_url = str(getattr(page, "url", "") or "")
                await loc.click(timeout=5000)
                # Do not wait for the full visual schedule widget.  A URL
                # transition or early schedule shell is enough because the
                # business API is fired by browser-context fetch.
                await page.wait_for_timeout(800)
                # Avoid false positives from header/menu text: success means
                # URL changed toward schedule/applications or the schedule DOM
                # is visible.
                ok = await page.evaluate(
                    """(beforeUrl) => {
                      const body = (document.body && document.body.innerText || '');
                      return location.href !== beforeUrl || /\\/schedule|\\/applications/i.test(location.href)
                        || !!document.querySelector('#page_form,#post_select,#datepicker,#datepicker-message')
                        || /申请人安排预约|Schedule Appointment|Application Manager|Applications/i.test(body);
                    }""",
                    before_url,
                )
                if ok:
                    return True, sel
        except Exception:
            pass
    try:
        clicked = await page.evaluate(
            """
            () => {
              const needles = ['schedule','安排预约','申请人安排预约','预约','appointment','Schedule Appointment','Appointment'];
              const nodes = Array.from(document.querySelectorAll('a[href],button,[role="button"],input[type="button"],input[type="submit"]'));
              const el = nodes.find(n => {
                const text = String(n.innerText || n.textContent || n.value || '').toLowerCase();
                const href = String(n.getAttribute('href') || '').toLowerCase();
                const onclick = String(n.getAttribute('onclick') || '').toLowerCase();
                return needles.some(x => text.includes(String(x).toLowerCase()))
                  || /schedule|appointment/i.test(href)
                  || /schedule|appointment/i.test(onclick);
              });
              if (!el) return '';
              el.scrollIntoView({block:'center', inline:'center'});
              el.click();
              return (el.innerText || el.textContent || el.getAttribute('href') || el.tagName || '').slice(0,120);
            }
            """
        )
        if clicked:
            await page.wait_for_timeout(800)
            return True, f"dom_text:{clicked}"
    except Exception:
        pass
    return False, "not_found"


async def click_application_action(page: Any) -> tuple[bool, str]:
    """Continue from /applications/ without direct URL hopping.

    The live site can route appointment/schedule actions to /applications/ first.  Directly
    forcing /schedule/ from there is noisy and triggered 1015 during validation.
    Prefer the site's own buttons/links so the same user gesture chain is kept:
    appointment/schedule -> application card/action -> appointment/schedule UI.
    """
    async def _verify_after_click(before_url: str) -> tuple[bool, str]:
        try:
            await page.wait_for_timeout(1800)
            probe = await page.evaluate(
                """
                (beforeUrl) => {
                  const body = (document.body && document.body.innerText || '').slice(0, 2500);
                  const url = location.href;
                  const hasScheduleDom = !!document.querySelector('#page_form,#post_select,#datepicker,#datepicker-message,#time_select');
                  const hasScheduleText = /申请人安排预约|安排预约|Schedule Appointment|Consular Posts|选择领事馆|Applicant/i.test(body);
                  const stillApplications = /\\/applications/i.test(url) && /Application Manager|Applications/i.test(body) && !hasScheduleDom && !hasScheduleText;
                  return {url, changed: url !== beforeUrl, hasScheduleDom, hasScheduleText, stillApplications, title: document.title};
                }
                """,
                before_url,
            )
            ok = bool((probe or {}).get("hasScheduleDom") or (probe or {}).get("hasScheduleText") or ((probe or {}).get("changed") and not (probe or {}).get("stillApplications")))
            return ok, f"url={((probe or {}).get('url') or '')[:160]} schedule_dom={bool((probe or {}).get('hasScheduleDom'))} schedule_text={bool((probe or {}).get('hasScheduleText'))}"
        except Exception as exc:
            return False, f"verify_error:{repr(exc)}"

    # First priority: the application manager table.  Click the row whose
    # Application Status is Open and whose Post matches Beijing.  During live
    # validation, global text matching hit breadcrumbs/header text; row-scoped
    # clicking is much safer.  Do not report success unless the page actually
    # leaves the inert Applications table or renders schedule DOM/text.
    try:
        before_url = str(getattr(page, "url", "") or "")
        row_clicked = await page.evaluate(
            """
            () => {
              const rows = Array.from(document.querySelectorAll('table tr, [role="row"]'));
              const norm = s => String(s || '').replace(/\\s+/g, ' ').trim();
              const wanted = rows.find(row => {
                const text = norm(row.innerText || row.textContent);
                return /\\bOpen\\b/i.test(text) && /(BEIJING|北京)/i.test(text);
              });
              if (!wanted) return '';
              const clickable = wanted.querySelector('a[href],button,[role="button"],input[type="button"],input[type="submit"],[onclick],[data-url],[data-href]');
              const rowIsClickable = wanted.getAttribute('onclick') || wanted.getAttribute('data-url') || wanted.getAttribute('data-href') || getComputedStyle(wanted).cursor === 'pointer';
              const target = clickable || (rowIsClickable ? wanted : null);
              if (!target) return 'no_clickable_open_beijing_row:' + norm(wanted.innerText || wanted.textContent).slice(0,180);
              target.scrollIntoView({block:'center', inline:'center'});
              target.click();
              return 'open_row:' + norm(wanted.innerText || wanted.textContent).slice(0,180);
            }
            """
        )
        if row_clicked and not str(row_clicked).startswith("no_clickable"):
            ok, verify = await _verify_after_click(before_url)
            if ok:
                return True, f"{row_clicked};{verify}"
            return False, f"open_row_no_transition:{row_clicked};{verify}"
        if row_clicked:
            return False, str(row_clicked)
    except Exception:
        pass

    selectors = [
        "a:has-text('继续')",
        "button:has-text('继续')",
        "a:has-text('Continue')",
        "button:has-text('Continue')",
        "a:has-text('安排预约')",
        "button:has-text('安排预约')",
        "a:has-text('管理预约')",
        "button:has-text('管理预约')",
        "a:has-text('Schedule Appointment')",
        "button:has-text('Schedule Appointment')",
        "a:has-text('Manage Appointment')",
        "button:has-text('Manage Appointment')",
        "a:has-text('Reschedule Appointment')",
        "button:has-text('Reschedule Appointment')",
        "a[href*='appointment']",
        "a[href*='schedule']",
    ]
    last_no_transition = ""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                before_url = str(getattr(page, "url", "") or "")
                await loc.click(timeout=5000)
                ok, verify = await _verify_after_click(before_url)
                if ok:
                    return True, f"{sel};{verify}"
                last_no_transition = f"{sel}_no_transition;{verify}"
        except Exception:
            pass
    try:
        before_url = str(getattr(page, "url", "") or "")
        clicked = await page.evaluate(
            """
            () => {
              const needles = ['继续','安排预约','管理预约','Continue','Schedule Appointment','Manage Appointment','Reschedule Appointment'];
              const nodes = Array.from(document.querySelectorAll('main a,main button,main [role="button"],main input[type="button"],main input[type="submit"],.container a,.container button,.card a,.card button,table a,table button'));
              const visible = (n) => {
                const r = n.getBoundingClientRect();
                const s = getComputedStyle(n);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
              };
              const el = nodes.find(n => visible(n) && (
                needles.some(x => ((n.innerText || n.textContent || n.value || '')).includes(x)) ||
                /schedule|appointment/i.test(n.getAttribute('href') || '') ||
                /schedule|appointment/i.test(n.getAttribute('onclick') || '')
              ));
              if (!el) return '';
              el.scrollIntoView({block:'center', inline:'center'});
              el.click();
              return (el.innerText || el.textContent || el.value || el.getAttribute('href') || el.tagName || '').slice(0,120);
            }
            """
        )
        if clicked:
            ok, verify = await _verify_after_click(before_url)
            if ok:
                return True, f"dom_text:{clicked};{verify}"
            last_no_transition = f"dom_text_no_transition:{clicked};{verify}"
    except Exception:
        pass
    return False, last_no_transition or "not_found"


async def go_home_or_top(ctx: Any, reason: str = "retry") -> None:
    page = ctx.page
    cfg = ctx.runtime_config
    emit(ctx, "business_retry_home", {"reason": reason, "from_url": getattr(page, "url", "")})
    slot_patch(ctx, reason="business_retry_home", reason_zh="业务页状态不完整，返回首页后重新进入预约入口")
    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    for sel in ["a:has-text('首页')", "a:has-text('Home')", "a[href$='/zh-CN/']", "a[href='/zh-CN/']", "a.navbar-brand"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(timeout=2500)
                await page.wait_for_timeout(1200)
                return
        except Exception:
            pass
    try:
        await page.goto(home_url(cfg), wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass


async def ensure_schedule_context(ctx: Any, *, max_attempts: int = 2) -> tuple[bool, Any, dict[str, Any]]:
    page = ctx.page
    cfg = ctx.runtime_config
    emit(ctx, "business_navigation_start", {"url": getattr(page, "url", "")})
    last_state = None
    actions: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        st = await classify_page(page)
        last_state = st
        actions.append({"attempt": attempt, "state": st.__dict__})
        if st.stage in BLOCKING_STAGES:
            return False, st, {"actions": actions, "needs_recover": st.stage}
        if st.stage == "schedule" or "/schedule" in (st.url or ""):
            emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st.url, "title": st.title})
            slot_patch(ctx, reason="schedule_page_ready", reason_zh="已进入预约管理页面")
            return True, st, {"actions": actions}
        if "/applications" in (st.url or ""):
            clicked_app, app_method = await click_application_action(page)
            emit(ctx, "business_manage_clicked", {"attempt": attempt, "clicked": clicked_app, "method": f"applications:{app_method}"})
            if clicked_app:
                st_app = await classify_page(page)
                last_state = st_app
                actions.append({"attempt": attempt, "after_application_action": st_app.__dict__, "method": app_method})
                if st_app.stage in BLOCKING_STAGES:
                    return False, st_app, {"actions": actions, "needs_recover": st_app.stage}
                if st_app.stage == "schedule" or "/schedule" in (st_app.url or "") or "/applications" in (st_app.url or ""):
                    emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st_app.url, "title": st_app.title, "method": app_method, "source": "applications_action"})
                    return True, st_app, {"actions": actions}
            # Even when no second button is found, /applications/ often carries
            # the application id needed by the AJAX schedule APIs.  Let the
            # business context resolver inspect it instead of hard-jumping.
            emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st.url, "title": st.title, "method": "applications_page_context"})
            return True, st, {"actions": actions, "source": "applications_page_context"}
        # Fastest/least-rendering path: after login, the portal home is already
        # authenticated and same-origin.  The official schedule page ultimately
        # calls /custom-actions/ via AJAX; when config has a known application id
        # we can try those browser-context fetches directly from home using the
        # schedule URL as referrer, without clicking/GETing /schedule/ first.
        #
        # This is deliberately gated by config so it can be disabled if a future
        # runtime proves page-view must be initialized by the schedule HTML.
        direct_from_home = bool(getattr(getattr(cfg, "producer", None), "protocol_direct_from_home", False))
        has_app_hint = bool(getattr(getattr(cfg, "target", None), "primary_id", "") or getattr(getattr(cfg, "target", None), "applications", []))
        if st.stage == "home" and direct_from_home and has_app_hint:
            emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st.url, "title": st.title, "method": "protocol_direct_from_home", "source": "home_same_origin_fetch"})
            slot_patch(ctx, reason="home_protocol_context", reason_zh="已在登录首页，跳过预约页渲染，直接尝试同源协议查询")
            actions.append({"attempt": attempt, "protocol_direct_from_home": True, "reason": "authenticated_home_with_config_application"})
            return True, st, {"actions": actions, "source": "home_same_origin_fetch"}
        if st.stage not in {"home", "site"}:
            await go_home_or_top(ctx, f"unexpected_state_{st.stage}")
            continue
        clicked, method = await click_manage_application(page)
        emit(ctx, "business_manage_clicked", {"attempt": attempt, "clicked": clicked, "method": method})
        if clicked:
            st2 = await classify_page(page)
            last_state = st2
            actions.append({"attempt": attempt, "after_click": st2.__dict__, "method": method})
            if st2.stage in BLOCKING_STAGES:
                return False, st2, {"actions": actions, "needs_recover": st2.stage}
            if st2.stage == "schedule" or "/schedule" in (st2.url or ""):
                emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st2.url, "title": st2.title, "method": method})
                return True, st2, {"actions": actions}
            if "/applications" in (st2.url or ""):
                clicked_app, app_method = await click_application_action(page)
                emit(ctx, "business_manage_clicked", {"attempt": attempt, "clicked": clicked_app, "method": f"applications:{app_method}"})
                if clicked_app:
                    st_app = await classify_page(page)
                    last_state = st_app
                    actions.append({"attempt": attempt, "after_application_action": st_app.__dict__, "method": app_method})
                    if st_app.stage in BLOCKING_STAGES:
                        return False, st_app, {"actions": actions, "needs_recover": st_app.stage}
                    if st_app.stage == "schedule" or "/schedule" in (st_app.url or "") or "/applications" in (st_app.url or ""):
                        emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st_app.url, "title": st_app.title, "method": app_method, "source": "applications_action"})
                        return True, st_app, {"actions": actions}
                emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st2.url, "title": st2.title, "method": "applications_page_context"})
                return True, st2, {"actions": actions, "source": "applications_page_context"}
        actions.append({"attempt": attempt, "schedule_keyword_click": False, "reason": "keyword_not_found"})
        # The portal can still be painting/redirecting after B2C callback.  Do
        # not declare "navigation_not_found" from a single early DOM probe: the
        # page may either finish rendering the appointment/schedule link or bounce
        # back to login/CF.  Re-classify after a quiet wait and retry selectors
        # without hard refresh/goto, which avoids the 1015-prone old loop.
        try:
            fallback_wait_ms = max(0, int(getattr(getattr(cfg, "producer", None), "schedule_direct_fallback_after_ms", 800) or 800))
            await page.wait_for_timeout(fallback_wait_ms)
            st_wait = await classify_page(page)
            last_state = st_wait
            actions.append({"attempt": attempt, "after_no_link_wait": st_wait.__dict__})
            if st_wait.stage in BLOCKING_STAGES:
                return False, st_wait, {"actions": actions, "needs_recover": st_wait.stage}
            if st_wait.stage == "schedule" or "/schedule" in (st_wait.url or "") or "/applications" in (st_wait.url or ""):
                emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st_wait.url, "title": st_wait.title, "method": "late_render_or_redirect"})
                return True, st_wait, {"actions": actions, "source": "late_render_or_redirect"}
        except Exception as exc:
            actions.append({"attempt": attempt, "after_no_link_wait_error": repr(exc)})
        try:
            target = schedule_url(cfg)
            actions.append({"attempt": attempt, "direct_schedule_fallback": target})
            emit(ctx, "business_manage_clicked", {"attempt": attempt, "clicked": True, "method": "direct_schedule_fallback", "target": target})
            await page.goto(target, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(300)
            st_direct = await classify_page(page)
            last_state = st_direct
            actions.append({"attempt": attempt, "after_direct_schedule_fallback": st_direct.__dict__})
            if st_direct.stage in BLOCKING_STAGES:
                return False, st_direct, {"actions": actions, "needs_recover": st_direct.stage}
            if st_direct.stage == "schedule" or "/schedule" in (st_direct.url or "") or "/applications" in (st_direct.url or ""):
                emit(ctx, "business_schedule_page_ready", {"attempt": attempt, "url": st_direct.url, "title": st_direct.title, "method": "direct_schedule_fallback"})
                return True, st_direct, {"actions": actions, "source": "direct_schedule_fallback"}
        except Exception as exc:
            actions.append({"attempt": attempt, "direct_schedule_fallback_error": repr(exc)})
        if attempt < max_attempts:
            continue
        return False, last_state, {"actions": actions, "needs_recover": "navigation_not_found"}
    return False, last_state, {"actions": actions, "needs_recover": getattr(last_state, "stage", "unknown")}
