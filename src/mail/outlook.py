"""
mail/outlook.py - Outlook/Hotmail mail client with Microsoft OAuth2.

Supports two fetch methods:
  graph : Microsoft Graph API  (recommended, no IMAP permission needed)
  imap  : IMAP with XOAUTH2   (requires IMAP.AccessAsUser.All scope)

Account config (stored in DB section 'mail.outlook'):
  email          : user@outlook.com / user@hotmail.com / user@live.com
  client_id      : Azure AD application (client) ID
  tenant_id      : 'consumers' (personal accounts, default) or specific tenant GUID
  refresh_token  : OAuth2 refresh token (long-lived)
  access_token   : (auto-managed, can be left empty)
  fetch_method   : 'graph' (default) or 'imap'

Minimal Azure AD app registration requirements:
  - Redirect URI: https://login.microsoftonline.com/common/oauth2/nativeclient
  - Delegated permissions (Graph):   Mail.Read, offline_access
  - Delegated permissions (IMAP):    IMAP.AccessAsUser.All, offline_access
  - "Allow public client flows": enabled

Obtaining a refresh_token (one-time, per account):
  Use the device code flow or any OAuth2 tool with the scopes above.
"""
from __future__ import annotations

import asyncio
import email as email_lib
import re
import time
from email.header import decode_header, make_header
from typing import Optional

import httpx
from loguru import logger

from src.mail.base import MailClient

# -- Constants -------------------------------------------------------------

_CODE_RE = re.compile(r"\b(\d{6})\b")
_CODE_FALLBACK_RE = re.compile(r"\b(\d{4,8})\b")

_GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
_GRAPH_JUNK_URL = "https://graph.microsoft.com/v1.0/me/mailFolders/junkemail/messages"
_IMAP_HOST = "outlook.live.com"
_IMAP_PORT = 993
_IMAP_FOLDERS = ["INBOX", "Junk"]

_SCOPE_GRAPH = "https://graph.microsoft.com/Mail.Read offline_access"
_SCOPE_IMAP = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"


# -- Helpers ---------------------------------------------------------------

class _OutlookTokenError(RuntimeError):
    def __init__(
        self,
        *,
        status: int,
        error: str = "",
        description: str = "",
        error_codes: Optional[list[int]] = None,
        scope: str = "",
    ) -> None:
        self.status = status
        self.error = error
        self.description = description
        self.error_codes = error_codes or []
        self.scope = scope
        msg = f"{status} {error or 'token_error'}"
        if description:
            msg = f"{msg}: {description}"
        super().__init__(msg)


class _OutlookGraphScopeUnavailable(RuntimeError):
    """Graph token refresh failed because the refresh_token lacks Graph scope."""


def _looks_like_scope_mismatch(exc: _OutlookTokenError) -> bool:
    text = f"{exc.error} {exc.description}".lower()
    if 70000 in exc.error_codes:
        return True
    return (
        "requested are unauthorized" in text
        or "unauthorized or expired" in text
        or "grant the client application access to the requested scope" in text
    )


def _extract_code(text: str) -> Optional[str]:
    m = _CODE_RE.search(text)
    if m:
        return m.group(1)
    m = _CODE_FALLBACK_RE.search(text)
    return m.group(1) if m else None


def _decode_str(raw) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return str(raw or "")


def _extract_text(msg: email_lib.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass
    return " ".join(parts)


def _make_xoauth2_token(email: str, access_token: str) -> str:
    """Build base64-encoded XOAUTH2 SASL string (kept for external use / tests)."""
    import base64

    raw = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode()).decode()


# -- Single Outlook account client ----------------------------------------

class OutlookMailClient(MailClient):
    """
    Single Outlook/Hotmail account using Microsoft OAuth2.

    Token lifecycle is managed internally: the access_token is refreshed
    automatically before it expires using the stored refresh_token.

    proxy : optional HTTP/SOCKS proxy URL, e.g. "http://127.0.0.1:10808"
            When set, all httpx calls (token refresh + Graph API) will route
            through this proxy.  Required in regions where Microsoft endpoints
            are blocked (e.g. mainland China).
    """

    def __init__(
        self,
        email: str,
        client_id: str,
        tenant_id: str = "consumers",
        refresh_token: str = "",
        access_token: str = "",
        fetch_method: str = "graph",
        proxy: Optional[str] = None,
    ) -> None:
        self._email = email
        self._client_id = client_id
        self._tenant_id = tenant_id or "consumers"
        self._refresh_token = refresh_token
        self._access_token = access_token
        self._fetch_method = fetch_method
        self._proxy = proxy
        self._token_expiry = 0.0
        self._seen_imap_uids: set[str] = set()
        self._seen_graph_ids: set[str] = set()

    # -- httpx factory ----------------------------------------------------

    def _httpx_client(self, **extra) -> httpx.AsyncClient:
        """
        Return a configured httpx.AsyncClient.

        httpx >= 0.27 accepts ``proxy`` (singular URL string).
        If self._proxy is set the client routes all traffic through it.
        trust_env=False prevents picking up Windows system proxy settings
        (which can conflict with our explicit proxy).
        """
        kw: dict = {"timeout": 30, "trust_env": False, **extra}
        if self._proxy:
            kw["proxy"] = self._proxy
        return httpx.AsyncClient(**kw)

    # -- Token management -------------------------------------------------

    def _refresh_token_sync(self) -> dict:
        """
        Synchronous token refresh using stdlib urllib.request.

        We intentionally do NOT route through self._proxy here.
        Diagnosis shows that login.microsoftonline.com is reachable directly,
        but going through the HTTP proxy can cause TLS handshake failures.
        The proxy is only used for the IMAP tunnel where it works via CONNECT.

        Fallback: if direct attempt fails, retry once through the configured
        proxy.
        """
        import json as _json
        import ssl as _ssl
        import urllib.error as _urlerr
        import urllib.parse as _urlparse
        import urllib.request as _urlreq

        scope = _SCOPE_GRAPH if self._fetch_method == "graph" else _SCOPE_IMAP
        token_url = (
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        )
        payload = _urlparse.urlencode(
            {
                "client_id": self._client_id,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "scope": scope,
            }
        ).encode()

        req = _urlreq.Request(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        def _fetch(opener) -> dict:
            try:
                with opener.open(req, timeout=25) as resp:
                    data = _json.loads(resp.read())
            except _urlerr.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    body = _json.loads(raw)
                except Exception:
                    body = {}
                raise _OutlookTokenError(
                    status=exc.code,
                    error=body.get("error", ""),
                    description=body.get("error_description", raw),
                    error_codes=body.get("error_codes") or [],
                    scope=scope,
                ) from exc
            if "access_token" not in data:
                raise RuntimeError(
                    f"Token endpoint returned no access_token: {data}"
                )
            return data

        direct_exc_repr = ""
        try:
            no_proxy_opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))
            return _fetch(no_proxy_opener)
        except _OutlookTokenError:
            raise
        except Exception as direct_exc:
            direct_exc_repr = repr(direct_exc)
            logger.debug(
                f"[Outlook] Direct token fetch failed ({type(direct_exc).__name__}: "
                f"{direct_exc!r}), retrying via proxy..."
            )

        if self._proxy:
            ssl_ctx = _ssl.create_default_context()
            opener = _urlreq.build_opener(
                _urlreq.ProxyHandler({"http": self._proxy, "https": self._proxy}),
                _urlreq.HTTPSHandler(context=ssl_ctx),
            )
            return _fetch(opener)

        raise RuntimeError(
            f"[Outlook] Token refresh failed for {self._email} "
            f"(direct error: {direct_exc_repr}; no proxy configured)"
        )

    async def _get_token(self) -> str:
        """Return a valid access_token, refreshing if needed."""
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        if not self._refresh_token:
            raise ValueError(
                f"[Outlook] No refresh_token configured for {self._email}. "
                "Complete the OAuth2 device-code flow first."
            )

        data = await asyncio.to_thread(self._refresh_token_sync)

        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        if "refresh_token" in data:
            self._refresh_token = data["refresh_token"]

        logger.debug(f"[Outlook] Token refreshed for {self._email}")
        return self._access_token

    # -- generate ---------------------------------------------------------

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        """Return the Outlook address directly (no alias support needed)."""
        if prefix or domain:
            logger.info(
                "[Outlook] Registration prefix/domain is ignored in fixed-address mode; "
                f"using {self._email}"
            )
        logger.info(f"[Outlook] Using account: {self._email}")
        return self._email

    # -- poll -------------------------------------------------------------

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        if self._fetch_method == "imap":
            return await self._poll_imap(timeout)
        try:
            return await self._poll_graph(timeout)
        except _OutlookGraphScopeUnavailable as exc:
            logger.warning(
                f"[Outlook] Graph scope unavailable for {self._email}; "
                f"falling back to IMAP ({exc})"
            )
            self._fetch_method = "imap"
            self._access_token = ""
            self._token_expiry = 0.0
            return await self._poll_imap(timeout)

    # -- Graph API fetch --------------------------------------------------

    async def _poll_graph(self, timeout: int) -> Optional[str]:
        import json as _json
        import urllib.request as _urlreq

        deadline = time.monotonic() + timeout
        seen_ids = self._seen_graph_ids

        _GRAPH_FOLDERS = [
            (_GRAPH_MESSAGES_URL, "inbox"),
            (_GRAPH_JUNK_URL, "junk"),
        ]
        _PARAMS = (
            "$select=id,subject,body,receivedDateTime"
            "&$filter=isRead eq false"
            "&$orderby=receivedDateTime desc"
            "&$top=25"
        )

        logger.info(
            f"[Outlook/Graph] Polling inbox+junk for {self._email} (timeout={timeout}s)"
        )

        def _sync_graph_fetch(access_token: str) -> Optional[str]:
            """
            Synchronous Graph API fetch using urllib.
            Always connects directly and bypasses system proxy settings.
            """
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))

            for url, folder_label in _GRAPH_FOLDERS:
                full_url = f"{url}?{_PARAMS}"
                req = _urlreq.Request(full_url, headers=headers)
                try:
                    with opener.open(req, timeout=20) as resp:
                        data = _json.loads(resp.read())
                    messages = data.get("value", [])
                except Exception as exc:
                    logger.warning(f"[Outlook/Graph] {folder_label} error: {exc!r}")
                    continue

                for msg in messages:
                    mid = msg.get("id", "")
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    subject = msg.get("subject", "")
                    body = (msg.get("body") or {}).get("content", "")
                    code = _extract_code(f"{subject} {body}")
                    if code:
                        logger.info(
                            f"[Outlook/Graph] Code {code} for {self._email}"
                            f" (folder={folder_label})"
                        )
                        return code
            return None

        while time.monotonic() < deadline:
            try:
                token = await self._get_token()
                code = await asyncio.to_thread(_sync_graph_fetch, token)
                if code:
                    return code
            except _OutlookTokenError as exc:
                if _looks_like_scope_mismatch(exc):
                    raise _OutlookGraphScopeUnavailable(str(exc)) from exc
                logger.warning(
                    f"[Outlook/Graph] token error [{exc.status} {exc.error}]: "
                    f"{exc.description}"
                )
            except Exception as exc:
                logger.warning(f"[Outlook/Graph] error [{type(exc).__name__}]: {exc!r}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(4, remaining))

        logger.warning(f"[Outlook/Graph] Timed out ({self._email})")
        return None

    # -- IMAP+XOAUTH2 fetch -----------------------------------------------

    async def _poll_imap(self, timeout: int) -> Optional[str]:
        """Route to proxy-aware IMAP implementation when a proxy is configured."""
        if self._proxy:
            return await self._poll_imap_via_proxy(timeout)
        return await self._poll_imap_direct(timeout)

    async def _poll_imap_via_proxy(self, timeout: int) -> Optional[str]:
        """
        Poll IMAP through an HTTP CONNECT proxy tunnel.

        Each IMAP session runs entirely inside asyncio.to_thread() so the event
        loop is never blocked. Token refresh happens in the event loop first.
        """
        import base64 as _b64
        import imaplib as _imaplib
        import socket as _socket
        import ssl as _ssl
        from urllib.parse import urlparse

        p = urlparse(self._proxy)
        proxy_host = p.hostname or "127.0.0.1"
        proxy_port = p.port or 8080

        deadline = time.monotonic() + timeout
        seen_uids = self._seen_imap_uids

        logger.info(
            f"[Outlook/IMAP-proxy] Polling {self._email} "
            f"via HTTP CONNECT {proxy_host}:{proxy_port} (timeout={timeout}s)"
        )

        def _sync_fetch(access_token: str) -> Optional[str]:
            raw = _socket.create_connection((proxy_host, proxy_port), timeout=15)
            raw.settimeout(30)

            connect_req = (
                f"CONNECT {_IMAP_HOST}:{_IMAP_PORT} HTTP/1.1\r\n"
                f"Host: {_IMAP_HOST}:{_IMAP_PORT}\r\n"
            )
            if p.username and p.password:
                cred = _b64.b64encode(f"{p.username}:{p.password}".encode()).decode()
                connect_req += f"Proxy-Authorization: Basic {cred}\r\n"
            connect_req += "\r\n"
            raw.sendall(connect_req.encode())

            resp_buf = b""
            while b"\r\n\r\n" not in resp_buf:
                chunk = raw.recv(4096)
                if not chunk:
                    raise ConnectionError("Proxy closed during CONNECT handshake")
                resp_buf += chunk

            status_line = resp_buf.split(b"\r\n")[0]
            if b"200" not in status_line:
                raise ConnectionError(
                    f"HTTP CONNECT rejected: {status_line.decode(errors='replace')}"
                )

            ssl_ctx = _ssl.create_default_context()
            ssl_sock = ssl_ctx.wrap_socket(raw, server_hostname=_IMAP_HOST)
            ssl_sock.settimeout(30)

            _the_sock = ssl_sock

            class _PatchedIMAP4(_imaplib.IMAP4):
                def _create_socket(self, timeout=None):  # noqa: ARG002
                    return _the_sock

            M = _PatchedIMAP4(_IMAP_HOST)

            try:
                auth_bytes = (
                    f"user={self._email}\x01auth=Bearer {access_token}\x01\x01"
                ).encode("utf-8")
                typ, resp = M.authenticate("XOAUTH2", lambda _: auth_bytes)
                if typ != "OK":
                    logger.warning(
                        f"[Outlook/IMAP-proxy] Auth failed for {self._email}: {resp}"
                    )
                    return None

                for folder_name in _IMAP_FOLDERS:
                    try:
                        typ, _ = M.select(f'"{folder_name}"', readonly=True)
                        if typ != "OK":
                            continue

                        typ, data = M.search(None, "ALL")
                        if typ != "OK" or not data or not data[0]:
                            continue

                        uid_list = data[0].decode().split()
                        for uid in reversed(uid_list):
                            uid_key = f"{folder_name}/{uid}"
                            if uid_key in seen_uids:
                                continue
                            seen_uids.add(uid_key)

                            typ2, msg_data = M.fetch(uid, "(RFC822)")
                            if typ2 != "OK" or not msg_data:
                                continue

                            raw_bytes: Optional[bytes] = None
                            for part in msg_data:
                                if isinstance(part, tuple) and len(part) >= 2:
                                    raw_bytes = part[1]
                                    break
                            if not raw_bytes:
                                continue

                            msg = email_lib.message_from_bytes(raw_bytes)
                            subject = _decode_str(msg.get("Subject", ""))
                            body = _extract_text(msg)
                            code = _extract_code(f"{subject} {body}")
                            if code:
                                logger.info(
                                    f"[Outlook/IMAP-proxy] Code {code} for {self._email}"
                                    f" (folder={folder_name})"
                                )
                                return code

                    except Exception as exc:
                        logger.warning(
                            f"[Outlook/IMAP-proxy] folder {folder_name}: {exc!r}"
                        )

            finally:
                try:
                    M.logout()
                except Exception:
                    pass

            return None

        while time.monotonic() < deadline:
            try:
                token = await self._get_token()
                code = await asyncio.to_thread(_sync_fetch, token)
                if code:
                    return code

            except asyncio.TimeoutError:
                logger.warning("[Outlook/IMAP-proxy] Operation timed out - retrying")
            except OSError as exc:
                logger.warning(
                    f"[Outlook/IMAP-proxy] Network error [{type(exc).__name__}]: {exc!r}"
                )
            except Exception as exc:
                logger.warning(
                    f"[Outlook/IMAP-proxy] Error [{type(exc).__name__}]: {exc!r}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(4, remaining))

        logger.warning(f"[Outlook/IMAP-proxy] Timed out ({self._email})")
        return None

    async def _poll_imap_direct(self, timeout: int) -> Optional[str]:
        """
        Direct IMAP polling (no proxy) using stdlib imaplib + asyncio.to_thread.
        """
        import imaplib as _imaplib

        deadline = time.monotonic() + timeout
        seen_uids = self._seen_imap_uids

        logger.info(f"[Outlook/IMAP] Polling {self._email} (timeout={timeout}s)")

        def _sync_fetch(access_token: str) -> Optional[str]:
            imap_client = _imaplib.IMAP4_SSL(_IMAP_HOST, _IMAP_PORT)
            try:
                auth_string = (
                    f"user={self._email}\x01auth=Bearer {access_token}\x01\x01"
                ).encode("utf-8")
                typ, _ = imap_client.authenticate("XOAUTH2", lambda x: auth_string)
                if typ != "OK":
                    logger.warning(f"[Outlook/IMAP] Auth failed for {self._email}")
                    return None

                for folder_name in _IMAP_FOLDERS:
                    try:
                        typ, _ = imap_client.select(f'"{folder_name}"', readonly=True)
                        if typ != "OK":
                            continue

                        typ, data = imap_client.search(None, "ALL")
                        if typ != "OK" or not data or not data[0]:
                            continue

                        uid_list = data[0].decode().split()
                        for uid in reversed(uid_list):
                            uid_key = f"{folder_name}/{uid}"
                            if uid_key in seen_uids:
                                continue
                            seen_uids.add(uid_key)

                            typ2, msg_data = imap_client.fetch(uid, "(RFC822)")
                            if typ2 != "OK" or not msg_data:
                                continue

                            raw_bytes: Optional[bytes] = None
                            for part in msg_data:
                                if isinstance(part, tuple) and len(part) >= 2:
                                    raw_bytes = part[1]
                                    break
                            if not raw_bytes:
                                continue

                            msg = email_lib.message_from_bytes(raw_bytes)
                            subject = _decode_str(msg.get("Subject", ""))
                            body = _extract_text(msg)
                            code = _extract_code(f"{subject} {body}")
                            if code:
                                logger.info(
                                    f"[Outlook/IMAP] Code {code} for {self._email}"
                                    f" (folder={folder_name})"
                                )
                                return code

                    except Exception as exc:
                        logger.warning(f"[Outlook/IMAP] folder {folder_name}: {exc!r}")

            finally:
                try:
                    imap_client.logout()
                except Exception:
                    pass

            return None

        while time.monotonic() < deadline:
            try:
                token = await self._get_token()
                code = await asyncio.to_thread(_sync_fetch, token)
                if code:
                    return code

            except asyncio.TimeoutError:
                logger.warning("[Outlook/IMAP] Timeout - retrying")
            except OSError as exc:
                logger.warning(
                    f"[Outlook/IMAP] Network error [{type(exc).__name__}]: {exc!r}"
                )
            except Exception as exc:
                logger.warning(
                    f"[Outlook/IMAP] Error [{type(exc).__name__}]: {exc!r}"
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(4, remaining))

        logger.warning(f"[Outlook/IMAP] Timed out ({self._email})")
        return None


# -- Multi-account wrapper ------------------------------------------------

class MultiOutlookMailClient(MailClient):
    """
    Wraps multiple OutlookMailClient instances and round-robins across them.
    Each generate_email() call picks the next unused account; poll_code()
    routes to the owning account.
    """

    def __init__(self, clients: list[OutlookMailClient]) -> None:
        if not clients:
            raise ValueError("MultiOutlookMailClient requires at least one account")
        self._clients = clients
        self._index = 0
        self._routing: dict[str, OutlookMailClient] = {}

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        client = self._clients[self._index % len(self._clients)]
        self._index += 1
        addr = await client.generate_email()
        self._routing[addr.lower()] = client
        return addr

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        client = self._routing.get(email.lower())
        if client is None:
            logger.warning(f"[Outlook] No routing for {email!r} - using first account")
            client = self._clients[0]
        return await client.poll_code(email, timeout)
