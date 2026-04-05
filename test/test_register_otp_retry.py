"""Minimal offline checks for OTP retry helpers in src.browser.register.

Run:
    uv run python test/test_register_otp_retry.py
"""
from __future__ import annotations

import asyncio

from src.browser.register import _classify_otp_submit_result, _poll_fresh_code
from src.mail.base import MailClient


class _FakeLocator:
    def __init__(self, *, visible: bool = False, count: int = 0):
        self._visible = visible
        self._count = count
        self.first = self

    async def is_visible(self):
        return self._visible

    async def count(self):
        return self._count


class _FakePage:
    def __init__(self, *, url: str, text: str = "", otp_boxes: int = 0, visible_selectors: set[str] | None = None):
        self.url = url
        self._text = text
        self._otp_boxes = otp_boxes
        self._visible_selectors = visible_selectors or set()

    async def evaluate(self, script: str):
        return self._text.lower()

    def locator(self, selector: str):
        if "maxlength='1'" in selector or 'maxlength="1"' in selector:
            return _FakeLocator(count=self._otp_boxes, visible=self._otp_boxes > 0)
        return _FakeLocator(visible=selector in self._visible_selectors)


class _FakeMailClient(MailClient):
    def __init__(self, codes: list[str | None]):
        self._codes = list(codes)

    async def generate_email(self, prefix=None, domain=None):
        return "x@example.com"

    async def poll_code(self, email: str, timeout: int = 120):
        await asyncio.sleep(0)
        if self._codes:
            return self._codes.pop(0)
        return None


async def _test_incorrect():
    page = _FakePage(
        url="https://auth.openai.com/u/signup/email-verification",
        text="Incorrect code. Please try again.",
        otp_boxes=6,
    )
    result = await _classify_otp_submit_result("task-x", page, timeout_ms=50)
    assert result == "incorrect", result


async def _test_accepted_by_profile_url():
    page = _FakePage(
        url="https://auth.openai.com/u/signup/about-you",
        text="",
        otp_boxes=0,
    )
    result = await _classify_otp_submit_result("task-x", page, timeout_ms=50)
    assert result == "accepted", result


async def _test_poll_fresh_code():
    mail = _FakeMailClient(["111111", "111111", "222222"])
    code = await _poll_fresh_code(
        "task-x",
        mail,
        "x@example.com",
        previous_code="111111",
        timeout=5,
    )
    assert code == "222222", code


async def _main():
    await _test_incorrect()
    await _test_accepted_by_profile_url()
    await _test_poll_fresh_code()
    print("OTP retry helper tests passed")


if __name__ == "__main__":
    asyncio.run(_main())

