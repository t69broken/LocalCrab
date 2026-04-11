"""
import_external.py — Background importer for external memory sources.

Runs as a fire-and-forget asyncio task after LocalClaw startup.
Never blocks startup or task execution. Errors are logged and skipped.

Sources:
  - /home/tyson/AGENT/agent_memory.db   — SQLite memories from the AGENT project
  - /home/tyson/.nanobot/workspace/     — Markdown files from nanobot workspace
"""

import asyncio
import json
import logging
import math
import os
import sqlite3
import hashlib
import time
from pathlib import Path

log = logging.getLogger("localclaw.memory.import")

AGENT_DB       = Path("/home/tyson/AGENT/agent_memory.db")
NANOBOT_DIR    = Path("/home/tyson/.nanobot/workspace")

# Markdown files worth importing from nanobot workspace (relative to NANOBOT_DIR)
NANOBOT_FILES = [
    "memory/MEMORY.md",
    "memory/HISTORY.md",
    "USER.md",
    "SOUL.md",
    "AGENTS.md",
    "TOOLS.md",
    "HEARTBEAT.md",
]

MAX_CONTENT_LEN = 6000   # truncate very large files


async def run_import(memory_server) -> None:
    """
    Entry point — called with asyncio.create_task() after startup.
    Imports new memories only (skips already-imported ones).
    """
    log.info("[ExternalImport] Starting background memory import…")
    imported = 0

    try:
        imported += await _import_agent_db(memory_server)
    except Exception as e:
        log.warning(f"[ExternalImport] AGENT db import failed: {e}")

    try:
        imported += await _import_nanobot(memory_server)
    except Exception as e:
        log.warning(f"[ExternalImport] Nanobot import failed: {e}")

    log.info(f"[ExternalImport] Done — {imported} new memories imported.")


async def _already_imported(memory_server, source_key: str) -> bool:
    """Check if a memory with this source key already exists."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _db_has_source, memory_server._db, source_key)


def _db_has_source(db: sqlite3.Connection, source_key: str) -> bool:
    if db is None:
        return False
    row = db.execute(
        "SELECT 1 FROM memories WHERE source = ? LIMIT 1", (source_key,)
    ).fetchone()
    return row is not None


async def _import_agent_db(memory_server) -> int:
    """Import all rows from /home/tyson/AGENT/agent_memory.db."""
    if not AGENT_DB.exists():
        log.info(f"[ExternalImport] AGENT db not found at {AGENT_DB}, skipping.")
        return 0

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _read_agent_db)
    imported = 0

    for row in rows:
        source_key = f"imported:agent:{row['id']}"

        if await _already_imported(memory_server, source_key):
            continue

        # Combine summary + full context; prefer full context if available
        full = (row.get("full_context") or "").strip()
        summary = (row.get("content_summary") or "").strip()
        content = full if full else summary
        if not content:
            continue

        # Tags: comma-separated string → list
        raw_tags = row.get("tags") or ""
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        tags.append("imported")
        tags.append("agent-db")

        try:
            await memory_server.save_memory(
                content=content,
                memory_type="user_fact",
                source=source_key,
                importance=0.8,
                tags=tags,
            )
            log.info(f"[ExternalImport] Imported agent memory: {summary[:60]}")
            imported += 1
        except Exception as e:
            log.warning(f"[ExternalImport] Failed to import agent row {row['id']}: {e}")

        await asyncio.sleep(0)   # yield to event loop between inserts

    return imported


def _read_agent_db() -> list[dict]:
    conn = sqlite3.connect(str(AGENT_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM memories ORDER BY timestamp ASC").fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


async def _import_nanobot(memory_server) -> int:
    """Import key markdown files from the nanobot workspace."""
    if not NANOBOT_DIR.exists():
        log.info(f"[ExternalImport] Nanobot dir not found at {NANOBOT_DIR}, skipping.")
        return 0

    imported = 0

    for rel_path in NANOBOT_FILES:
        full_path = NANOBOT_DIR / rel_path
        if not full_path.exists():
            log.debug(f"[ExternalImport] Nanobot file not found: {rel_path}, skipping.")
            continue

        source_key = f"imported:nanobot:{rel_path}"

        if await _already_imported(memory_server, source_key):
            continue

        try:
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, full_path.read_text, "utf-8")
        except Exception as e:
            log.warning(f"[ExternalImport] Could not read {rel_path}: {e}")
            continue

        content = content.strip()
        if not content:
            continue

        # Truncate very large files
        if len(content) > MAX_CONTENT_LEN:
            content = content[:MAX_CONTENT_LEN] + "\n\n[…truncated]"

        tags = ["imported", "nanobot", Path(rel_path).stem.lower()]

        try:
            await memory_server.save_memory(
                content=content,
                memory_type="general",
                source=source_key,
                importance=0.7,
                tags=tags,
            )
            log.info(f"[ExternalImport] Imported nanobot file: {rel_path}")
            imported += 1
        except Exception as e:
            log.warning(f"[ExternalImport] Failed to import nanobot file {rel_path}: {e}")

        await asyncio.sleep(0)   # yield to event loop between inserts

    return imported
