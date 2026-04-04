"""
browser/register.py — ChatGPT account registration state machine.

Mirrors plan/browser/tool.js _0x548_inner exactly:

  States: GOTO_SIGNUP → FILL_EMAIL → FILL_PASSWORD → WAIT_CODE → FILL_CODE → FILL_PROFILE → COMPLETE

Flow (from tool.js):
  1. Navigate to chatgpt.com/auth/login
     NextAuth 302-redirects → auth.openai.com (Auth0 Universal Login)
  2. GOTO_SIGNUP
     • Check if email input already visible (Auth0 may load immediately)
       → if yes: fill email inline, click Continue, wait for password
     • Else: look for "Sign up" link/button, click it, wait 3 s
  3. FILL_EMAIL  — wait for email input, fill, click Continue
  4. FILL_PASSWORD — wait for password input (≤60 s), fill, click Continue
  5. WAIT_CODE   — poll gptmail inbox for 6-digit code while OTP page loads
  6. FILL_CODE   — fill individual maxlength=1 boxes or one-time-code input
  7. FILL_PROFILE — fill firstName, lastName, birthday spinbuttons, click Agree
  8. COMPLETE    — URL no longer contains auth.openai.com

CLI dry-run:
    python -m src.browser.register --dry-run
"""
from __future__ import annotations

import asyncio
import json
import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from playwright.async_api import Page

from src.browser.engine import create_page
from src.browser.oauth import acquire_tokens_via_browser
from src.browser.helpers import (
    click_submit_or_text,
    find_signup_button,
    human_move_and_click,
    is_error_page,
    is_visible,
    jitter_sleep,
    set_react_input,
    set_spinbutton,
    wait_any_element,
)
from src.mail.base import MailClient

# ── Constants ──────────────────────────────────────────────────────────────

LOGIN_URL   = "https://chatgpt.com/auth/login"
AUTH0_HOST  = "auth.openai.com"

MAX_RETRIES  = 5
CODE_TIMEOUT = 180   # seconds to poll for OTP e-mail (legacy fallback)


# ── Timing configuration ───────────────────────────────────────────────────

@dataclass
class TimingCfg:
    """
    All wait/timeout knobs in one place.
    Load via TimingCfg.from_cfg(cfg) so values come from config.yaml.

    config.yaml structure:
        timing:
          post_nav: 1.0      # s after GOTO_SIGNUP redirect (default 1.0, was 2.0)
          pre_fill: 0.5      # s before each fill / click action (was 1.0)
          post_click: 1.5    # s after signup-button / OTP submit click (was 3.0)
          post_complete: 1.0 # s at COMPLETE before reading final URL (was 2.0)
        timeout:
          email_input: 15    # s to wait for email input
          password_input: 30 # s to wait for password input (was 60)
          otp_input: 30      # s to wait for OTP digit boxes (was 60)
          profile_input: 20  # s to wait for firstName on about-you (was 30)
          code_poll: 120     # s to poll mailbox for OTP code (was 180)
    """
    post_nav: float      = 1.0
    pre_fill: float      = 0.5
    post_click: float    = 1.5
    post_complete: float = 1.0

    email_input_ms: int    = 15_000
    password_input_ms: int = 30_000
    otp_input_ms: int      = 30_000
    profile_input_ms: int  = 20_000
    code_poll: int         = 120

    @classmethod
    def from_cfg(cls, cfg: dict) -> "TimingCfg":
        t  = cfg.get("timing",  {}) or {}
        to = cfg.get("timeout", {}) or {}
        return cls(
            post_nav      = float(t.get("post_nav",      1.0)),
            pre_fill      = float(t.get("pre_fill",      0.5)),
            post_click    = float(t.get("post_click",    1.5)),
            post_complete = float(t.get("post_complete", 1.0)),
            email_input_ms    = int(float(to.get("email_input",    15)) * 1000),
            password_input_ms = int(float(to.get("password_input", 30)) * 1000),
            otp_input_ms      = int(float(to.get("otp_input",      30)) * 1000),
            profile_input_ms  = int(float(to.get("profile_input",  20)) * 1000),
            code_poll         = int(to.get("code_poll", 120)),
        )

# Email selectors — mirrors tool.js GOTO_SIGNUP + FILL_EMAIL order
_EMAIL_SELECTORS = [
    "input[type='email']",
    "input[name='email']",
    "input[name='username']",
    "#username",
    "input[id*='email']",
    "input[autocomplete='email']",
    "input[inputmode='email']",
]

# Password selectors — mirrors tool.js _0x98d
_PASSWORD_SELECTORS = [
    "input[type='password']",
    "input[name='password']",
]

# OTP selectors — mirrors tool.js _0x98d wait loop + _0xbaf
_OTP_BOX_SELECTOR    = "input[type='text'][maxlength='1'], input[maxlength='1']"
_OTP_SINGLE_SELECTORS = [
    "input[autocomplete='one-time-code']",
    "input[name='code']",
    "input[id*='code']",
]

# Profile selectors — mirrors tool.js _0xcc0
# OpenAI about-you page uses name='firstName'/'lastName' but also autocomplete attrs
_FNAME_SELECTORS = [
    "input[name='firstName']",
    "input[name='first_name']",
    "input[id*='firstName']",
    "input[id*='first-name']",
    "input[autocomplete='given-name']",
    "input[placeholder*='first' i]",
    "input[placeholder*='First' i]",
]
_LNAME_SELECTORS = [
    "input[name='lastName']",
    "input[name='last_name']",
    "input[id*='lastName']",
    "input[id*='last-name']",
    "input[autocomplete='family-name']",
    "input[placeholder*='last' i]",
    "input[placeholder*='Last' i]",
]

# ── Name / birthday / password generators ─────────────────────────────────

_FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Mary", "Patricia", "Jennifer", "Linda",
    "Barbara", "Elizabeth", "Susan", "Jessica", "Sarah", "Karen", "Emma",
    "Olivia", "Ava", "Sophia", "Isabella", "Liam", "Noah", "Oliver",
    "Elijah", "Lucas",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Thompson", "White", "Harris", "Clark",
]


def _gen_name() -> tuple[str, str]:
    return random.choice(_FIRST_NAMES), random.choice(_LAST_NAMES)


def _gen_birthday() -> dict:
    year  = datetime.now().year - 18 - random.randint(0, 30)
    month = random.randint(1, 12)
    day   = random.randint(1, 28)
    return {"year": year, "month": month, "day": day}


def _gen_password(length: int = 16) -> str:
    """
    Mirrors tool.js _0xae(16):
    guaranteed uppercase + lowercase + digit + special, then shuffled.
    """
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%"
    parts = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%"),
    ]
    for _ in range(length - 4):
        parts.append(random.choice(chars))
    random.shuffle(parts)
    return "".join(parts)


def _gen_prefix(length: int = 12) -> str:
    chars = string.ascii_lowercase + string.digits
    return random.choice(string.ascii_lowercase) + "".join(
        random.choice(chars) for _ in range(length - 1)
    )


# ── Custom exceptions ──────────────────────────────────────────────────────

class RegistrationError(Exception):
    """Raised when a retryable registration step fails."""


class FatalRegistrationError(Exception):
    """Raised when registration cannot be retried (e.g. email creation failed)."""


# ── Public entry-point ─────────────────────────────────────────────────────

async def register_one(
    task_id: str,
    cfg: dict,
    mail_client: MailClient,
    proxy: Optional[str] = None,
) -> dict:
    """
    Run a single end-to-end ChatGPT registration mirroring tool.js flow.

    Returns a dict with at least:
        email, password, firstName, lastName, status, provider, proxy, createdAt
    """
    first_name, last_name = _gen_name()
    birthday  = _gen_birthday()
    password  = _gen_password()
    reg_cfg   = cfg.get("registration", {})
    prefix    = reg_cfg.get("prefix") or _gen_prefix()
    domain    = reg_cfg.get("domain") or None
    engine    = cfg.get("engine", "playwright")
    headless  = cfg.get("headless", True)
    slow_mo   = cfg.get("slow_mo", 0)
    if not headless and slow_mo == 0:
        slow_mo = 80

    timing = TimingCfg.from_cfg(cfg)
    logger.debug(f"[{task_id}] timing={timing}")

    logger.info(f"[{task_id}] Creating e-mail via {cfg.get('mail_provider', 'gptmail')}")
    try:
        email = await mail_client.generate_email(prefix=prefix, domain=domain)
    except Exception as exc:
        logger.error(f"[{task_id}] E-mail creation failed: {exc}")
        return {"email": "", "status": "email_creation_failed", "error": str(exc)}

    account: dict = {
        "email":     email,
        "password":  password,
        "firstName": first_name,
        "lastName":  last_name,
        "birthday":  birthday,
        "status":    "starting",
        "provider":  cfg.get("mail_provider", "gptmail"),
        "proxy":     proxy or "",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    async with create_page(engine=engine, proxy=proxy, headless=headless, slow_mo=slow_mo) as page:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"[{task_id}] Attempt {attempt}/{MAX_RETRIES} — {email}")
                await _state_machine(task_id, page, account, mail_client, timing)
                account["status"] = "注册完成"
                logger.success(f"[{task_id}] ✅ Done: {email}")

                # ── OAuth token acquisition ──────────────────────────────
                oauth_cfg = cfg.get("oauth", {}) or {}
                if oauth_cfg.get("enabled", True):
                    token_timeout = float(oauth_cfg.get("timeout", 45))
                    try:
                        token_result = await acquire_tokens_via_browser(
                            page, email,
                            password=password,
                            first_name=account["firstName"],
                            last_name=account["lastName"],
                            birthday=account["birthday"],
                            proxy=proxy, timeout=token_timeout
                        )
                        if token_result:
                            account["tokens"] = token_result.to_dict()
                            account["status"] = "已获取Token"
                            logger.success(
                                f"[{task_id}] 🔑 Token acquired — "
                                f"account_id={token_result.account_id} "
                                f"expires={token_result.expires_at}"
                            )
                        else:
                            logger.warning(
                                f"[{task_id}] OAuth flow returned no token — "
                                "registration saved without token"
                            )
                    except Exception as _oa_exc:
                        logger.warning(
                            f"[{task_id}] OAuth error (non-fatal): {_oa_exc}"
                        )

                return account

            except RegistrationError as exc:
                logger.warning(f"[{task_id}] Retry {attempt}: {exc}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(3 * attempt)
                    try:
                        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass

            except Exception as exc:
                logger.error(f"[{task_id}] Unexpected error (attempt {attempt}): {exc}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5)
                    try:
                        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                    except Exception:
                        pass

        account["status"] = "failed"
        return account


# ── State machine ──────────────────────────────────────────────────────────

async def _state_machine(
    task_id: str,
    page: Page,
    account: dict,
    mail_client: MailClient,
    timing: TimingCfg,
) -> None:
    """
    Sequentially executes the 7-state flow matching tool.js _0x548_inner:
      GOTO_SIGNUP → FILL_EMAIL → FILL_PASSWORD → WAIT_CODE → FILL_CODE → FILL_PROFILE → COMPLETE
    All wait/timeout values come from TimingCfg (config.yaml timing/timeout sections).
    """
    T = timing  # shorthand

    # ── STATE: GOTO_SIGNUP ────────────────────────────────────────────
    logger.info(f"[{task_id}] GOTO_SIGNUP → {LOGIN_URL}")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

    try:
        await page.wait_for_url(f"**{AUTH0_HOST}**", timeout=8_000)
    except Exception:
        pass

    # 鼠标预热：导航后先随机移动鼠标模拟真人入场
    await _mouse_warmup(page)

    # tool.js: await _0x1ae(0x7d0) — configurable post-nav wait
    await jitter_sleep(T.post_nav, T.post_nav * 0.25)
    await _assert_not_error(task_id, page)
    logger.debug(f"[{task_id}] GOTO_SIGNUP landed: {page.url}")

    email_already_visible = await _find_visible_email_input(page)

    if email_already_visible:
        logger.info(f"[{task_id}] Email input already visible — proceeding to FILL_EMAIL")
        await jitter_sleep(T.pre_fill * 0.5, T.pre_fill * 0.15)
    else:
        logger.info(f"[{task_id}] Looking for Sign Up button — URL={page.url}")
        signup_btn = await find_signup_button(task_id, page)

        if not signup_btn:
            await jitter_sleep(T.post_click, T.post_click * 0.25)
            signup_btn = await find_signup_button(task_id, page)

        if signup_btn:
            logger.info(f"[{task_id}] Clicking Sign Up button (human simulation)")
            await human_move_and_click(page, signup_btn)
            await jitter_sleep(T.post_click, T.post_click * 0.25)
            await _assert_not_error(task_id, page)
            logger.debug(f"[{task_id}] After signup click: {page.url}")
        else:
            raise RegistrationError(
                f"Sign Up button not found after retrying. URL={page.url}"
            )

    # ── STATE: FILL_EMAIL ─────────────────────────────────────────────
    logger.info(f"[{task_id}] FILL_EMAIL — URL={page.url}")
    email_result = await wait_any_element(page, _EMAIL_SELECTORS, timeout_ms=T.email_input_ms)
    if not email_result:
        try:
            snippet = (await page.content())[:600]
            logger.debug(f"[{task_id}] Page snippet:\n{snippet}")
        except Exception:
            pass
        raise RegistrationError(f"Email input not found. URL={page.url}")

    matched_sel, email_el = email_result
    logger.debug(f"[{task_id}] Email input matched: {matched_sel!r}")

    await jitter_sleep(T.pre_fill, T.pre_fill * 0.3)
    await set_react_input(page, matched_sel, account["email"])
    await jitter_sleep(T.pre_fill * 0.5, T.pre_fill * 0.15)

    sub_loc = None
    try:
        sub = page.locator("button[type='submit']").first
        if await sub.is_visible():
            sub_loc = sub
    except Exception:
        pass

    if sub_loc:
        await human_move_and_click(page, sub_loc)
        submitted = True
    else:
        submitted = await click_submit_or_text(page, ["Continue", "继续", "Next", "Submit"])
    if not submitted:
        try:
            await email_el.press("Enter")
        except Exception:
            pass

    logger.debug(f"[{task_id}] Email submitted")

    # ── STATE: FILL_PASSWORD ──────────────────────────────────────────
    logger.info(f"[{task_id}] FILL_PASSWORD — waiting for password input (≤{T.password_input_ms//1000} s)")
    pw_result = await wait_any_element(page, _PASSWORD_SELECTORS, timeout_ms=T.password_input_ms)
    if not pw_result:
        raise RegistrationError(
            f"Password input not found after email submit. URL={page.url}"
        )
    await _assert_not_error(task_id, page)

    matched_pw_sel, pw_el = pw_result
    logger.debug(f"[{task_id}] Password input matched: {matched_pw_sel!r}")

    await jitter_sleep(T.pre_fill * 0.5, T.pre_fill * 0.15)
    await set_react_input(page, matched_pw_sel, account["password"])
    logger.debug(f"[{task_id}] Password filled")

    await jitter_sleep(T.pre_fill, T.pre_fill * 0.3)
    pw_sub_loc = None
    try:
        pw_sub = page.locator("button[type='submit']").first
        if await pw_sub.is_visible():
            pw_sub_loc = pw_sub
    except Exception:
        pass

    if pw_sub_loc:
        await human_move_and_click(page, pw_sub_loc)
        submitted_pw = True
    else:
        submitted_pw = await click_submit_or_text(page, ["Continue", "继续", "Next", "Submit"])
    if not submitted_pw:
        try:
            await pw_el.press("Enter")
        except Exception:
            pass

    logger.debug(f"[{task_id}] Password submitted — waiting for OTP page")

    # ── STATE: WAIT_CODE ──────────────────────────────────────────────
    logger.info(f"[{task_id}] WAIT_CODE — waiting for OTP inputs (≤{T.otp_input_ms//1000} s)")
    otp_appeared = await _wait_for_otp_inputs(page, timeout_ms=T.otp_input_ms)
    if not otp_appeared:
        raise RegistrationError(
            f"OTP input did not appear after password submit. URL={page.url}"
        )
    await _assert_not_error(task_id, page)
    logger.info(f"[{task_id}] OTP page loaded — polling gptmail inbox")

    await jitter_sleep(T.post_nav, T.post_nav * 0.25)
    code = await mail_client.poll_code(account["email"], timeout=T.code_poll)
    if not code:
        raise RegistrationError("OTP code not received within timeout")

    logger.info(f"[{task_id}] FILL_CODE → {code}")

    # ── STATE: FILL_CODE ──────────────────────────────────────────────
    await _fill_otp(page, code)
    await jitter_sleep(T.pre_fill, T.pre_fill * 0.3)

    submitted_otp = await click_submit_or_text(
        page, ["Continue", "Verify", "Submit", "继续"]
    )
    if not submitted_otp:
        logger.debug(f"[{task_id}] No OTP submit button — likely auto-submitted on last digit")

    await jitter_sleep(T.post_click, T.post_click * 0.25)
    await _assert_not_error(task_id, page)
    logger.debug(f"[{task_id}] After OTP, URL={page.url}")

    # ── STATE: FILL_PROFILE ───────────────────────────────────────────
    logger.info(f"[{task_id}] Checking for FILL_PROFILE page")
    fname_result = await wait_any_element(page, _FNAME_SELECTORS, timeout_ms=T.profile_input_ms)
    # Also trigger profile fill when URL is about-you (selectors may miss the actual inputs)
    if fname_result or "about-you" in page.url:
        logger.info(f"[{task_id}] FILL_PROFILE — URL={page.url}")
        await _fill_profile(task_id, page, account, T)
    else:
        logger.debug(f"[{task_id}] No profile form inputs at {page.url} — trying click-through")
        # about-you page may only need a "Continue" / "Agree" click (age/terms confirmation)
        clicked = await click_submit_or_text(
            page, ["Continue", "Agree", "Accept", "同意", "Next", "Done"]
        )
        if clicked:
            logger.debug(f"[{task_id}] Clicked through profile-less page")
            await asyncio.sleep(T.post_click)

    # ── STATE: COMPLETE ───────────────────────────────────────────────
    logger.info(f"[{task_id}] COMPLETE — waiting for chatgpt.com redirect")
    try:
        await page.wait_for_url("**/chatgpt.com/**", timeout=20_000)
    except Exception:
        pass

    await asyncio.sleep(T.post_complete)
    final_url = page.url
    logger.info(f"[{task_id}] Final URL={final_url}")

    if AUTH0_HOST in final_url or "/auth/" in final_url:
        logger.warning(
            f"[{task_id}] Still on auth page after completion: {final_url}"
        )


# ── Sub-routines ───────────────────────────────────────────────────────────

async def _mouse_warmup(page: Page) -> None:
    """
    Move the mouse in a natural arc after page load to create mouse-event history.
    Cloudflare / Auth0 bot-detection scores sessions that have zero mouse-move
    events before any click as robotic.

    Simulates ~1.5 s of human "reading/scanning" cursor movement.
    """
    try:
        # Start from a random edge-ish position
        x, y = random.randint(300, 700), random.randint(100, 300)
        await page.mouse.move(x, y)
        # 3–5 micro-path segments with smooth easing
        for _ in range(random.randint(3, 5)):
            tx = x + random.randint(-200, 200)
            ty = y + random.randint(-100, 150)
            tx = max(60, min(tx, 1300))
            ty = max(60, min(ty, 700))
            steps = random.randint(6, 12)
            for i in range(1, steps + 1):
                t = i / steps
                t = t * t * (3.0 - 2.0 * t)   # smoothstep
                mx = x + (tx - x) * t + random.uniform(-3, 3)
                my = y + (ty - y) * t + random.uniform(-2, 2)
                await page.mouse.move(mx, my)
                await asyncio.sleep(random.uniform(0.01, 0.03))
            x, y = tx, ty
            await asyncio.sleep(random.uniform(0.1, 0.35))
    except Exception:
        pass   # non-fatal: best-effort warmup


async def _assert_not_error(task_id: str, page: Page) -> None:
    """
    Mirrors tool.js error detection:
      • URL contains /api/auth/error
      • body text contains '糟糕', '出错了', 'Operation timed out', '操作超时'
    """
    if "/api/auth/error" in page.url:
        raise RegistrationError(
            f"NextAuth error page — bot-detected or OAuth config issue: {page.url}"
        )
    if await is_error_page(page):
        raise RegistrationError(f"Error page detected at {page.url}")


async def _find_visible_email_input(page: Page) -> bool:
    """
    Check if an email/username input is currently visible.
    Mirrors tool.js:
      _vis('input[type="email"], input[name="email"], input[name="username"], #username')
    """
    for sel in [
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "#username",
    ]:
        if await is_visible(page, sel):
            return True
    return False


async def _wait_for_otp_inputs(page: Page, timeout_ms: int = 60_000) -> bool:
    """
    Wait for OTP input elements to appear.
    Mirrors tool.js _0x98d inner loop:
      input[type="text"][maxlength="1"]  (≥ 4 means OTP page)
      input[autocomplete="one-time-code"]
      input[name="code"]
    """
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        try:
            count = await page.locator(
                "input[type='text'][maxlength='1'], input[maxlength='1']"
            ).count()
            if count >= 4:
                return True
        except Exception:
            pass
        for sel in _OTP_SINGLE_SELECTORS:
            if await is_visible(page, sel):
                return True
        await asyncio.sleep(1.0)
    return False


async def _fill_otp(page: Page, code: str) -> None:
    """
    Fill OTP.
    Mirrors tool.js _0xbaf:
      If ≥ 6 individual maxlength=1 boxes → _0x1c0(ci[i], c[i]) each digit.
      Else → _0x1c0 on single autocomplete="one-time-code" or name="code" input.
    """
    boxes = page.locator("input[type='text'][maxlength='1'], input[maxlength='1']")
    count = await boxes.count()

    if count >= 4:
        # Individual digit boxes (Auth0 style)
        for i, ch in enumerate(code[:count]):
            box = boxes.nth(i)
            try:
                await box.click()
            except Exception:
                pass
            await box.fill(ch)
            await asyncio.sleep(0.1)  # _0x1ae(0x64)
    else:
        # Single OTP input
        for sel in _OTP_SINGLE_SELECTORS:
            ok = await set_react_input(page, sel, code)
            if ok:
                break


async def _fill_profile(task_id: str, page: Page, account: dict, timing: TimingCfg) -> None:
    """
    Fill name + birthday spinbuttons.
    Mirrors tool.js _0xcc0(d).

    The about-you page structure (as of 2026-04):
      - input[type='text', name='name']   — single combined name field
      - input[type='number', name='age']  — age in years (appears after name is set)
      - 3 role='spinbutton' elements      — year / month / day birthday pickers
    """
    bd = account["birthday"]
    T  = timing

    # ── Compute age ──────────────────────────────────────────────────────────
    from datetime import datetime as _dt
    today = _dt.now()
    age = today.year - bd["year"]
    if (today.month, today.day) < (bd["month"], bd["day"]):
        age -= 1
    age = max(age, 1)

    fname_result = await wait_any_element(page, _FNAME_SELECTORS, timeout_ms=5_000)
    lname_result = await wait_any_element(page, _LNAME_SELECTORS, timeout_ms=5_000)

    if fname_result and lname_result:
        f_sel, _ = fname_result
        l_sel, _ = lname_result
        await set_react_input(page, f_sel, account["firstName"])
        await set_react_input(page, l_sel, account["lastName"])
        logger.info(f"[{task_id}] Filled firstName/lastName via selectors")
    else:
        name_result = await wait_any_element(
            page,
            ["input[name='name']", "input[name='fullName']", "input[id*='name']"],
            timeout_ms=3_000,
        )
        if name_result:
            n_sel, _ = name_result
            full_name = f"{account['firstName']} {account['lastName']}"
            await set_react_input(page, n_sel, full_name)
            logger.info(f"[{task_id}] Filled combined name '{full_name}' via selector {n_sel!r}")
        else:
            # Final fallback: fill first visible non-form-control input with full name
            full_name = json.dumps(f"{account['firstName']} {account['lastName']}")
            filled = await page.evaluate(f"""
                () => {{
                    const BAD = new Set(['hidden','password','checkbox','radio',
                                         'submit','button','file','image','reset']);
                    const inputs = Array.from(document.querySelectorAll('input')).filter(el => {{
                        const r = el.getBoundingClientRect();
                        const s = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && !BAD.has(el.type)
                            && s.display !== 'none'
                            && s.visibility !== 'hidden';
                    }});
                    if (inputs.length > 0) {{
                        const el = inputs[0];
                        const nv = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        );
                        nv.set.call(el, {full_name});
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        el.dispatchEvent(new Event('blur', {{bubbles: true}}));
                        return inputs.map(i => ({{name:i.name,id:i.id,type:i.type,ph:i.placeholder}}));
                    }}
                    return null;
                }}
            """)
            if filled:
                logger.info(f"[{task_id}] Filled inputs[0] with name '{account['firstName']} {account['lastName']}' (JS fallback) — page inputs: {filled}")
            else:
                logger.warning(f"[{task_id}] No visible non-control inputs found for name on {page.url}")
                try:
                    all_inputs = await page.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map(el => ({
                            name: el.name, id: el.id, type: el.type,
                            placeholder: el.placeholder,
                            visible: el.getBoundingClientRect().width > 0
                        }))
                    """)
                    logger.debug(f"[{task_id}] All inputs on page: {all_inputs}")
                except Exception:
                    pass

    # ── Wait for age input to appear reactively (shown after name is set) ────
    # The registration form uses birthday spinbuttons (not an age input).
    # The age input (input[name='age']) only appears in the OAuth about-you flow.
    # Use a SHORT timeout (2s) so we don't block spinbutton filling for 30s.
    await asyncio.sleep(1.0)
    age_result = await wait_any_element(
        page, ["input[name='age']", "input[id*='age']"], timeout_ms=2_000
    )
    if age_result:
        a_sel, _ = age_result
        await set_react_input(page, a_sel, str(age))
        logger.info(f"[{task_id}] Filled age={age} via {a_sel!r}")
    else:
        logger.debug(f"[{task_id}] No age input visible — form uses birthday spinbuttons")

    # ── Birthday spinbuttons (year / month / day) ────────────────────────────
    # Use page.evaluate() for one-shot spinbutton detection — avoids locator.evaluate()
    # per-element Playwright timeouts (30 s each) that caused ~4-minute hangs.
    sb_info = await page.evaluate("""
        () => {
            const sbs = Array.from(document.querySelectorAll('[role="spinbutton"]'));
            return sbs.map((el, i) => ({
                idx:   i,
                label: (el.getAttribute('aria-label') || '').toLowerCase(),
                max:   parseInt(el.getAttribute('aria-valuemax') || '0', 10),
                now:   parseInt(el.getAttribute('aria-valuenow') || el.innerText || '0', 10),
            }));
        }
    """)
    logger.info(f"[{task_id}] Spinbutton info (one-shot): {sb_info}")

    if len(sb_info) >= 3:
        # Detect field order from aria-valuemax
        def _detect_sb_field(info: dict) -> str:
            label = info.get("label", "")
            mx    = info.get("max", 0)
            if "year"  in label or mx > 200:          return "year"
            if "month" in label or (0 < mx <= 12):    return "month"
            if "day"   in label or (12 < mx <= 31):   return "day"
            return "unknown"

        field_order = [_detect_sb_field(sb) for sb in sb_info[:3]]
        if set(field_order) != {"year", "month", "day"}:
            logger.debug(f"[{task_id}] Order detection ambiguous ({field_order}), defaulting MM/DD/YYYY")
            field_order = ["month", "day", "year"]

        logger.info(f"[{task_id}] Setting birthday {bd} via spinbuttons (order={field_order})")
        from src.browser.helpers import fill_spinbutton
        for i, field in enumerate(field_order):
            val = bd.get(field, 1)
            await fill_spinbutton(page, i, val)
            logger.debug(f"[{task_id}] sb[{i}] {field}={val} done")
    else:
        date_str = (
            f"{bd['year']}/{str(bd['month']).zfill(2)}/{str(bd['day']).zfill(2)}"
        )
        date_result = await wait_any_element(
            page,
            ["input[type='date']", "input[name*='birth']", "input[id*='birth']"],
            timeout_ms=3_000,
        )
        if date_result:
            d_sel, _ = date_result
            await set_react_input(page, d_sel, date_str)
        else:
            # Final fallback: fill second visible input with date "YYYY/MM/DD"
            bdate = json.dumps(date_str)
            filled_bd = await page.evaluate(f"""
                () => {{
                    const inputs = Array.from(document.querySelectorAll('input')).filter(el => {{
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0 && el.type !== 'hidden';
                    }});
                    if (inputs.length > 1) {{
                        const el = inputs[1];
                        const nv = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        );
                        nv.set.call(el, {bdate});
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return true;
                    }}
                    return false;
                }}
            """)
            if filled_bd:
                logger.info(f"[{task_id}] Filled second visible input with birthday (JS fallback)")

    await asyncio.sleep(T.pre_fill * 0.5)

    submitted = await click_submit_or_text(
        page, ["Continue", "Agree", "同意", "Next", "Finish", "Done", "继续"]
    )
    if not submitted:
        logger.warning(f"[{task_id}] Profile submit button not found")

    await asyncio.sleep(T.post_click)
    logger.debug(f"[{task_id}] After profile submit, URL={page.url}")


# ── CLI dry-run ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    async def _dry_run() -> None:
        logger.info("[dry-run] Simulating tool.js registration state machine")
        states = [
            "GOTO_SIGNUP  — navigate chatgpt.com/auth/login, find & click Sign Up",
            "FILL_EMAIL   — wait email input, fill, click Continue",
            "FILL_PASSWORD — wait password input (≤60s), fill, click Continue",
            "WAIT_CODE    — poll gptmail for 6-digit OTP code",
            "FILL_CODE    — fill individual digit boxes, click Continue",
            "FILL_PROFILE — fill firstName/lastName + birthday spinbuttons",
            "COMPLETE     — verify redirect to chatgpt.com",
        ]
        for i, s in enumerate(states, 1):
            logger.info(f"[task-0] [{i}/{len(states)}] {s}")
            await asyncio.sleep(0.15)
        logger.success("[dry-run] State machine trace complete — no errors")

    asyncio.run(_dry_run())
