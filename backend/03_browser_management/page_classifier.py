from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PageState:
    stage: str
    url: str
    title: str
    reason: str = ""


async def classify_page(page: Any) -> PageState:
    url = ""
    title = ""
    text = ""
    try:
        url = str(page.url or "")
    except Exception:
        pass
    try:
        title = str(await page.title() or "")
    except Exception:
        pass
    try:
        text = str(await page.locator("body").inner_text(timeout=1500) or "")[:5000]
    except Exception:
        text = ""
    frame_hint = ""
    try:
        frame_hint = str(
            await page.evaluate(
                """() => Array.from(document.querySelectorAll('iframe')).map(f => f.src || '').join(' ')"""
            )
            or ""
        )[:3000]
    except Exception:
        frame_hint = ""
    kba_hint = ""
    try:
        kba_hint = str(
            await page.evaluate(
                """() => Array.from(document.querySelectorAll('input[id*="kba" i], input[name*="kba" i], [id*="kbq" i], .textInParagraph'))
                  .map(el => [el.id || '', el.getAttribute('name') || '', el.getAttribute('aria-label') || '', el.innerText || el.textContent || ''].join(' '))
                  .join(' ')"""
            )
            or ""
        )[:3000]
    except Exception:
        kba_hint = ""
    low = " ".join([url, title, text, frame_hint, kba_hint]).lower()

    # Account-level USTravelDocs automation block.  This page can still render
    # the normal B2C username/password controls, so it must be detected before
    # ``has_credential_form``; otherwise ten slots will keep re-submitting
    # credentials against an account that is explicitly not allowed to log in.
    if (
        "login not allowed for your account at this moment" in low
        or "temporarily blocked due to prohibited use of automation" in low
        or "prohibited use of automation" in low and "ustraveldocs" in low
        or "violated ustraveldocs policies" in low
    ):
        return PageState("account_login_blocked", url, title, "ustd_account_automation_block")

    # Real actionable B2C forms must be recognized before generic CF/callback
    # URL heuristics.  During Cloudflare return-token redirects the URL can
    # still contain ``__cf_chl_*`` after the actual B2C form has rendered; in
    # that case the login component should fill the form instead of re-running
    # CF gate forever.
    has_credential_form = False
    has_kba_form = False
    try:
        has_credential_form = await page.locator("#signInName").count() > 0 and await page.locator("#password").count() > 0
    except Exception:
        has_credential_form = False
    try:
        has_kba_form = await page.locator("input[id*='kba' i], input[name*='kba' i], [id*='kbq' i]").count() > 0
    except Exception:
        has_kba_form = False
    if has_credential_form:
        return PageState("login", url, title, "b2c_login_form")
    if has_kba_form or ("kba" in low or "kbq" in low or "security question" in low):
        if "b2clogin.com" in low or "selfasserted" in low:
            return PageState("security_questions", url, title, "security_question_form")

    if url.startswith("chrome-error://") or "err_timed_out" in low or "err_proxy" in low or "this site can’t be reached" in low or "this site can't be reached" in low:
        return PageState("network_error", url, title, "chrome_network_error")
    if url in {"about:blank", ""} and not text:
        return PageState("blank", url, title, "blank_page")
    if "error 1015" in low or "you are being rate limited" in low:
        return PageState("rate_limit_1015", url, title, "cloudflare_error_1015_rate_limited")
    if (
        "http error 429" in low
        or "error 429" in low
        or "429 too many requests" in low
        or "too many requests" in low
        or "rate limit" in low and "1015" not in low
    ):
        return PageState("rate_limit_429", url, title, "http_429_too_many_requests")
    if (
        "access denied" in low
        or "error 1020" in low
        or "sorry, you have been blocked" in low
        or "you are unable to access" in low
        or "cf-error-details" in low
        or ("attention required" in low and "cloudflare" in low and "blocked" in low)
    ):
        return PageState("access_denied", url, title, "cloudflare_access_denied")
    if (
        "page not found" in low
        or "page could not be found" in low
        or "404" in title.lower()
        or "找不到页面" in low
        or "页面不存在" in low
    ) and "usvisascheduling.com" in low:
        if "signin-aad-b2c_1" in low or "externallogincallback" in low:
            return PageState("callback_not_found", url, title, "b2c_callback_page_not_found")
        return PageState("page_not_found", url, title, "portal_page_not_found")
    if (
        "just a moment" in low
        or "checking your browser" in low
        or "__cf_chl" in low
        or "cf_chl_rt_tk" in low
        or "cf_chl_tk" in low
        or "challenge-platform" in low
        or "challenges.cloudflare.com" in low
        or "verify you are human" in low
        or "正在进行安全验证" in low
        or "请验证您是真人" in low
        or "安全服务防护恶意自动程序" in low
    ):
        return PageState("cf_challenge", url, title, "cf_text_or_iframe")
    # Classify identity-provider pages before generic waiting-room text.
    # B2C self-asserted pages often contain localized "please wait" snippets;
    # treating them as Cloudflare Waiting Room makes the pipeline call the
    # queue policy instead of the login component.
    if "loading https://atlasauth.b2clogin.com/" in low or "account/login/externallogin" in low:
        return PageState("idp_loading", url, title, "idp_redirect_or_portal_spinner")
    if (
        "signin-aad-b2c_1" in low
        or "account/login/externallogincallback" in low
        or "openidconnect.authenticationproperties" in low
    ):
        return PageState("idp_loading", url, title, "portal_b2c_callback_loading")
    if "externalauthenticationfailed" in low or "sign in failed" in low:
        return PageState("login_failed", url, title, "external_auth_failed")
    if "b2clogin.com" in low or "signin" in low or "sign in" in low or "signinname" in low or "password" in low:
        if "b2clogin.com" in low and "password" not in low and "signinname" not in low and "sign in" not in low:
            return PageState("idp_loading", url, title, "b2c_redirect_without_form")
        return PageState("login", url, title, "login_form_or_url")
    if "queue-it" in low or "waiting room" in low or "排队" in low or "等候" in low or ("please wait" in low and "b2clogin.com" not in low):
        return PageState("waiting_room", url, title, "waiting_room_text")
    if "/schedule" in low:
        return PageState("schedule", url, title, "schedule_url")
    if (
        "usvisascheduling.com" in low
        and "loading " not in title.lower()
        and (
            "continue" in low
            or "appointments" in low
            or "appointment manager" in low
            or "customer self-service" in low
            or "sign out" in low
            or "安排" in low
            or "预约" in low
            or "注销" in low
            or "签证申请首页" in low
        )
    ):
        return PageState("home", url, title, "portal_home")
    if "usvisascheduling.com" in low:
        return PageState("site", url, title, "site_generic")
    return PageState("unknown", url, title, "unclassified")


async def is_cf_or_login_bounce(page: Any) -> bool:
    st = await classify_page(page)
    return st.stage in {"cf_challenge", "access_denied", "login", "security_questions", "account_login_blocked"}
