"""
mail/__init__.py — Factory for all mail service clients.
"""
from __future__ import annotations

from src.mail.base import MailClient
from src.mail.gptmail import GPTMailClient
from src.mail.npcmail import NPCMailClient
from src.mail.yydsmail import YYDSMailClient

__all__ = ["MailClient", "GPTMailClient", "NPCMailClient", "YYDSMailClient", "get_mail_client"]


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
        case _:
            raise ValueError(f"Unknown mail provider: {provider!r}")


