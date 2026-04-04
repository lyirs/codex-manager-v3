"""
proxy_pool.py — Proxy pool backed by the proxies SQLite table.

Usage:
    python -m src.proxy_pool load [proxies.txt]
    python -m src.proxy_pool list
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import aiosqlite
from loguru import logger

from src.db import DB_PATH


# ──────────────────────────────────────────────
# Write
# ──────────────────────────────────────────────

async def load_from_file(path: Path) -> int:
    """Bulk-insert proxies from a text file into the DB (skip duplicates)."""
    if not path.exists():
        logger.warning(f"[proxy_pool] File not found: {path}")
        return 0

    lines = [
        l.strip()
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]

    loaded = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for addr in lines:
            await db.execute(
                "INSERT OR IGNORE INTO proxies (address, fail_count, last_used, is_active) VALUES (?, 0, 0, 1)",
                (addr,),
            )
            loaded += 1
        await db.commit()

    logger.info(f"[proxy_pool] Loaded {loaded} proxies from {path}")
    return loaded


async def report_result(address: str, success: bool) -> None:
    """Update fail_count; deactivate proxy after 3 consecutive failures."""
    async with aiosqlite.connect(DB_PATH) as db:
        if success:
            await db.execute(
                "UPDATE proxies SET fail_count = 0 WHERE address = ?",
                (address,),
            )
        else:
            await db.execute(
                "UPDATE proxies SET fail_count = fail_count + 1 WHERE address = ?",
                (address,),
            )
            await db.execute(
                "UPDATE proxies SET is_active = 0 WHERE address = ? AND fail_count >= 3",
                (address,),
            )
        await db.commit()


async def add(address: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO proxies (address, fail_count, last_used, is_active) VALUES (?, 0, 0, 1)",
            (address,),
        )
        await db.commit()


async def remove(address: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM proxies WHERE address = ?", (address,))
        await db.commit()


# ──────────────────────────────────────────────
# Read
# ──────────────────────────────────────────────

async def acquire() -> Optional[str]:
    """Return the least-recently-used active proxy, updating its last_used timestamp."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT address FROM proxies WHERE is_active = 1 ORDER BY last_used ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if not row:
            return None
        address: str = row["address"]
        await db.execute(
            "UPDATE proxies SET last_used = ? WHERE address = ?",
            (time.time(), address),
        )
        await db.commit()
    return address


async def list_all() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT address, fail_count, last_used, is_active FROM proxies ORDER BY is_active DESC, last_used ASC"
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def active_count() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM proxies WHERE is_active = 1")
        row = await cur.fetchone()
    return row[0] if row else 0


# ──────────────────────────────────────────────
# CLI entry-point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    async def _main() -> None:
        from src.db import init as db_init
        await db_init()

        cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
        if cmd == "load":
            fp = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("proxies.txt")
            n = await load_from_file(fp)
            print(f"Loaded {n} proxies")
        elif cmd == "list":
            from rich.table import Table
            from rich.console import Console
            rows = await list_all()
            tbl = Table(title="Proxy Pool")
            tbl.add_column("Address", style="cyan")
            tbl.add_column("Fail", justify="right")
            tbl.add_column("Active", justify="center")
            for r in rows:
                tbl.add_row(r["address"], str(r["fail_count"]), "✓" if r["is_active"] else "✗")
            Console().print(tbl)
        else:
            print("Usage:  python -m src.proxy_pool  load [file]  |  list")

    asyncio.run(_main())

