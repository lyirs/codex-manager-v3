"""Offline checks for Outlook Graph -> IMAP fallback.

Run:
    uv run python test/test_outlook_graph_fallback.py
"""
from __future__ import annotations

import asyncio

from src.mail.outlook import (
    OutlookMailClient,
    _OutlookGraphScopeUnavailable,
    _OutlookTokenError,
    _looks_like_scope_mismatch,
)


class _FallbackClient(OutlookMailClient):
    def __init__(self):
        super().__init__(
            email="demo@outlook.com",
            client_id="cid",
            tenant_id="consumers",
            refresh_token="rt",
            fetch_method="graph",
        )

    async def _poll_graph(self, timeout: int):
        raise _OutlookGraphScopeUnavailable("graph scope missing")

    async def _poll_imap(self, timeout: int):
        return "123456"


async def _test_graph_falls_back_to_imap():
    client = _FallbackClient()
    code = await client.poll_code("demo@outlook.com", timeout=5)
    assert code == "123456", code
    assert client._fetch_method == "imap", client._fetch_method


def _test_scope_mismatch_detector():
    exc = _OutlookTokenError(
        status=400,
        error="invalid_grant",
        description=(
            "AADSTS70000: The request was denied because one or more scopes "
            "requested are unauthorized or expired."
        ),
        error_codes=[70000],
        scope="https://graph.microsoft.com/Mail.Read offline_access",
    )
    assert _looks_like_scope_mismatch(exc) is True


async def _main():
    await _test_graph_falls_back_to_imap()
    _test_scope_mismatch_detector()
    print("Outlook Graph fallback tests passed")


if __name__ == "__main__":
    asyncio.run(_main())
