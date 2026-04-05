"""
settings_db.py — WebUI-managed configuration stored in SQLite.
Non-common settings (mail credentials, timeouts, mouse, etc.) live here.
Common/operational settings (engine, headless, concurrency, proxy) stay in config.yaml.
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

from src.db import DB_PATH

# ── Sections managed in DB ────────────────────────────────────────────────

_SECTIONS = [
    "mail.gptmail",
    "mail.npcmail",
    "mail.yydsmail",
    "mail.imap",
    "registration",
    "team",
    "sync",
    "oauth",
    "mouse",
    "timeouts",
    "timing",
]

_DEFAULTS: dict[str, Any] = {
    "mail.gptmail": {"api_key": "", "base_url": "https://mail.chatgpt.org.uk"},
    "mail.npcmail": {"api_key": "", "base_url": "https://dash.xphdfs.me"},
    "mail.yydsmail": {"api_key": "", "base_url": "https://maliapi.215.im/v1"},
    "mail.imap": [],
    "registration": {"prefix": "", "domain": ""},
    "team": {"url": "", "key": ""},
    "sync": {"url": "", "key": ""},
    "oauth": {"enabled": True, "timeout": 45},
    "mouse": {
        "steps_min": 4,
        "steps_max": 8,
        "step_delay_min": 0.003,
        "step_delay_max": 0.010,
        "hover_min": 0.02,
        "hover_max": 0.08,
    },
    "timeouts": {
        "page_load": 30,
        "auth0_redirect": 8,
        "email_input": 15,
        "password_input": 60,
        "otp_input": 60,
        "otp_code": 180,
        "profile_detect": 15,
        "profile_field": 5,
        "complete_redirect": 20,
        "oauth_navigate": 20,
        "oauth_flow_element": 8,
        "oauth_login_email": 8,
        "oauth_login_password": 10,
        "oauth_token_exchange": 30,
        "oauth_total": 45,
    },
    "timing": {
        "post_nav": 1.0,
        "pre_fill": 0.5,
        "post_click": 1.5,
        "post_complete": 1.0,
    },
}


# ── Internal helpers ──────────────────────────────────────────────────────

async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                section TEXT PRIMARY KEY,
                value   TEXT NOT NULL DEFAULT '{}'
            )
        """)
        await db.commit()


# ── Public API ────────────────────────────────────────────────────────────

async def init_from_yaml() -> None:
    """Migrate non-common settings from YAML to DB (only for missing sections)."""
    await _ensure_table()
    import src.config as cfg_mod
    yaml_cfg = cfg_mod.load()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT section FROM settings") as cur:
            existing = {row[0] async for row in cur}

        to_insert: list[tuple[str, str]] = []

        for section in _SECTIONS:
            if section in existing:
                continue

            if section.startswith("mail."):
                provider = section[5:]  # e.g. "gptmail"
                raw = yaml_cfg.get("mail", {}).get(provider, _DEFAULTS[section])
                to_insert.append((section, json.dumps(raw, ensure_ascii=False)))

            elif section == "oauth":
                val = {
                    "enabled": yaml_cfg.get("enable_oauth",
                                             yaml_cfg.get("oauth", {}).get("enabled", True)),
                    "timeout": yaml_cfg.get("oauth", {}).get("timeout", 45),
                }
                to_insert.append((section, json.dumps(val, ensure_ascii=False)))

            elif section in yaml_cfg and yaml_cfg[section]:
                to_insert.append((section, json.dumps(yaml_cfg[section], ensure_ascii=False)))

            else:
                to_insert.append((section, json.dumps(_DEFAULTS[section], ensure_ascii=False)))

        if to_insert:
            await db.executemany(
                "INSERT OR IGNORE INTO settings (section, value) VALUES (?, ?)",
                to_insert,
            )
            await db.commit()


async def get_section(section: str) -> Any:
    """Return a single settings section (or default if not stored)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM settings WHERE section = ?", (section,)
        ) as cur:
            row = await cur.fetchone()
    return json.loads(row[0]) if row else _DEFAULTS.get(section, {})


async def set_section(section: str, value: Any) -> None:
    """Persist a settings section to DB."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (section, value) VALUES (?, ?)",
            (section, json.dumps(value, ensure_ascii=False)),
        )
        await db.commit()


async def get_all() -> dict[str, Any]:
    """Return all settings sections (DB values override defaults)."""
    await _ensure_table()
    result = {k: v for k, v in _DEFAULTS.items()}
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT section, value FROM settings") as cur:
            rows = await cur.fetchall()
    for section, value_json in rows:
        result[section] = json.loads(value_json)
    return result


async def build_config() -> dict[str, Any]:
    """
    Build the complete runtime config dict.
    Priority: YAML defaults (common settings) → DB settings (non-common settings).
    """
    import src.config as cfg_mod
    yaml_cfg = cfg_mod.load()
    db = await get_all()

    cfg = dict(yaml_cfg)

    # Mail credentials
    mail = cfg.setdefault("mail", {})
    for provider in ("gptmail", "npcmail", "yydsmail"):
        key = f"mail.{provider}"
        if db.get(key):
            mail[provider] = db[key]
    if db.get("mail.imap") is not None:
        mail["imap"] = db["mail.imap"]

    # Other non-common sections
    for section in ("registration", "team", "sync", "mouse", "timeouts", "timing"):
        if db.get(section):
            cfg[section] = db[section]

    # OAuth
    oauth_db = db.get("oauth", {})
    if oauth_db:
        cfg["enable_oauth"] = oauth_db.get("enabled", True)
        cfg.setdefault("oauth", {})["enabled"] = oauth_db.get("enabled", True)
        cfg.setdefault("oauth", {})["timeout"] = oauth_db.get("timeout", 45)
        cfg.setdefault("timeouts", {})["oauth_total"] = oauth_db.get("timeout", 45)

    return cfg

