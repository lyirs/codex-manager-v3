"""
mail/gptmail.py — GPTMail client (https://mail.chatgpt.org.uk)

CLI smoke-test:
    python -m src.mail.gptmail [api_key]
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx
from loguru import logger

from src.mail.base import MailClient

BASE_URL = "https://mail.chatgpt.org.uk"


def _extract_code(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{6})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{4,8})\b", text)
    return m.group(1) if m else None


def _coerce_records(payload) -> list[dict]:
    """
    GPTMail has changed response shapes over time.

    Normalize the common variants into a flat list of dict records:
      - {"data": {"emails": [...]}}
      - {"data": [...]}
      - {"emails": [...]}
      - [...]
      - {"id": ..., ...}
    """
    if payload is None:
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    if "data" in payload:
        return _coerce_records(payload.get("data"))

    if "emails" in payload:
        return _coerce_records(payload.get("emails"))

    if any(k in payload for k in ("id", "subject", "content", "html_content")):
        return [payload]

    return []


def _combined_mail_text(mail: dict) -> str:
    return " ".join(filter(None, [
        str(mail.get("subject", "")),
        str(mail.get("content", "")),
        str(mail.get("html_content", "")),
        str(mail.get("text_content", "")),
        str(mail.get("body", "")),
    ]))


class GPTMailClient(MailClient):
    def __init__(self, api_key: str = "gpt-test", base_url: str = BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── generate ─────────────────────────────────────────────────────────

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        body: dict = {}
        if prefix:
            body["prefix"] = prefix
        if domain:
            body["domain"] = domain

        async with httpx.AsyncClient(timeout=30) as client:
            if body:
                r = await client.post(
                    f"{self._base_url}/api/generate-email",
                    headers=self._headers,
                    json=body,
                )
            else:
                r = await client.get(
                    f"{self._base_url}/api/generate-email",
                    headers=self._headers,
                )
            r.raise_for_status()
            data = r.json()

        email = (
            (data.get("data") or {}).get("email")
            or data.get("email")
        )
        if not email:
            raise ValueError(f"GPTMail: unexpected response: {data}")

        logger.info(f"[GPTMail] Generated: {email}")
        return email

    # ── poll ─────────────────────────────────────────────────────────────

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        deadline = time.monotonic() + timeout
        seen_ids: set[str] = set()

        async with httpx.AsyncClient(timeout=30) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(
                        f"{self._base_url}/api/emails",
                        headers=self._headers,
                        params={"email": email},
                    )
                    r.raise_for_status()
                    payload = r.json()
                    raw_emails = _coerce_records(payload)

                    for mail in raw_emails:
                        mid = str(mail.get("id", ""))
                        if mid in seen_ids:
                            continue
                        seen_ids.add(mid)

                        combined = _combined_mail_text(mail)
                        code = _extract_code(combined)

                        if not code and mid:
                            try:
                                det = await client.get(
                                    f"{self._base_url}/api/email/{mid}",
                                    headers=self._headers,
                                )
                                det.raise_for_status()
                                detail_records = _coerce_records(det.json())
                                det_data = detail_records[0] if detail_records else {}
                                combined2 = _combined_mail_text(det_data)
                                code = _extract_code(combined2)
                            except Exception:
                                pass

                        if code:
                            logger.info(f"[GPTMail] Code {code} for {email}")
                            return code

                except Exception as exc:
                    logger.warning(f"[GPTMail] poll error: {exc}")

                await asyncio.sleep(3)

        logger.warning(f"[GPTMail] Timed out waiting for code ({email})")
        return None


# ── CLI smoke-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        key = sys.argv[1] if len(sys.argv) > 1 else "gpt-test"
        client = GPTMailClient(api_key=key)
        email = await client.generate_email()
        print(f"Generated: {email}")

    asyncio.run(_main())

