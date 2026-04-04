"""
mail/npcmail.py — NPCmail client (https://dash.xphdfs.me)

CLI smoke-test:
    python -m src.mail.npcmail <api_key>
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx
from loguru import logger

from src.mail.base import MailClient

BASE_URL = "https://dash.xphdfs.me"


def _extract_code(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{6})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4,8})\b", text)
    return m.group(1) if m else None


class NPCMailClient(MailClient):
    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise ValueError("NPCMail requires an API key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

    # ── generate ─────────────────────────────────────────────────────────

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        body: dict = {"count": 1, "expiryDays": 30}
        if domain:
            body["domain"] = domain
        if prefix:
            body["prefix"] = prefix

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base_url}/api/public/batch-create-emails",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()

        emails = data.get("emails", [])
        if not emails:
            raise ValueError(f"NPCMail: no emails returned: {data}")

        first = emails[0]
        email = first if isinstance(first, str) else first.get("address", str(first))
        logger.info(f"[NPCMail] Generated: {email}")
        return email

    # ── poll ─────────────────────────────────────────────────────────────

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        deadline = time.monotonic() + timeout

        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.post(
                        f"{self._base_url}/api/public/extract-codes",
                        headers=self._headers,
                        json={"addresses": [email]},
                    )
                    r.raise_for_status()
                    data = r.json()

                    code: Optional[str] = None
                    if isinstance(data, list) and data:
                        code = data[0].get("code")
                    elif isinstance(data, dict):
                        codes = data.get("codes") or data.get("data") or []
                        if isinstance(codes, list) and codes:
                            code = codes[0].get("code")

                    if code:
                        logger.info(f"[NPCMail] Code {code} for {email}")
                        return str(code)

                except Exception as exc:
                    logger.warning(f"[NPCMail] poll error: {exc}")

                await asyncio.sleep(3)

        logger.warning(f"[NPCMail] Timed out waiting for code ({email})")
        return None


# ── CLI smoke-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        if len(sys.argv) < 2:
            print("Usage: python -m src.mail.npcmail <api_key>")
            return
        client = NPCMailClient(api_key=sys.argv[1])
        email = await client.generate_email()
        print(f"Generated: {email}")

    asyncio.run(_main())

