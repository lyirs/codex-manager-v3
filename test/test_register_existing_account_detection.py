"""Offline checks for existing-account detection in src.browser.register.

Run:
    uv run python test/test_register_existing_account_detection.py
"""
from __future__ import annotations

import asyncio

from src.browser.register import (
    _already_exists_error_markers,
    _is_about_you_url,
    _looks_like_existing_account_password_page,
    _profile_validation_markers,
)


class _FakePage:
    def __init__(self, *, url: str, text: str = ""):
        self.url = url
        self._text = text

    async def evaluate(self, script: str):
        return self._text.lower()


async def _test_login_password_page():
    page = _FakePage(
        url="https://auth.openai.com/log-in/password",
        text="""
            Welcome back
            Enter your password
            Forgot password?
        """,
    )
    result = await _looks_like_existing_account_password_page("task-x", page)
    assert result is True, result


async def _test_signup_password_page_not_misclassified():
    page = _FakePage(
        url="https://auth.openai.com/log-in/password",
        text="""
            Sign up
            Create a password
            Continue signing up
        """,
    )
    result = await _looks_like_existing_account_password_page("task-x", page)
    assert result is False, result


async def _test_non_login_url():
    page = _FakePage(
        url="https://auth.openai.com/u/signup/password",
        text="Create a password",
    )
    result = await _looks_like_existing_account_password_page("task-x", page)
    assert result is False, result


async def _test_about_you_validation_markers():
    page = _FakePage(
        url="https://auth.openai.com/about-you",
        text="""
            Let's confirm your age
            Birthday
            We can't create an account with that info. Try again.
        """,
    )
    markers = await _profile_validation_markers(page)
    assert "lets confirm your age" in markers or "let's confirm your age" in markers, markers
    assert "birthday" in markers, markers


async def _test_about_you_url_helper():
    assert _is_about_you_url("https://auth.openai.com/about-you")
    assert _is_about_you_url("https://auth.openai.com/u/signup/about_you")
    assert not _is_about_you_url("https://auth.openai.com/log-in")


async def _test_auth_error_existing_account_markers():
    page = _FakePage(
        url="https://auth.openai.com/u/login/identifier?state=abc",
        text="""
            Oops, an error occurred!
            An error occurred during authentication (user_already_exists). Please try again.
        """,
    )
    markers = await _already_exists_error_markers(page)
    assert "user_already_exists" in markers, markers
    assert "an error occurred during authentication" in markers, markers


async def _main():
    await _test_login_password_page()
    await _test_signup_password_page_not_misclassified()
    await _test_non_login_url()
    await _test_about_you_validation_markers()
    await _test_about_you_url_helper()
    await _test_auth_error_existing_account_markers()
    print("Existing-account detection tests passed")


if __name__ == "__main__":
    asyncio.run(_main())
