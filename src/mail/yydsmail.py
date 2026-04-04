"""
mail/yydsmail.py — YYDS Mail client (https://maliapi.215.im/v1)

CLI smoke-test:
    python -m src.mail.yydsmail <api_key>
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx
from loguru import logger

from src.mail.base import MailClient

BASE_URL = "https://maliapi.215.im/v1"


def _extract_code(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{6})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4,8})\b", text)
    return m.group(1) if m else None


class YYDSMailClient(MailClient):
    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise ValueError("YYDSMail requires an API key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }
        self._temp_token: Optional[str] = None

    # ── generate ─────────────────────────────────────────────────────────

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        body: dict = {}
        if prefix:
            body["address"] = prefix
        if domain:
            body["domain"] = domain

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self._base_url}/accounts",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            data = r.json()

        inner = data.get("data") or {}
        address = inner.get("address")
        self._temp_token = inner.get("token")   # keep for subsequent calls

        if not address:
            raise ValueError(f"YYDSMail: unexpected response: {data}")

        logger.info(f"[YYDSMail] Generated: {address}")
        return address

    # ── poll ─────────────────────────────────────────────────────────────

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        deadline = time.monotonic() + timeout
        seen_ids: set[str] = set()

        # Prefer temp-token auth when available; fall back to API key
        if self._temp_token:
            auth_headers = {**self._headers, "Authorization": f"Bearer {self._temp_token}"}
        else:
            auth_headers = self._headers

        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(
                        f"{self._base_url}/messages",
                        headers=auth_headers,
                        params={"address": email},
                    )
                    r.raise_for_status()
                    messages = (r.json().get("data") or {}).get("messages", [])

                    for msg in messages:
                        mid = str(msg.get("id", ""))
                        if mid in seen_ids:
                            continue
                        seen_ids.add(mid)

                        try:
                            det = await client.get(
                                f"{self._base_url}/messages/{mid}",
                                headers=auth_headers,
                                params={"address": email},
                            )
                            det.raise_for_status()
                            det_data = det.json().get("data") or {}
                            html_parts = det_data.get("html", [])
                            if isinstance(html_parts, list):
                                html_str = " ".join(html_parts)
                            else:
                                html_str = str(html_parts)
                            combined = " ".join(filter(None, [
                                det_data.get("subject", ""),
                                det_data.get("text", ""),
                                html_str,
                            ]))
                        except Exception:
                            combined = msg.get("subject", "")

                        code = _extract_code(combined)
                        if code:
                            logger.info(f"[YYDSMail] Code {code} for {email}")
                            return code

                except Exception as exc:
                    logger.warning(f"[YYDSMail] poll error: {exc}")

                await asyncio.sleep(3)

        logger.warning(f"[YYDSMail] Timed out waiting for code ({email})")
        return None


# ── CLI smoke-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        if len(sys.argv) < 2:
            print("Usage: python -m src.mail.yydsmail <api_key>")
            return
        client = YYDSMailClient(api_key=sys.argv[1])
        email = await client.generate_email()
        print(f"Generated: {email}")

    asyncio.run(_main())

