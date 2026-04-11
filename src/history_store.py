"""
ChatHistoryStore — Persists per-agent conversation history to SQLite.

Survives container restarts and is accessible from any machine that
mounts the same data volume (/app/data/history/history.db).
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Optional

log = logging.getLogger("localclaw.history")

DB_PATH = os.environ.get("HISTORY_DB", "/app/data/history/history.db")

# Cap per-agent stored history to avoid unbounded growth
MAX_MESSAGES_PER_AGENT = 2000


class ChatHistoryStore:
    def __init__(self):
        self._db: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_db)
        log.info(f"Chat history store initialized — DB: {DB_PATH}")

    def _init_db(self):
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id       TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                role     TEXT NOT NULL,
                content  TEXT NOT NULL,
                model    TEXT,
                ts       REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hist_agent ON messages(agent_id, ts);
        """)
        self._db.commit()

    async def close(self):
        if self._db:
            self._db.close()

    async def append(
        self,
        agent_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
    ) -> str:
        """Insert a single message. Returns its id."""
        msg_id = str(uuid.uuid4())
        now = time.time()
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._db_insert, msg_id, agent_id, role, content, model, now)
        return msg_id

    def _db_insert(self, msg_id, agent_id, role, content, model, ts):
        self._db.execute(
            "INSERT INTO messages (id, agent_id, role, content, model, ts) VALUES (?,?,?,?,?,?)",
            (msg_id, agent_id, role, content, model, ts),
        )
        self._db.commit()

    async def append_exchange(
        self,
        agent_id: str,
        messages: list[dict],
        response: str,
        model: Optional[str] = None,
    ):
        """Persist a full user→assistant exchange in one batch."""
        now = time.time()
        rows = []
        for i, m in enumerate(messages):
            rows.append((str(uuid.uuid4()), agent_id, m["role"], m.get("content", ""), None, now - 0.001 * (len(messages) - i)))
        rows.append((str(uuid.uuid4()), agent_id, "assistant", response, model, now))
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._db_batch_insert, rows)
            # Prune old messages if over cap
            await loop.run_in_executor(None, self._db_prune, agent_id)

    def _db_batch_insert(self, rows):
        self._db.executemany(
            "INSERT OR IGNORE INTO messages (id, agent_id, role, content, model, ts) VALUES (?,?,?,?,?,?)",
            rows,
        )
        self._db.commit()

    def _db_prune(self, agent_id):
        """Delete oldest messages beyond MAX_MESSAGES_PER_AGENT."""
        count = self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE agent_id=?", (agent_id,)
        ).fetchone()[0]
        if count > MAX_MESSAGES_PER_AGENT:
            excess = count - MAX_MESSAGES_PER_AGENT
            self._db.execute(
                """DELETE FROM messages WHERE id IN (
                    SELECT id FROM messages WHERE agent_id=?
                    ORDER BY ts ASC LIMIT ?
                )""",
                (agent_id, excess),
            )
            self._db.commit()

    async def load(self, agent_id: str, limit: int = 100) -> list[dict]:
        """Return the most recent `limit` messages for an agent, oldest-first."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(None, self._db_load, agent_id, limit)
        return rows

    def _db_load(self, agent_id, limit):
        rows = self._db.execute(
            """SELECT role, content, model, ts FROM messages
               WHERE agent_id=?
               ORDER BY ts DESC LIMIT ?""",
            (agent_id, limit),
        ).fetchall()
        # Reverse so oldest is first
        return [{"role": r["role"], "content": r["content"], "model": r["model"], "ts": r["ts"]}
                for r in reversed(rows)]

    async def clear(self, agent_id: str) -> int:
        """Delete all history for an agent. Returns rows deleted."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            n = await loop.run_in_executor(None, self._db_clear, agent_id)
        log.info(f"Cleared {n} messages for agent {agent_id}")
        return n

    def _db_clear(self, agent_id):
        cur = self._db.execute("DELETE FROM messages WHERE agent_id=?", (agent_id,))
        self._db.commit()
        return cur.rowcount

    async def list_agents(self) -> list[dict]:
        """Return agent_ids that have stored history with message counts."""
        async with self._lock:
            rows = self._db.execute(
                """SELECT agent_id, COUNT(*) as count, MAX(ts) as last_ts
                   FROM messages GROUP BY agent_id ORDER BY last_ts DESC"""
            ).fetchall()
        return [{"agent_id": r["agent_id"], "message_count": r["count"], "last_ts": r["last_ts"]}
                for r in rows]
