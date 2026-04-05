"""
mail/__init__.py — Factory for all mail service clients.
"""
from __future__ import annotations

from typing import Optional

from src.mail.base import MailClient
from src.mail.gptmail import GPTMailClient
from src.mail.npcmail import NPCMailClient
from src.mail.yydsmail import YYDSMailClient
from src.mail.imap import IMAPMailClient, MultiIMAPMailClient

__all__ = [
    "MailClient", "GPTMailClient", "NPCMailClient",
    "YYDSMailClient", "IMAPMailClient", "MultiIMAPMailClient",
    "get_mail_client",
]


def get_mail_client(provider: str, api_key: str = "", base_url: str = "") -> MailClient:
    """Return the appropriate MailClient for *provider*."""
    match provider.lower():
        case "gptmail":
            return GPTMailClient(
                api_key=api_key or "gpt-test",
                **({"base_url": base_url} if base_url else {}),
            )
        case "npcmail":
            return NPCMailClient(
                api_key=api_key,
                **({"base_url": base_url} if base_url else {}),
            )
        case "yydsmail":
            return YYDSMailClient(
                api_key=api_key,
                **({"base_url": base_url} if base_url else {}),
            )
        case _ if provider.lower() == "imap" or provider.lower().startswith("imap:"):
            # Support "imap" (random from all) or "imap:N" (fixed index N).
            _parts = provider.split(":", 1)
            _index: Optional[int] = None
            if len(_parts) == 2 and _parts[1].isdigit():
                _index = int(_parts[1])

            import src.config as _cfg_mod
            _imap_raw = (_cfg_mod.load().get("mail") or {}).get("imap", [])

            # Backward compat: accept a single dict (old config format).
            if isinstance(_imap_raw, dict):
                _imap_raw = [_imap_raw]

            # Build one IMAPMailClient per configured account, skipping blanks.
            _clients = [
                IMAPMailClient(
                    email     = c.get("email", ""),
                    password  = c.get("password", ""),
                    host      = c.get("host", ""),
                    port      = int(c.get("port", 993)),
                    ssl       = bool(c.get("ssl", True)),
                    folder    = c.get("folder", "INBOX"),
                    use_alias = c.get("use_alias"),  # None → auto-detect by domain
                )
                for c in _imap_raw
                if c.get("email")   # skip placeholder / empty entries
            ]

            if not _clients:
                raise ValueError(
                    "No valid IMAP accounts configured. "
                    "Add at least one entry with a non-empty 'email' under mail.imap."
                )

            # Fixed-index mode: "imap:0", "imap:1", …
            if _index is not None:
                if _index >= len(_clients):
                    raise ValueError(
                        f"mail_provider: imap:{_index} 越界 — "
                        f"共配置了 {len(_clients)} 个账户 (有效索引 0–{len(_clients) - 1})"
                    )
                return _clients[_index]

            # Random / all-accounts mode: "imap"
            return _clients[0] if len(_clients) == 1 else MultiIMAPMailClient(_clients)

        case _:
            raise ValueError(f"Unknown mail provider: {provider!r}")

