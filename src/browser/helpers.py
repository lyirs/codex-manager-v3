"""
browser/helpers.py — Low-level DOM interaction utilities.

All functions accept a playwright Page and work with both
camoufox (Firefox) and Chromium contexts.
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from loguru import logger
from typing import Literal
from playwright.async_api import Page, Locator, TimeoutError as PWTimeoutError

# ── React-compatible input fill ───────────────────────────────────────────

_REACT_INPUT_JS = """
(args) => {
    const [selector, value] = args;
    const el = document.querySelector(selector);
    if (!el) return false;
    el.focus();
    // Trigger React's synthetic onChange via nativeInputValueSetter
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input',  { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    // Also dispatch key events for each character (some forms require it)
    for (const ch of value) {
        el.dispatchEvent(new KeyboardEvent('keydown',  {key: ch, bubbles: true}));
        el.dispatchEvent(new KeyboardEvent('keypress', {key: ch, bubbles: true}));
        el.dispatchEvent(new KeyboardEvent('keyup',    {key: ch, bubbles: true}));
    }
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    el.focus();
    return true;
}
"""


async def set_react_input(page: Page, selector: str, value: str) -> bool:
    """
    Fill an input element in a way that triggers React's onChange.
    Falls back to playwright's built-in fill() if the JS approach fails.
    """
    try:
        ok = await page.evaluate(_REACT_INPUT_JS, [selector, value])
        if ok:
            return True
    except Exception as exc:
        logger.debug(f"[helpers] JS fill failed for {selector!r}: {exc}")

    # Fallback: use Playwright locator directly
    try:
        el = page.locator(selector).first
        await el.fill(value)
        return True
    except Exception as exc:
        logger.warning(f"[helpers] fill fallback failed for {selector!r}: {exc}")
        return False


# ── Element waiting ───────────────────────────────────────────────────────

async def wait_element(
    page: Page,
    selector: str,
    timeout_ms: int = 20_000,
    state: Literal["attached", "detached", "hidden", "visible"] = "visible",
) -> Optional[Locator]:
    """Wait for selector and return its Locator, or None on timeout."""
    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return page.locator(selector).first
    except PWTimeoutError:
        return None


async def wait_any_element(
    page: Page,
    selectors: list[str],
    timeout_ms: int = 20_000,
) -> Optional[tuple[str, Locator]]:
    """
    Wait for whichever selector appears first (visibility-checked).
    Returns (matched_selector, locator) or None on timeout.
    Each selector is tried individually so compound CSS is never needed.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    while loop.time() < deadline:
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible():
                    return sel, el
            except Exception:
                pass
        await asyncio.sleep(0.4)
    return None


# ── Button finding ────────────────────────────────────────────────────────

async def find_button_by_texts(page: Page, texts: list[str]) -> Optional[Locator]:
    """
    Find the first visible button/link whose text matches one of *texts*
    (case-insensitive, partial match).
    Searches button, a, [role='button'], div, span — mirroring JS querySelectorAll.
    """
    for text in texts:
        for tag in ("button", "a", "[role='button']", "div", "span"):
            try:
                loc = page.locator(f"{tag}:has-text('{text}')").first
                if await loc.is_visible():
                    return loc
            except Exception:
                pass
    return None


async def click_button_by_texts(page: Page, texts: list[str]) -> bool:
    btn = await find_button_by_texts(page, texts)
    if btn:
        await btn.click()
        return True
    return False


async def click_submit_or_text(page: Page, texts: list[str]) -> bool:
    """
    Click a submit/continue button.
    Priority 1: button[type='submit']  (mirrors JS: document.querySelector('button[type="submit"]'))
    Priority 2: text-based search (find_button_by_texts)
    Priority 3: Enter press on active element
    """
    # Priority 1: visible submit button
    try:
        sub = page.locator("button[type='submit']").first
        if await sub.is_visible():
            await sub.click()
            return True
    except Exception:
        pass

    # Priority 2: text-based
    return await click_button_by_texts(page, texts)


async def find_signup_button(task_id: str, page: Page) -> Optional[Locator]:
    """
    Find the Sign Up entry-point using multiple strategies.
    Mirrors JS _0x548_inner GOTO_SIGNUP detection order:
      data-testid → href*signup → exact text (all elements) → partial text
    """
    # Strategy 1: data-testid (fastest when present)
    for testid in ("signup-link", "signup-button", "create-account", "register-button"):
        try:
            loc = page.locator(f"[data-testid='{testid}']").first
            if await loc.is_visible():
                logger.debug(f"[{task_id}] signup via data-testid={testid}")
                return loc
        except Exception:
            pass

    # Strategy 2: anchor with signup/register in href
    # auth.openai.com uses '/u/signup' sub-path; also match generic patterns.
    try:
        loc = page.locator(
            "a[href*='u/signup'], a[href*='signup'], a[href*='register'], a[href*='create-account']"
        ).first
        if await loc.is_visible():
            logger.debug(f"[{task_id}] signup via href")
            return loc
    except Exception:
        pass

    # Strategy 3: exact text match across ALL element types (mirrors JS regex)
    exact_texts = [
        "Sign up", "Sign Up", "Sign up for free",
        "Create account", "Create Account",
        "Get started", "Register",
        "注册", "免费注册",
    ]
    for text in exact_texts:
        for tag in ("button", "a", "[role='button']", "div", "span", "p"):
            try:
                loc = page.locator(tag).get_by_text(text, exact=True).first
                if await loc.is_visible():
                    logger.debug(f"[{task_id}] signup via exact text={text!r}")
                    return loc
            except Exception:
                pass

    # Strategy 4: partial text fallback
    logger.debug(f"[{task_id}] signup falling back to partial text search")
    return await find_button_by_texts(page, ["Sign up", "Create account", "注册"])


# ── Spinbutton (year / month / day) ──────────────────────────────────────

async def fill_spinbutton(page: Page, sb_index: int, target: int) -> None:
    """
    Fill the *sb_index*-th [role='spinbutton'] on the page to *target*.

    Strategy (no locator.evaluate() → no 30-second per-element Playwright timeouts):
      1. Read pre-click value via page.evaluate() on querySelectorAll.
      2. Click to focus, then page.keyboard.type(str(target)) — trusted events.
      3. Re-read value; if correct → Tab and return.
      4. Fallback: ArrowUp/Down for the delta (capped at 200 presses).
    """
    CSS = "[role='spinbutton']"

    # ── Pre-click snapshot (one JS call, no element timeout) ─────────────
    before: Optional[int] = await page.evaluate(f"""
        () => {{
            const el = document.querySelectorAll('{CSS}')[{sb_index}];
            if (!el) return null;
            const raw = el.getAttribute('aria-valuenow') || el.innerText || '';
            const n = parseInt(raw.replace(/[^0-9-]/g, ''), 10);
            return isNaN(n) ? 0 : n;
        }}
    """)

    # ── Click to focus ────────────────────────────────────────────────────
    locator = page.locator(CSS).nth(sb_index)
    try:
        await locator.click(timeout=3_000)
    except Exception as _ce:
        logger.warning(f"[fill_spinbutton] sb[{sb_index}] click failed: {_ce}")
        return
    await asyncio.sleep(0.2)

    # ── Type the value (trusted events, multi-char accumulation) ─────────
    await page.keyboard.type(str(target), delay=30)
    await asyncio.sleep(0.4)

    # ── Post-type snapshot ────────────────────────────────────────────────
    after: Optional[int] = await page.evaluate(f"""
        () => {{
            const el = document.querySelectorAll('{CSS}')[{sb_index}];
            if (!el) return null;
            const raw = el.getAttribute('aria-valuenow') || el.innerText || '';
            const n = parseInt(raw.replace(/[^0-9-]/g, ''), 10);
            return isNaN(n) ? null : n;
        }}
    """)

    logger.debug(
        f"[fill_spinbutton] sb[{sb_index}] target={target} "
        f"before={before} after_type={after}"
    )

    if after == target:
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.15)
        return

    # ── ArrowKey correction (capped at 200 presses ≈ 2 s) ────────────────
    current = after if after is not None else (before or 0)
    diff    = target - current

    if abs(diff) > 200:
        logger.warning(
            f"[fill_spinbutton] sb[{sb_index}] delta too large ({diff}); "
            f"current={current} target={target} — skipping arrow correction"
        )
    else:
        key = "ArrowUp" if diff > 0 else "ArrowDown"
        logger.debug(f"[fill_spinbutton] sb[{sb_index}] arrow-key fallback: {key} × {abs(diff)}")
        for _ in range(abs(diff)):
            await page.keyboard.press(key)
            await asyncio.sleep(0.01)

    await page.keyboard.press("Tab")
    await asyncio.sleep(0.15)


async def set_spinbutton(page: Page, locator: Locator, target: int) -> None:
    """
    Adjust a spinbutton (role='spinbutton') to reach *target*.

    Fast path 1: JS value-setter for native <input type='number'>.
    Fast path 2: Click + select-all + type (works for many date-picker spinbuttons).
    Fast path 3: Batch JS keydown dispatch (single IPC call, works for ARIA spinbuttons).
    Fallback:    Individual ArrowUp/Down key presses (minimal delay).
    """
    # ── Diagnostic: log element info ─────────────────────────────────────
    try:
        info = await locator.evaluate("""
            (el) => ({
                tag:    el.tagName,
                role:   el.getAttribute('role'),
                label:  el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '',
                min:    el.getAttribute('aria-valuemin') || el.min || '',
                max:    el.getAttribute('aria-valuemax') || el.max || '',
                now:    el.getAttribute('aria-valuenow') || el.value || el.textContent || '',
                type:   el.type || '',
            })
        """)
        logger.debug(f"[spinbutton] target={target} info={info}")
    except Exception:
        info = {}

    # Fast path 1: native <input type='number'> — set via React-compatible JS
    try:
        ok = await locator.evaluate(f"""
            (el) => {{
                if (el.tagName !== 'INPUT') return false;
                const nv = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                );
                nv.set.call(el, '{target}');
                el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }}
        """)
        if ok:
            await locator.press("Tab")
            logger.debug(f"[spinbutton] fast-path-1 (JS native setter) used for target={target}")
            return
    except Exception:
        pass

    # Click to focus the element
    await locator.click()
    await asyncio.sleep(0.1)

    # Fast path 2: select-all + type (works for many custom spinbuttons)
    try:
        await locator.press("Control+a")
        await asyncio.sleep(0.05)
        await locator.type(str(target), delay=0)
        await asyncio.sleep(0.15)
        # Verify the value was accepted
        accepted_str: str = await locator.evaluate(
            "el => el.getAttribute('aria-valuenow') || el.value || el.textContent || ''"
        )
        accepted_val = "".join(c for c in accepted_str if c.isdigit() or c == "-")
        if accepted_val and int(accepted_val) == target:
            await locator.press("Tab")
            logger.debug(f"[spinbutton] fast-path-2 (type) used for target={target}")
            return
    except Exception:
        pass

    # Read current value to minimise the number of arrow key presses
    current_str: str = await locator.evaluate(
        "el => el.getAttribute('aria-valuenow') || el.value || el.textContent || '0'"
    )
    try:
        current = int("".join(c for c in current_str if c.isdigit() or c == "-"))
    except ValueError:
        current = 0

    diff = target - current
    logger.debug(f"[spinbutton] arrow-key fallback: current={current} target={target} diff={diff}")

    if diff == 0:
        await locator.press("Tab")
        return

    key_name = "ArrowUp" if diff > 0 else "ArrowDown"
    key_code  = 38        if diff > 0 else 40

    # Fast path 3: Batch JS keydown dispatch in a single IPC call
    # (dispatches all N events without Python ↔ Playwright round-trip per press)
    try:
        await locator.evaluate(f"""
            (el) => {{
                el.focus();
                for (let i = 0; i < {abs(diff)}; i++) {{
                    el.dispatchEvent(new KeyboardEvent('keydown', {{
                        key: '{key_name}', keyCode: {key_code},
                        bubbles: true, cancelable: true
                    }}));
                    el.dispatchEvent(new KeyboardEvent('keyup', {{
                        key: '{key_name}', keyCode: {key_code},
                        bubbles: true, cancelable: true
                    }}));
                }}
            }}
        """)
        # Verify the JS events actually changed the value
        await asyncio.sleep(0.2)
        post_str: str = await locator.evaluate(
            "el => el.getAttribute('aria-valuenow') || el.value || el.textContent || ''"
        )
        post_val = "".join(c for c in post_str if c.isdigit() or c == "-")
        if post_val and int(post_val) == target:
            await locator.press("Tab")
            logger.debug(f"[spinbutton] fast-path-3 (batch JS keydown) used for target={target}")
            return
        logger.debug(f"[spinbutton] batch JS keydown: post_val={post_str!r} expected={target} — falling back to individual presses")
    except Exception as e:
        logger.debug(f"[spinbutton] batch JS keydown failed: {e}")

    # Fallback: individual Playwright key presses with minimal delay
    # Each locator.press() dispatches a trusted event that React can't ignore.
    for _ in range(abs(diff)):
        await locator.press(key_name)
        await asyncio.sleep(0.002)

    await locator.press("Tab")


async def detect_spinbutton_date_order(
    page: Page,
    spinbuttons,  # Playwright Locator
) -> list[str]:
    """
    Detect the date-field order of 3 spinbuttons (year / month / day).

    Strategy:
    1. Check aria-label for obvious keywords (year, month, day).
    2. Infer from aria-valuemin / aria-valuemax (year max > 1000, month max ≤ 31, day max ≤ 31 but year > 31).
    3. Fall back to assumed MM/DD/YYYY order (US locale default).

    Returns a list of 3 strings, e.g. ['month', 'day', 'year'].
    """
    order: list[str] = []
    for i in range(3):
        try:
            meta = await spinbuttons.nth(i).evaluate("""
                (el) => ({
                    label: (el.getAttribute('aria-label') || '').toLowerCase(),
                    min:   parseInt(el.getAttribute('aria-valuemin') || el.min || '0', 10),
                    max:   parseInt(el.getAttribute('aria-valuemax') || el.max || '0', 10),
                    now:   parseInt(el.getAttribute('aria-valuenow') || el.value || el.textContent || '0', 10),
                })
            """)
            label = meta.get("label", "")
            mn    = meta.get("min", 0)
            mx    = meta.get("max", 0)

            if "year" in label or mx > 200:
                order.append("year")
            elif "month" in label or (1 <= mx <= 12):
                order.append("month")
            elif "day" in label or (1 <= mx <= 31 and mx <= 31):
                order.append("day")
            else:
                order.append(f"unknown({i})")
        except Exception:
            order.append(f"unknown({i})")

    # If detection produced duplicates or unknowns, fall back to MM/DD/YYYY
    if set(order) != {"year", "month", "day"}:
        logger.debug(f"[spinbutton] detection ambiguous ({order}), assuming MM/DD/YYYY")
        order = ["month", "day", "year"]

    logger.debug(f"[spinbutton] detected date field order: {order}")
    return order


# ── Error page detection ──────────────────────────────────────────────────

_ERROR_PHRASES = [
    "糟糕", "出错了", "Operation timed out", "操作超时",
    "Something went wrong", "error occurred",
    "Access denied", "403 Forbidden",
]


async def is_error_page(page: Page) -> bool:
    try:
        text = await page.evaluate("() => document.body?.innerText || ''")
        return any(phrase.lower() in text.lower() for phrase in _ERROR_PHRASES)
    except Exception:
        return False


# ── Visibility check ──────────────────────────────────────────────────────

async def is_visible(page: Page, selector: str) -> bool:
    try:
        return await page.locator(selector).first.is_visible()
    except Exception:
        return False


# ── Human-like interaction ────────────────────────────────────────────────

async def jitter_sleep(base: float, jitter: float = 0.3) -> None:
    """Sleep for base ± jitter seconds to mimic human reaction time."""
    await asyncio.sleep(base + random.uniform(-jitter, jitter))


async def human_move_and_click(page: Page, locator: Locator) -> None:
    """
    Move the mouse to a locator via a curved path with random jitter,
    then click — mimicking human cursor behavior that bot-detection looks for.

    Auth0 / Cloudflare track mouse history before a click; a direct
    playwright .click() with no prior movement is a strong bot signal.
    """
    try:
        box = await locator.bounding_box()
        if not box:
            await locator.click()
            return

        # Random landing point within middle 60% of element
        target_x = box["x"] + box["width"]  * random.uniform(0.2, 0.8)
        target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)

        # Start from a plausible "previous" cursor position
        start_x = random.randint(200, 900)
        start_y = random.randint(150, 600)

        # Move in N micro-steps with ease-in-out + slight per-step noise
        steps = random.randint(8, 16)
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3.0 - 2.0 * t)          # smoothstep easing
            x = start_x + (target_x - start_x) * t + random.uniform(-2, 2)
            y = start_y + (target_y - start_y) * t + random.uniform(-2, 2)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.008, 0.025))

        # Brief human "hover" pause before pressing
        await asyncio.sleep(random.uniform(0.05, 0.18))
        await page.mouse.click(target_x, target_y)

    except Exception as exc:
        logger.debug(f"[helpers] human_move_and_click fallback: {exc}")
        await locator.click()


