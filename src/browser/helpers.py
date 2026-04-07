"""
browser/helpers.py — Low-level DOM interaction utilities.

All functions accept a playwright Page and work with both
camoufox (Firefox) and Chromium contexts.
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import Optional

from loguru import logger
from typing import Literal
from playwright.async_api import Page, Locator, TimeoutError as PWTimeoutError

import src.settings_db as _settings_db

_MOUSE_DEFAULTS = {
    "human_simulation": True,
    "steps_min": 4,
    "steps_max": 8,
    "step_delay_min": 0.003,
    "step_delay_max": 0.010,
    "hover_min": 0.02,
    "hover_max": 0.08,
}

# ── React-compatible input fill ───────────────────────────────────────────

_REACT_INPUT_JS = """
(args) => {
    const [selector, value] = args;
    const el = document.querySelector(selector);
    if (!el) return false;

    const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;

    // Focus and clear existing value
    el.focus();
    el.click();
    nativeSetter.call(el, '');
    el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: '' }));

    // Type character-by-character to trigger per-keystroke React validation.
    // Auth0 password fields keep the submit button disabled unless each keydown
    // → nativeSetter → InputEvent('input') sequence fires per character.
    let cur = '';
    for (const ch of value) {
        el.dispatchEvent(new KeyboardEvent('keydown',  { key: ch, bubbles: true, cancelable: true, composed: true }));
        el.dispatchEvent(new KeyboardEvent('keypress', { key: ch, bubbles: true, cancelable: true, composed: true }));
        cur += ch;
        nativeSetter.call(el, cur);
        el.dispatchEvent(new InputEvent('input',  { bubbles: true, composed: true, data: ch, inputType: 'insertText' }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup',    { key: ch, bubbles: true, cancelable: true, composed: true }));
    }

    el.dispatchEvent(new Event('blur',  { bubbles: true }));
    el.focus();
    return true;
}
"""


async def set_react_input(page: Page, selector: str, value: str) -> bool:
    """
    Fill an input element in a way that triggers React's onChange.

    Primary path: JS character-by-character approach (keydown → nativeSetter →
    InputEvent per char) so Auth0's per-keystroke React validation fires correctly.

    Fallback 1: Playwright press_sequentially (real browser key events per char).
    Fallback 2: Playwright fill() (bulk set — last resort, may leave button disabled).
    """
    # The JS fast-path relies on document.querySelector(), so skip it for
    # Playwright-only selector engines such as xpath=...
    if not selector.lstrip().startswith("xpath="):
        try:
            ok = await page.evaluate(_REACT_INPUT_JS, [selector, value])
            if ok:
                return True
        except Exception as exc:
            logger.debug(f"[helpers] JS fill failed for {selector!r}: {exc}")

    # Fallback 1: press_sequentially fires native key events per character
    try:
        el = page.locator(selector).first
        await el.click()
        await el.press("Control+a")
        await el.press("Delete")
        await el.press_sequentially(value, delay=40)
        return True
    except Exception as exc:
        logger.debug(f"[helpers] press_sequentially fallback failed for {selector!r}: {exc}")

    # Fallback 2: bulk fill (may not enable disabled submit buttons)
    try:
        el = page.locator(selector).first
        await el.fill(value)
        return True
    except Exception as exc:
        logger.warning(f"[helpers] fill fallback failed for {selector!r}: {exc}")
        return False


_BIRTHDAY_INPUT_SELECTORS = [
    "input[type='date']",
    "input[name*='birth' i]",
    "input[id*='birth' i]",
    "input[aria-label*='birth' i]",
    "input[placeholder*='birth' i]",
    "input[placeholder*='dob' i]",
    "input[placeholder*='mm/dd' i]",
    "input[placeholder*='dd/mm' i]",
    "input[placeholder*='yyyy' i]",
    "xpath=//label[contains(translate(normalize-space(.), 'BIRTHDAY', 'birthday'),'birthday')]/following::input[1]",
    "xpath=//*[self::label or self::div or self::span or self::p][contains(translate(normalize-space(.), 'BIRTHDAY', 'birthday'),'birthday')]/following::input[1]",
]


async def has_visible_birthday_controls(
    page: Page,
    *,
    timeout_ms: int = 1_200,
) -> bool:
    """
    Detect whether the current profile form already looks like a birthday/DOB page.

    This is intentionally conservative: if visible spinbuttons or birthday/date
    inputs already exist, callers should avoid trying the age-only fallback.
    """
    result = await wait_any_element(
        page,
        ["[role='spinbutton']", *_BIRTHDAY_INPUT_SELECTORS],
        timeout_ms=timeout_ms,
    )
    return result is not None


async def fill_birthday_input(
    page: Page,
    *,
    year: int,
    month: int,
    day: int,
    timeout_ms: int = 3_000,
) -> Optional[str]:
    """
    Fill a single visible birthday/date input.

    Supports:
      - native ``input[type='date']`` fields (expects YYYY-MM-DD)
      - masked/text birthday fields labeled or hinted as Birthday / DOB
      - common placeholders such as MM/DD/YYYY or DD/MM/YYYY

    Returns a short description of the successful selector/value, or None.
    """
    result = await wait_any_element(page, _BIRTHDAY_INPUT_SELECTORS, timeout_ms=timeout_ms)
    if not result:
        marker = f"codex-birthday-target-{random.randint(100000, 999999)}"
        match = await page.evaluate(
            """
            ([marker]) => {
                const BAD_TYPES = new Set([
                    'hidden', 'password', 'checkbox', 'radio', 'submit',
                    'button', 'file', 'image', 'reset'
                ]);
                const visible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 &&
                        s.display !== 'none' && s.visibility !== 'hidden';
                };
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const hasBirthdayWord = (text) =>
                    /(birthday|birth\\s*date|birthdate|date\\s*of\\s*birth|\\bdob\\b)/i.test(clean(text));
                const badHint = (text) =>
                    /(full\\s*name|first\\s*name|last\\s*name|given\\s*name|family\\s*name|\\bage\\b)/i.test(clean(text));
                const attrText = (el) => clean([
                    el.name || '',
                    el.id || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('placeholder') || '',
                    el.getAttribute('autocomplete') || '',
                ].join(' '));
                const pushNodeText = (parts, node) => {
                    if (!node || !visible(node)) return;
                    if (!['LABEL', 'DIV', 'SPAN', 'P', 'LEGEND'].includes(node.tagName)) return;
                    const text = clean(node.textContent);
                    if (text) parts.push(text);
                };
                const contextText = (el) => {
                    const parts = [];
                    if (el.id) {
                        try {
                            for (const label of document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`)) {
                                pushNodeText(parts, label);
                            }
                        } catch {}
                    }
                    for (const label of Array.from(el.labels || [])) {
                        pushNodeText(parts, label);
                    }
                    pushNodeText(parts, el.previousElementSibling);
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 3; depth += 1, node = node.parentElement) {
                        pushNodeText(parts, node.previousElementSibling);
                    }
                    return clean(parts.join(' | '));
                };

                let best = null;
                const inputs = Array.from(document.querySelectorAll('input')).filter((el) => {
                    const type = (el.type || 'text').toLowerCase();
                    return visible(el) && !BAD_TYPES.has(type);
                });

                for (const el of inputs) {
                    const own = attrText(el);
                    const ctx = contextText(el);
                    const combined = clean(`${own} | ${ctx}`);
                    if (badHint(combined)) continue;

                    let score = 0;
                    const type = (el.type || 'text').toLowerCase();
                    const placeholder = clean(el.getAttribute('placeholder') || '');
                    if (type === 'date') score += 8;
                    if (hasBirthdayWord(own)) score += 9;
                    if (hasBirthdayWord(ctx)) score += 7;
                    if (/\\bbday\\b/i.test(own)) score += 8;
                    if (/mm\\s*\\/\\s*dd|dd\\s*\\/\\s*mm|yyyy/i.test(placeholder)) score += 6;
                    if (/(birth|birthday|dob)/i.test(combined)) score += 3;
                    if (score <= 0) continue;

                    if (!best || score > best.score) {
                        best = { el, score };
                    }
                }

                if (!best) return null;
                best.el.setAttribute('data-codex-birthday-target', marker);
                return `input[data-codex-birthday-target="${marker}"]`;
            }
            """,
            [marker],
        )
        if match:
            result = (match, page.locator(match).first)
    if not result:
        return None

    sel, loc = result
    input_type = ((await loc.get_attribute("type")) or "text").lower()
    hint = " ".join(
        filter(
            None,
            [
                await loc.get_attribute("name"),
                await loc.get_attribute("id"),
                await loc.get_attribute("aria-label"),
                await loc.get_attribute("placeholder"),
            ],
        )
    ).lower()

    iso_value = f"{year}-{month:02d}-{day:02d}"
    us_values = [
        f"{month:02d}/{day:02d}/{year}",
        f"{month}/{day}/{year}",
        f"{month:02d}-{day:02d}-{year}",
        f"{month}.{day:02d}.{year}",
    ]
    eu_values = [
        f"{day:02d}/{month:02d}/{year}",
        f"{day}/{month}/{year}",
        f"{day:02d}-{month:02d}-{year}",
        f"{day:02d}.{month:02d}.{year}",
    ]
    candidate_values = [iso_value] if input_type == "date" else (
        eu_values + us_values + [iso_value]
        if "dd/mm" in hint or "day/month" in hint
        else us_values + [iso_value] + eu_values
    )
    goals = {
        re.sub(r"\D", "", iso_value),
        re.sub(r"\D", "", f"{month:02d}/{day:02d}/{year}"),
        re.sub(r"\D", "", f"{day:02d}/{month:02d}/{year}"),
    }

    for value in dict.fromkeys(candidate_values):
        ok = await set_react_input(page, sel, value)
        if not ok:
            continue
        await asyncio.sleep(0.25)
        try:
            current = await loc.input_value()
        except Exception:
            current = (await loc.get_attribute("value")) or ""
        normalized = re.sub(r"\D", "", current or "")
        if current == value or normalized in goals:
            if "data-codex-birthday-target" in sel:
                try:
                    await page.evaluate(
                        """(selector) => {
                            const el = document.querySelector(selector);
                            if (el) el.removeAttribute('data-codex-birthday-target');
                        }""",
                        sel,
                    )
                except Exception:
                    pass
            return f"{sel} -> {current or value}"

    if "data-codex-birthday-target" in sel:
        try:
            await page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (el) el.removeAttribute('data-codex-birthday-target');
                }""",
                sel,
            )
        except Exception:
            pass

    return None


async def fill_age_input(
    page: Page,
    *,
    age: int,
    timeout_ms: int = 3_000,
) -> Optional[str]:
    """
    Fill a single visible age input.

    Supports both explicit age fields (name/id/placeholder/aria-label) and
    layouts where only a nearby visible label contains the text "Age".
    """
    selectors = [
        "input[type='number']",
        "input[name*='age' i]",
        "input[id*='age' i]",
        "input[aria-label*='age' i]",
        "input[placeholder*='age' i]",
        "xpath=//label[normalize-space()='Age']/following-sibling::input[1]",
        "xpath=//label[contains(translate(normalize-space(.), 'AGE', 'age'),'age')]/following-sibling::input[1]",
    ]
    result = await wait_any_element(page, selectors, timeout_ms=timeout_ms)
    if not result:
        marker = f"codex-age-target-{random.randint(100000, 999999)}"
        match = await page.evaluate(
            """
            ([marker]) => {
                const BAD_TYPES = new Set([
                    'hidden', 'password', 'checkbox', 'radio', 'submit',
                    'button', 'file', 'image', 'reset'
                ]);
                const visible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 &&
                        s.display !== 'none' && s.visibility !== 'hidden';
                };
                const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                const hasAgeWord = (text) => /\\bage\\b/i.test(clean(text));
                const badHint = (text) => /(full\\s*name|first\\s*name|last\\s*name|birthday|birth\\s*date|birthdate|\\bdob\\b|month|day|year)/i.test(clean(text));
                const attrText = (el) => clean([
                    el.name || '',
                    el.id || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('placeholder') || '',
                ].join(' '));
                const contextText = (el) => {
                    const bits = [];
                    const push = (text) => {
                        const val = clean(text);
                        if (val) bits.push(val);
                    };
                    const pushNode = (node) => {
                        if (!node || !visible(node)) return;
                        if (!['LABEL', 'DIV', 'SPAN', 'P'].includes(node.tagName)) return;
                        push(node.textContent);
                    };
                    if (el.id) {
                        try {
                            for (const label of document.querySelectorAll(`label[for="${CSS.escape(el.id)}"]`)) {
                                push(label.textContent);
                            }
                        } catch {}
                    }
                    for (const label of Array.from(el.labels || [])) {
                        push(label.textContent);
                    }
                    pushNode(el.previousElementSibling);
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 3; depth += 1, node = node.parentElement) {
                        pushNode(node.previousElementSibling);
                    }
                    return clean(bits.join(' | '));
                };

                let best = null;
                const inputs = Array.from(document.querySelectorAll('input')).filter((el) => {
                    const type = (el.type || 'text').toLowerCase();
                    return visible(el) && !BAD_TYPES.has(type);
                });
                for (const el of inputs) {
                    const own = attrText(el);
                    const ctx = contextText(el);
                    const combined = clean(`${own} | ${ctx}`);
                    if (badHint(combined)) continue;

                    let score = 0;
                    if ((el.type || '').toLowerCase() === 'number') score += 3;
                    if (hasAgeWord(own)) score += 5;
                    if (hasAgeWord(ctx)) score += 4;
                    if (score <= 0) continue;

                    if (!best || score > best.score) {
                        best = { el, score };
                    }
                }
                if (!best) return null;
                best.el.setAttribute('data-codex-age-target', marker);
                return `input[data-codex-age-target="${marker}"]`;
            }
            """,
            [marker],
        )
        if not match:
            return None
        result = (match, page.locator(match).first)

    sel, loc = result
    value = str(max(age, 1))
    ok = await set_react_input(page, sel, value)
    if not ok:
        return None
    await asyncio.sleep(0.25)
    try:
        current = await loc.input_value()
    except Exception:
        current = (await loc.get_attribute("value")) or ""
    normalized = re.sub(r"\D", "", current or "")
    if current == value or normalized == value:
        if "data-codex-age-target" in sel:
            try:
                await page.evaluate(
                    """(selector) => {
                        const el = document.querySelector(selector);
                        if (el) el.removeAttribute('data-codex-age-target');
                    }""",
                    sel,
                )
            except Exception:
                pass
        return f"{sel} -> {current or value}"
    return None


async def wait_button_enabled(
    page: Page,
    selector: str = "button[type='submit']",
    timeout_ms: int = 5_000,
) -> bool:
    """
    Poll until the first visible button matching *selector* is not disabled.
    Returns True if the button becomes enabled within the timeout, else False.
    Useful for verifying that React form validation has accepted the input.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    while loop.time() < deadline:
        try:
            el = page.locator(selector).first
            if await el.is_visible():
                disabled      = await el.get_attribute("disabled")
                aria_disabled = await el.get_attribute("aria-disabled")
                if disabled is None and aria_disabled != "true":
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.25)
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


async def fill_spinbutton(page: Page, index: int, target: int) -> None:
    """
    Locate the spinbutton at *index* (0-based) in the DOM and adjust it to
    *target* value using ArrowUp/ArrowDown keys.

    This is a convenience wrapper over set_spinbutton() that selects the
    nth spinbutton by index rather than requiring a pre-built Locator.
    Called by oauth._fill_about_you_js() during the post-registration
    OAuth flow's about-you profile form filling.
    """
    locator = page.locator("[role='spinbutton']").nth(index)
    await set_spinbutton(page, locator, target)


# ── CLI dry-run ────────────────────────────────────────────────────────────

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


async def dismiss_known_obstructions(task_id: str, page: Page) -> None:
    """
    Dismiss or hide common overlays that can block the registration entry page.

    Current targets:
      - ChatGPT cookie banner at the bottom
      - Google One Tap / credential-picker prompt at the top-right
    """
    # First, try the polite path: click visible cookie buttons if present.
    for texts in (
        ["Reject non-essential", "Accept all", "Accept All", "I agree", "Got it"],
        ["Manage Cookies"],
    ):
        try:
            btn = await find_button_by_texts(page, texts)
            if btn and await btn.is_visible():
                label = await btn.inner_text()
                await btn.click()
                logger.info(f"[{task_id}] Dismissed page obstruction via button {label[:60]!r}")
                await asyncio.sleep(0.4)
                break
        except Exception:
            pass

    # Then hide known fixed overlays directly when they are still present.
    try:
        hidden = await page.evaluate("""
            () => {
                const hidden = [];
                const hide = (el, reason) => {
                    if (!el || hidden.includes(reason)) return;
                    el.style.setProperty('display', 'none', 'important');
                    el.style.setProperty('visibility', 'hidden', 'important');
                    el.style.setProperty('pointer-events', 'none', 'important');
                    hidden.push(reason);
                };
                const isFixedish = (el) => {
                    const s = window.getComputedStyle(el);
                    const z = Number.parseInt(s.zIndex || '0', 10);
                    return s.position === 'fixed' || s.position === 'sticky' || z >= 1000;
                };
                const escalate = (el) => {
                    let cur = el;
                    for (let i = 0; i < 4 && cur && cur.parentElement; i++) {
                        if (!isFixedish(cur.parentElement)) break;
                        cur = cur.parentElement;
                    }
                    return cur;
                };

                const directSelectors = [
                    '#credential_picker_container',
                    '#credential_picker_iframe',
                    'iframe[src*="accounts.google.com/gsi/"]',
                    'iframe[src*="accounts.google.com"]',
                ];
                for (const sel of directSelectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        hide(escalate(el), sel);
                    }
                }

                const textBlocks = Array.from(document.querySelectorAll('div,section,aside,dialog')).filter(el => {
                    if (!isFixedish(el)) return false;
                    const text = (el.innerText || '').toLowerCase();
                    if (!text) return false;
                    const looksLikeCookie =
                        text.includes('we use cookies') ||
                        text.includes('manage cookies') ||
                        text.includes('accept all') ||
                        text.includes('reject non-essential') ||
                        text.includes('cookie policy');
                    const looksLikeGooglePrompt =
                        text.includes('google') &&
                        (text.includes('openai') || text.includes('login') || text.includes('ログイン'));
                    return looksLikeCookie || looksLikeGooglePrompt;
                });
                for (const el of textBlocks) {
                    const text = (el.innerText || '').slice(0, 50);
                    hide(el, text || 'fixed-text-block');
                }
                return hidden;
            }
        """)
        if hidden:
            logger.info(f"[{task_id}] Hid obstructive overlay(s): {hidden}")
            await asyncio.sleep(0.3)
    except Exception as exc:
        logger.debug(f"[{task_id}] Overlay dismissal skipped: {exc}")


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

async def set_spinbutton(page: Page, locator: Locator, target: int) -> None:
    """
    Adjust a spinbutton (role='spinbutton') to reach *target* using ArrowUp/ArrowDown.
    Mirrors JS _0x1d1 exactly:
      1. Focus + click, wait 200ms
      2. If no digit in textContent, send ArrowDown once to activate (100ms)
      3. Loop up to 100 iterations: re-read value each time, ArrowUp/ArrowDown to adjust
      4. After loop: force-set aria-valuenow + dispatch change + blur events
    """
    await locator.focus()
    await locator.click()
    await asyncio.sleep(0.2)   # 0xc8 = 200ms

    # Step 1: If no digit visible, send ArrowDown once to activate (mirrors _0x1d1)
    content: str = await locator.evaluate("el => el.textContent || ''")
    if not any(c.isdigit() for c in content):
        await locator.press("ArrowDown")
        await asyncio.sleep(0.1)   # 0x64 = 100ms

    # Step 2: Loop up to 100 iterations (m = 0x64 in tool.js)
    for _ in range(100):
        text: str = await locator.evaluate(
            "el => el.textContent || el.getAttribute('aria-valuenow') || ''"
        )
        digits = "".join(c for c in text if c.isdigit())
        if not digits:
            # Still no digit — keep pressing ArrowDown to activate
            await locator.press("ArrowDown")
            await asyncio.sleep(0.1)
            continue
        try:
            current = int(digits)
        except ValueError:
            continue

        diff = target - current
        if diff == 0:
            break
        key = "ArrowUp" if diff > 0 else "ArrowDown"
        await locator.press(key)
        await asyncio.sleep(0.08)   # 0x50 = 80ms

    # Step 3: Force-set aria-valuenow + dispatch change/blur (mirrors _0x1d1 end)
    await locator.evaluate(
        "(el, v) => {"
        "  el.setAttribute('aria-valuenow', String(v));"
        "  el.dispatchEvent(new Event('change', {bubbles: true}));"
        "  el.dispatchEvent(new Event('blur', {bubbles: true}));"
        "}",
        target,
    )
    await asyncio.sleep(0.1)   # 0x64 = 100ms


# ── Error page detection ──────────────────────────────────────────────────

_ERROR_PHRASES = [
    "糟糕", "出错了", "Operation timed out", "操作超时",
    "Something went wrong", "error occurred",
    "Access denied", "403 Forbidden",
]

_CLOUDFLARE_PHRASES = [
    "Just a moment...",
    "Checking your browser",
    "Verify you are human",
    "Please stand by, while we are checking your browser",
    "Enable JavaScript and cookies to continue",
    "__cf_chl_rt_tk",
]


async def is_error_page(page: Page) -> bool:
    try:
        text = await page.evaluate("() => document.body?.innerText || ''")
        return any(phrase.lower() in text.lower() for phrase in _ERROR_PHRASES)
    except Exception:
        return False


async def is_cloudflare_challenge(page: Page) -> bool:
    """
    Detect a Cloudflare interstitial/challenge page.

    We keep this separate from generic error-page detection because the
    registration flow may succeed if we simply wait for the challenge to clear.
    """
    try:
        url = (page.url or "").lower()
        if "__cf_chl_rt_tk=" in url:
            return True
        text = await page.evaluate(
            "() => [document.title || '', document.body?.innerText || ''].join('\\n')"
        )
        lowered = text.lower()
        return any(phrase.lower() in lowered for phrase in _CLOUDFLARE_PHRASES)
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

    Configuration is read from the DB ``mouse`` section (set via the WebUI
    advanced page).  If the DB is unavailable, built-in defaults are used.

        mouse:
          human_simulation: true   # false → plain locator.click() (faster)
          steps_min:       4
          steps_max:       8
          step_delay_min:  0.003
          step_delay_max:  0.010
          hover_min:       0.02
          hover_max:       0.08
    """
    # ── Load mouse config from DB (source of truth) ───────────────────────────
    try:
        _mc = await _settings_db.get_section("mouse") or {}
    except Exception:
        _mc = dict(_MOUSE_DEFAULTS)

    # ── Fast path: human simulation disabled → direct click ───────────────────
    if not _mc.get("human_simulation", True):
        try:
            await locator.click()
        except Exception as exc:
            logger.debug(f"[helpers] direct click error: {exc}")
        return

    # ── Slow path: curved-path human-like movement then click ─────────────────
    steps_min = float(_mc.get("steps_min",      4))
    steps_max = float(_mc.get("steps_max",      8))
    step_dmin = float(_mc.get("step_delay_min", 0.003))
    step_dmax = float(_mc.get("step_delay_max", 0.010))
    hover_min = float(_mc.get("hover_min",      0.02))
    hover_max = float(_mc.get("hover_max",      0.08))

    try:
        box = await locator.bounding_box()
        if not box:
            await locator.click()
            return

        target_x = box["x"] + box["width"]  * random.uniform(0.2, 0.8)
        target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
        start_x  = random.randint(200, 900)
        start_y  = random.randint(150, 600)

        steps = random.randint(int(steps_min), int(steps_max))
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3.0 - 2.0 * t)   # smoothstep easing
            x = start_x + (target_x - start_x) * t + random.uniform(-2, 2)
            y = start_y + (target_y - start_y) * t + random.uniform(-2, 2)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(step_dmin, step_dmax))

        await asyncio.sleep(random.uniform(hover_min, hover_max))
        await page.mouse.click(target_x, target_y)

    except Exception as exc:
        logger.debug(f"[helpers] human_move_and_click fallback: {exc}")
        await locator.click()


