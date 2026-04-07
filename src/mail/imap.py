"""
mail/imap.py — Generic IMAP mailbox client (supports multiple accounts, alias mode, OAuth2).

Unlike the API-based providers (gptmail / yydsmail), this client connects
directly to any standard IMAP server using the account's own credentials.

**Alias mode** (auto-enabled for gmail.com mailboxes):
    generate_email() returns ``local+{random8}@domain`` instead of the bare
    address.  All aliases land in the same inbox; poll_code() filters by the
    ``To:`` / ``Delivered-To:`` headers so concurrent registrations sharing one
    mailbox receive the right code.

Configuration in SQLite settings:
    mail_provider: imap
    mail:
      imap:
        - email:    user@gmail.com
          password: app-password        # Gmail: use app-specific password
          host:     imap.gmail.com
          port:     993                 # 993 = IMAPS (SSL), 143 = STARTTLS
          ssl:      true
          folder:   INBOX
          # use_alias: true            # override auto-detect (omit → auto)
        - email:    user2@qq.com
          password: qq-app-password
          host:     imap.qq.com
          port:     993
          ssl:      true
          folder:   INBOX

CLI smoke-test:
    python -m src.mail.imap
"""
from __future__ import annotations

import asyncio
import base64
import email as email_lib
import random
import re
import string
import time
from email.header import decode_header, make_header
from typing import Optional
from urllib.parse import urlparse

import aioimaplib
from loguru import logger

from src.mail.base import MailClient

# ── Constants ─────────────────────────────────────────────────────────────

_ALIAS_DOMAINS: frozenset[str] = frozenset({"gmail.com"})
_ALIAS_BASE_FALLBACK_DOMAINS: frozenset[str] = frozenset({"qq.com", "foxmail.com"})

# Well-known IMAP hosts auto-detected from email domain.
_AUTO_HOSTS: dict[str, str] = {
    "gmail.com":    "imap.gmail.com",
    "qq.com":       "imap.qq.com",
    "foxmail.com":  "imap.qq.com",
    "163.com":      "imap.163.com",
    "126.com":      "imap.126.com",
    "yeah.net":     "imap.yeah.net",
    "hotmail.com":  "imap-mail.outlook.com",
    "outlook.com":  "imap-mail.outlook.com",
    "live.com":     "imap-mail.outlook.com",
    "msn.com":      "imap-mail.outlook.com",
}

_CODE_RE          = re.compile(r"\b(\d{6})\b")
_CODE_FALLBACK_RE = re.compile(r"\b(\d{4,8})\b")


# ── Helpers ───────────────────────────────────────────────────────────────

def _extract_code(text: str) -> Optional[str]:
    """Return the first 6-digit (or 4–8 digit fallback) numeric code in *text*."""
    m = _CODE_RE.search(text)
    if m:
        return m.group(1)
    m = _CODE_FALLBACK_RE.search(text)
    return m.group(1) if m else None


def _decode_str(raw) -> str:
    """Decode an RFC-2047 encoded email header value to a plain string."""
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return str(raw or "")


def _extract_text(msg: email_lib.message.Message) -> str:
    """Walk a parsed email message and concatenate all text parts."""
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


def _recipient_headers(msg: email_lib.message.Message) -> list[str]:
    """Return lower-cased recipient headers used for alias matching."""
    values: list[str] = []
    for name in ("To", "Delivered-To", "X-Original-To"):
        value = _decode_str(msg.get(name, "")).strip().lower()
        if value:
            values.append(value)
    return values


def _looks_like_openai_mail(msg: email_lib.message.Message, combined: str) -> bool:
    """
    Heuristic used only for alias fallback.

    Some providers rewrite ``+alias`` recipients back to the base mailbox.
    When that happens, only accept the message as a fallback if it still looks
    like an OpenAI / ChatGPT verification email.
    """
    sender = _decode_str(msg.get("From", "")).lower()
    text = combined.lower()
    keywords = (
        "openai",
        "chatgpt",
        "verify your email",
        "verification code",
        "email verification",
    )
    return any(k in sender for k in ("openai", "tm.openai.com", "chatgpt")) or any(
        k in text for k in keywords
    )


def _extract_code_from_message(
    msg: email_lib.message.Message,
    *,
    filter_to: Optional[str],
    mailbox_email: str,
    allow_base_fallback: bool,
    uid: str,
    log_prefix: str,
    initial_snapshot: bool = False,
) -> Optional[str]:
    """
    Parse a fetched message and return a matching OTP code if present.

    On the very first mailbox snapshot we still want to consider a freshly
    delivered alias mail that arrived before the first IMAP SEARCH completed.
    """
    subject  = _decode_str(msg.get("Subject", ""))
    body     = _extract_text(msg)
    combined = f"{subject} {body}"
    recipient_headers = _recipient_headers(msg)

    if filter_to is not None:
        alias_exact_match = any(
            filter_to in hdr for hdr in recipient_headers
        )
        base_match = any(
            mailbox_email in hdr for hdr in recipient_headers
        )
        fallback_match = (
            allow_base_fallback
            and base_match
            and _looks_like_openai_mail(msg, combined)
        )
        if not alias_exact_match and fallback_match:
            logger.warning(
                f"[{log_prefix}] uid={uid} matched base mailbox "
                f"{mailbox_email!r} instead of alias {filter_to!r}; "
                "accepting provider fallback"
            )
        if not alias_exact_match and not fallback_match:
            hdr_preview = " | ".join(recipient_headers)[:120]
            logger.debug(
                f"[{log_prefix}] uid={uid} skipped - recipient headers "
                f"{hdr_preview!r} don't match {filter_to!r}"
            )
            return None
    elif initial_snapshot:
        # Without an alias-specific target, treating already-present messages as
        # fresh is too risky because a stale OTP from an earlier attempt could
        # be reused accidentally.
        return None

    logger.debug(f"[{log_prefix}] uid={uid} subject={subject[:60]!r}")
    code = _extract_code(combined)
    if code:
        suffix = " during initial snapshot" if initial_snapshot else ""
        logger.info(f"[{log_prefix}] Code {code} found in uid={uid}{suffix}")
        return code
    return None


def _random_alias(length: int = 8) -> str:
    """Return a random alphanumeric string for use as a '+alias' suffix."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _make_xoauth2_token(email: str, access_token: str) -> str:
    """Build the base64-encoded XOAUTH2 SASL token for IMAP authentication."""
    raw = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode()).decode()


def _response_ok(resp) -> bool:
    """Return True when an IMAP command response indicates success."""
    if resp is None:
        return False
    result = getattr(resp, "result", None)
    if result is not None:
        return result == "OK"
    try:
        return resp[0] == "OK"
    except Exception:
        return False


# ── Single-account IMAP client ────────────────────────────────────────────

class IMAPMailClient(MailClient):
    """
    Generic IMAP client that wraps a single existing mailbox.

    Parameters
    ----------
    email        : Full e-mail address used for login.
    password     : IMAP password (ignored when auth_type='oauth2').
    host         : IMAP server. Auto-detected from domain when empty.
    port         : 993 = IMAPS (SSL), 143 = STARTTLS.
    ssl          : True → IMAPS; False → plain/STARTTLS.
    folder       : Mailbox folder (default 'INBOX').
    use_alias    : None = auto-detect (gmail.com), True/False = override.
    auth_type    : 'password' (default) or 'oauth2' (XOAUTH2).
    access_token : Bearer token required when auth_type='oauth2'.
    proxy        : Optional HTTP proxy URL used for IMAP CONNECT tunneling.
    """

    def __init__(
        self,
        email: str,
        password: str = "",
        host: str = "",
        port: int = 993,
        ssl: bool = True,
        folder: str = "INBOX",
        use_alias: Optional[bool] = None,
        auth_type: str = "password",
        access_token: str = "",
        proxy: Optional[str] = None,
    ) -> None:
        self._email        = email
        self._password     = password
        self._host         = host or _AUTO_HOSTS.get(email.split("@")[-1].lower() if "@" in email else "", "")
        self._port         = port
        self._ssl          = ssl
        self._folder       = folder
        self._auth_type    = auth_type
        self._access_token = access_token
        self._proxy        = proxy

        if use_alias is None:
            domain    = email.split("@")[-1].lower() if "@" in email else ""
            use_alias = domain in _ALIAS_DOMAINS
        self._use_alias: bool = use_alias

    # ── generate ─────────────────────────────────────────────────────────

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        """
        Return a usable registration address.

        * **Alias mode** (gmail.com or ``use_alias: true``):
          Returns ``local+{random8}@domain``.  The inbox still receives all
          messages sent to any ``+alias`` variant.
        * **Standard mode**: Returns the configured address as-is.
        """
        if self._use_alias:
            local, _, dom = self._email.partition("@")
            # Strip any pre-existing alias suffix before adding a new one.
            local = local.split("+")[0]
            alias_email = f"{local}+{_random_alias()}@{dom}"
            logger.info(f"[IMAP] Alias mode — using {alias_email} (inbox: {self._email})")
            return alias_email

        logger.info(f"[IMAP] Using fixed mailbox: {self._email}")
        return self._email

    # ── poll ─────────────────────────────────────────────────────────────

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        """
        Connect to the IMAP server, repeatedly search for unseen messages
        and return the first OTP code found in subject + body text.

        When *email* differs from the configured mailbox address (i.e., an
        alias was used), only messages whose ``To:`` / ``Delivered-To:``
        header contains *email* are considered — this prevents concurrent
        tasks sharing the same inbox from stealing each other's codes.
        """
        if self._proxy:
            return await self._poll_code_via_proxy(email, timeout)

        deadline      = time.monotonic() + timeout
        known_uids: set[str] = set()
        poll_interval = 4   # seconds between IMAP searches

        # If an alias was used, filter incoming messages by To: header.
        mailbox_email = self._email.lower()
        filter_to: Optional[str] = (
            email.lower()
            if email.lower() != mailbox_email
            else None
        )
        domain = mailbox_email.split("@")[-1] if "@" in mailbox_email else ""
        allow_base_fallback = (
            filter_to is not None and domain in _ALIAS_BASE_FALLBACK_DOMAINS
        )

        logger.info(
            f"[IMAP] Polling {self._folder} on {self._host}:{self._port} "
            f"for {email} (timeout={timeout}s, alias_filter={filter_to is not None})"
        )

        while time.monotonic() < deadline:
            imap = None
            try:
                # ── Connect & authenticate ────────────────────────────────
                if self._ssl:
                    imap = aioimaplib.IMAP4_SSL(
                        host=self._host, port=self._port,
                        timeout=15,
                    )
                else:
                    imap = aioimaplib.IMAP4(
                        host=self._host, port=self._port,
                        timeout=15,
                    )

                await imap.wait_hello_from_server()
                if self._auth_type == "oauth2":
                    if not self._access_token:
                        logger.warning("[IMAP] OAuth2 selected but access_token is empty")
                        await asyncio.sleep(poll_interval)
                        continue
                    if hasattr(imap, "xoauth2"):
                        login_resp = await imap.xoauth2(self._email, self._access_token)
                    else:
                        token = _make_xoauth2_token(self._email, self._access_token)
                        login_resp = await imap.authenticate("XOAUTH2", lambda x: token.encode())
                else:
                    login_resp = await imap.login(self._email, self._password)
                if not _response_ok(login_resp):
                    logger.warning(f"[IMAP] Login failed: {login_resp}")
                    await asyncio.sleep(poll_interval)
                    continue

                select_resp = await imap.select(self._folder)
                if not _response_ok(select_resp):
                    logger.warning(f"[IMAP] SELECT {self._folder} failed: {select_resp}")
                    await asyncio.sleep(poll_interval)
                    continue

                # ── Search for all UNSEEN messages ────────────────────────
                search_resp = await imap.search("ALL")
                if not _response_ok(search_resp):
                    await asyncio.sleep(poll_interval)
                    continue
                _, data = search_resp

                uid_list: list[str] = []
                if data and data[0]:
                    raw = data[0]
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    uid_list = [u for u in raw.split() if u]

                # Prime the current mailbox snapshot, then only inspect mail
                # that appeared after polling started.
                if not known_uids:
                    initial_uids = list(reversed(uid_list[-12:]))
                    for uid in initial_uids:
                        fetch_resp = await imap.fetch(uid, "(RFC822)")
                        if not _response_ok(fetch_resp):
                            continue
                        _, msg_data = fetch_resp

                        raw_bytes: Optional[bytes] = None
                        for part in msg_data:
                            if isinstance(part, bytes) and len(part) > 100:
                                raw_bytes = part
                                break

                        if not raw_bytes:
                            continue

                        msg = email_lib.message_from_bytes(raw_bytes)
                        code = _extract_code_from_message(
                            msg,
                            filter_to=filter_to,
                            mailbox_email=mailbox_email,
                            allow_base_fallback=allow_base_fallback,
                            uid=uid,
                            log_prefix="IMAP",
                            initial_snapshot=True,
                        )
                        if code:
                            await imap.logout()
                            return code

                    known_uids.update(uid_list)
                    await asyncio.sleep(
                        min(poll_interval, max(0, deadline - time.monotonic()))
                    )
                    continue

                new_uids = [u for u in uid_list if u not in known_uids]
                known_uids.update(uid_list)

                for uid in reversed(new_uids):
                    fetch_resp = await imap.fetch(uid, "(RFC822)")
                    if not _response_ok(fetch_resp):
                        continue
                    _, msg_data = fetch_resp

                    # aioimaplib returns a list; find the raw bytes entry
                    raw_bytes: Optional[bytes] = None
                    for part in msg_data:
                        if isinstance(part, bytes) and len(part) > 100:
                            raw_bytes = part
                            break

                    if not raw_bytes:
                        continue

                    msg = email_lib.message_from_bytes(raw_bytes)
                    code = _extract_code_from_message(
                        msg,
                        filter_to=filter_to,
                        mailbox_email=mailbox_email,
                        allow_base_fallback=allow_base_fallback,
                        uid=uid,
                        log_prefix="IMAP",
                    )
                    if code:
                        await imap.logout()
                        return code

            except asyncio.TimeoutError:
                logger.warning("[IMAP] Connection timed out — retrying")
            except OSError as exc:
                logger.warning(f"[IMAP] Network error: {exc}")
            except Exception as exc:
                logger.warning(f"[IMAP] Unexpected error: {exc}")
            finally:
                if imap is not None:
                    try:
                        await imap.logout()
                    except Exception:
                        pass

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        logger.warning(f"[IMAP] Timed out waiting for code ({email})")
        return None

    async def _poll_code_via_proxy(self, email: str, timeout: int = 120) -> Optional[str]:
        """
        Poll IMAP through an HTTP CONNECT proxy.

        This path is mainly for providers such as Gmail where direct IMAP
        access can be blocked by the local network environment.
        """
        import imaplib as _imaplib
        import socket as _socket
        import ssl as _ssl

        if not self._proxy:
            return None

        proxy_url = urlparse(self._proxy)
        if proxy_url.scheme and proxy_url.scheme.lower().startswith("socks"):
            logger.warning(f"[IMAP/proxy] Unsupported proxy scheme: {proxy_url.scheme}")
            return None

        proxy_host = proxy_url.hostname
        proxy_port = proxy_url.port or 8080
        if not proxy_host:
            logger.warning(f"[IMAP/proxy] Invalid proxy URL: {self._proxy!r}")
            return None

        deadline      = time.monotonic() + timeout
        known_uids: set[str] = set()
        poll_interval = 4

        mailbox_email = self._email.lower()
        filter_to: Optional[str] = (
            email.lower()
            if email.lower() != mailbox_email
            else None
        )
        domain = mailbox_email.split("@")[-1] if "@" in mailbox_email else ""
        allow_base_fallback = (
            filter_to is not None and domain in _ALIAS_BASE_FALLBACK_DOMAINS
        )

        logger.info(
            f"[IMAP/proxy] Polling {self._folder} on {self._host}:{self._port} "
            f"via HTTP CONNECT {proxy_host}:{proxy_port} for {email} "
            f"(timeout={timeout}s, alias_filter={filter_to is not None})"
        )

        def _sync_fetch() -> Optional[str]:
            raw = _socket.create_connection((proxy_host, proxy_port), timeout=15)
            raw.settimeout(30)

            connect_req = (
                f"CONNECT {self._host}:{self._port} HTTP/1.1\r\n"
                f"Host: {self._host}:{self._port}\r\n"
            )
            if proxy_url.username and proxy_url.password:
                cred = base64.b64encode(
                    f"{proxy_url.username}:{proxy_url.password}".encode()
                ).decode()
                connect_req += f"Proxy-Authorization: Basic {cred}\r\n"
            connect_req += "\r\n"
            raw.sendall(connect_req.encode())

            resp_buf = b""
            while b"\r\n\r\n" not in resp_buf:
                chunk = raw.recv(4096)
                if not chunk:
                    raise ConnectionError("Proxy closed during CONNECT handshake")
                resp_buf += chunk

            status_line = resp_buf.split(b"\r\n", 1)[0]
            if b"200" not in status_line:
                raise ConnectionError(
                    f"HTTP CONNECT rejected: {status_line.decode(errors='replace')}"
                )

            if self._ssl:
                ssl_ctx = _ssl.create_default_context()
                tunnel_sock = ssl_ctx.wrap_socket(raw, server_hostname=self._host)
                tunnel_sock.settimeout(30)
            else:
                tunnel_sock = raw

            _the_sock = tunnel_sock

            class _PatchedIMAP4(_imaplib.IMAP4):
                def _create_socket(self, timeout=None):   # noqa: ARG002
                    return _the_sock

            M = _PatchedIMAP4(self._host)
            try:
                if self._auth_type == "oauth2":
                    if not self._access_token:
                        logger.warning("[IMAP/proxy] OAuth2 selected but access_token is empty")
                        return None
                    auth_bytes = (
                        f"user={self._email}\x01auth=Bearer {self._access_token}\x01\x01"
                    ).encode("utf-8")
                    typ, resp = M.authenticate("XOAUTH2", lambda _: auth_bytes)
                else:
                    typ, resp = M.login(self._email, self._password)
                if typ != "OK":
                    logger.warning(f"[IMAP/proxy] Login failed for {self._email}: {resp}")
                    return None

                typ, _ = M.select(f'"{self._folder}"', readonly=True)
                if typ != "OK":
                    logger.warning(f"[IMAP/proxy] SELECT {self._folder} failed for {self._email}")
                    return None

                typ, data = M.search(None, "ALL")
                if typ != "OK":
                    return None

                uid_list: list[str] = []
                if data and data[0]:
                    raw_uids = data[0]
                    if isinstance(raw_uids, bytes):
                        raw_uids = raw_uids.decode()
                    uid_list = [u for u in str(raw_uids).split() if u]

                if not known_uids:
                    initial_uids = list(reversed(uid_list[-12:]))
                    for uid in initial_uids:
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
                        code = _extract_code_from_message(
                            msg,
                            filter_to=filter_to,
                            mailbox_email=mailbox_email,
                            allow_base_fallback=allow_base_fallback,
                            uid=uid,
                            log_prefix="IMAP/proxy",
                            initial_snapshot=True,
                        )
                        if code:
                            return code

                    known_uids.update(uid_list)
                    return None

                new_uids = [u for u in uid_list if u not in known_uids]
                known_uids.update(uid_list)

                for uid in reversed(new_uids):
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
                    code = _extract_code_from_message(
                        msg,
                        filter_to=filter_to,
                        mailbox_email=mailbox_email,
                        allow_base_fallback=allow_base_fallback,
                        uid=uid,
                        log_prefix="IMAP/proxy",
                    )
                    if code:
                        return code

                return None
            finally:
                try:
                    M.logout()
                except Exception:
                    pass

        while time.monotonic() < deadline:
            try:
                code = await asyncio.to_thread(_sync_fetch)
                if code:
                    return code
            except asyncio.TimeoutError:
                logger.warning("[IMAP/proxy] Operation timed out - retrying")
            except OSError as exc:
                logger.warning(f"[IMAP/proxy] Network error [{type(exc).__name__}]: {exc!r}")
            except Exception as exc:
                logger.warning(f"[IMAP/proxy] Error [{type(exc).__name__}]: {exc!r}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval, remaining))

        logger.warning(f"[IMAP/proxy] Timed out waiting for code ({email})")
        return None


# ── Multi-account IMAP client ─────────────────────────────────────────────

class MultiIMAPMailClient(MailClient):
    """
    Wraps multiple :class:`IMAPMailClient` instances and randomly selects one
    per ``generate_email()`` call.

    The chosen client is stored per-alias-email so that a subsequent
    ``poll_code(alias_email)`` always queries the correct inbox — safe for
    concurrent registrations that share a single ``MultiIMAPMailClient`` instance.
    """

    def __init__(self, clients: list[IMAPMailClient]) -> None:
        if not clients:
            raise ValueError("MultiIMAPMailClient requires at least one account")
        self._clients = clients
        # Maps generated alias email → the IMAPMailClient that owns it.
        self._routing: dict[str, IMAPMailClient] = {}

    async def generate_email(
        self,
        prefix: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> str:
        client = random.choice(self._clients)
        addr   = await client.generate_email(prefix, domain)
        # Store mapping so poll_code() knows which inbox to check.
        self._routing[addr.lower()] = client
        return addr

    async def poll_code(self, email: str, timeout: int = 120) -> Optional[str]:
        client = self._routing.get(email.lower())
        if client is None:
            # Fallback: should not normally happen; pick random client.
            logger.warning(
                f"[IMAP] No routing entry for {email!r} — "
                "falling back to random client"
            )
            client = random.choice(self._clients)
        return await client.poll_code(email, timeout)


# ── CLI smoke-test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import src.settings_db as settings_db

    async def _main() -> None:
        cfg      = await settings_db.build_config()
        imap_raw = (cfg.get("mail") or {}).get("imap", [])

        # Backward compat: single dict in config
        if isinstance(imap_raw, dict):
            imap_raw = [imap_raw]

        valid = [c for c in imap_raw if c.get("email")]
        if not valid:
            print(
                "No IMAP accounts found in SQLite settings.\n"
                "Open the WebUI and add entries under Settings → IMAP 账户 first.\n"
            )
            sys.exit(1)

        clients = [
            IMAPMailClient(
                email     = c["email"],
                password  = c["password"],
                host      = c["host"],
                port      = int(c.get("port", 993)),
                ssl       = bool(c.get("ssl", True)),
                folder    = c.get("folder", "INBOX"),
                use_alias = c.get("use_alias"),
            )
            for c in valid
        ]

        client = MultiIMAPMailClient(clients) if len(clients) > 1 else clients[0]
        addr   = await client.generate_email()
        print(f"Mailbox / alias: {addr}")
        print("Waiting 30 s for a verification code …")
        code = await client.poll_code(addr, timeout=30)
        print(f"Code: {code or '(none received)'}")

    asyncio.run(_main())

