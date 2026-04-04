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

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """Return True if the generated email looks usable for registration."""
        if not email or "@" not in email:
            return False
        _, domain = email.rsplit("@", 1)
        domain = domain.lower()
        # Reject reverse-DNS / arpa domains (e.g. 5.2.5.b.0.d.0.0.1.0.a.2.ip6.arpa)
        if domain.endswith(".arpa") or domain.endswith(".arpa."):
            return False
        # Must have at least one dot (TLD)
        if "." not in domain:
            return False
        # TLD must be alphabetic (not all digits)
        tld = domain.rsplit(".", 1)[-1]
        if not tld.isalpha():
            return False
        return True

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
        max_retries: int = 5,
    ) -> str:
        body: dict = {}
        if prefix:
            body["prefix"] = prefix
        if domain:
            body["domain"] = domain

        for attempt in range(1, max_retries + 1):
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

            if self._is_valid_email(email):
                logger.info(f"[GPTMail] Generated: {email}")
                return email

            logger.warning(
                f"[GPTMail] Attempt {attempt}/{max_retries}: invalid domain in "
                f"{email!r} — retrying"
            )

        raise ValueError(f"GPTMail: failed to generate a valid email after {max_retries} attempts")

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

                    raw_emails = (
                        (payload.get("data") or {}).get("emails")
                        or payload.get("data")
                        or payload.get("emails")
                        or []
                    )
                    if isinstance(raw_emails, dict):
                        raw_emails = list(raw_emails.values())

                    for mail in raw_emails:
                        mid = str(mail.get("id", ""))
                        if mid in seen_ids:
                            continue
                        seen_ids.add(mid)

                        combined = " ".join(filter(None, [
                            mail.get("subject", ""),
                            mail.get("content", ""),
                            mail.get("html_content", ""),
                        ]))
                        code = _extract_code(combined)

                        if not code and mid:
                            try:
                                det = await client.get(
                                    f"{self._base_url}/api/email/{mid}",
                                    headers=self._headers,
                                )
                                det.raise_for_status()
                                det_data = det.json().get("data") or {}
                                combined2 = " ".join(filter(None, [
                                    det_data.get("subject", ""),
                                    det_data.get("content", ""),
                                    det_data.get("html_content", ""),
                                ]))
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

