"""
TaskStore — persists TaskJob metadata and check-ins to SQLite.

Survives server restarts. Jobs that were running at shutdown are marked
"interrupted" on next startup. Completed/failed/cancelled jobs are restored
read-only so they remain visible in the UI.
"""

import asyncio
import logging
import os
import sqlite3
import time
from typing import Optional

log = logging.getLogger("localclaw.task_store")

DB_PATH = os.environ.get("TASKS_DB", "/app/data/history/tasks.db")

# How many recent completed jobs to load on startup (oldest-first, capped)
MAX_LOAD_JOBS = 200


class TaskStore:
    def __init__(self):
        self._db: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_db)
        log.info(f"Task store initialized — DB: {DB_PATH}")

    def _init_db(self):
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                job_id      TEXT PRIMARY KEY,
                agent_id    TEXT NOT NULL,
                task        TEXT NOT NULL,
                prompt      TEXT,
                status      TEXT NOT NULL,
                model_used  TEXT,
                result      TEXT,
                error       TEXT,
                created_at  REAL NOT NULL,
                started_at  REAL,
                ended_at    REAL,
                poke_count  INTEGER DEFAULT 0,
                tokens_out  INTEGER DEFAULT 0,
                step        INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_checkins (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL,
                ts          REAL NOT NULL,
                prompt      TEXT,
                response    TEXT,
                step        INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_checkins_job  ON task_checkins(job_id, ts);
        """)
        self._db.commit()

    async def close(self):
        if self._db:
            self._db.close()

    # ── Write ops ─────────────────────────────────────────────────────────────

    async def save_job(self, job) -> None:
        """Insert or update a task job record."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._upsert_job, job)

    def _upsert_job(self, job):
        self._db.execute("""
            INSERT INTO tasks
                (job_id, agent_id, task, prompt, status, model_used, result, error,
                 created_at, started_at, ended_at, poke_count, tokens_out, step)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
                status     = excluded.status,
                model_used = excluded.model_used,
                result     = excluded.result,
                error      = excluded.error,
                started_at = excluded.started_at,
                ended_at   = excluded.ended_at,
                poke_count = excluded.poke_count,
                tokens_out = excluded.tokens_out,
                step       = excluded.step
        """, (
            job.job_id, job.agent_id, job.task, job.prompt,
            str(job.status), job.model_used, job.result, job.error,
            job.created_at, job.started_at, job.ended_at,
            job.poke_count, job.tokens_out, job.step,
        ))
        self._db.commit()

    async def save_checkin(self, job_id: str, ts: float, prompt: str, response: str, step: int) -> None:
        """Insert a single check-in record."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._insert_checkin, job_id, ts, prompt, response, step)

    def _insert_checkin(self, job_id, ts, prompt, response, step):
        self._db.execute(
            "INSERT INTO task_checkins (job_id, ts, prompt, response, step) VALUES (?,?,?,?,?)",
            (job_id, ts, prompt, response, step),
        )
        self._db.commit()

    # ── Read ops ──────────────────────────────────────────────────────────────

    async def load_all_jobs(self) -> list[dict]:
        """Return all jobs ordered oldest-first (capped at MAX_LOAD_JOBS)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._load_jobs)

    def _load_jobs(self):
        rows = self._db.execute(
            "SELECT * FROM tasks ORDER BY created_at ASC LIMIT ?",
            (MAX_LOAD_JOBS,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def load_checkins(self, job_id: str) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._load_checkins, job_id)

    def _load_checkins(self, job_id):
        rows = self._db.execute(
            "SELECT ts, prompt, response, step FROM task_checkins WHERE job_id=? ORDER BY ts ASC",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Startup fixup ─────────────────────────────────────────────────────────

    async def mark_interrupted(self) -> int:
        """
        Called once at startup: any job that was still running when the server
        died gets marked 'interrupted' so the UI doesn't show ghost spinners.
        Returns the number of jobs updated.
        """
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._mark_interrupted)

    def _mark_interrupted(self) -> int:
        cur = self._db.execute(
            """UPDATE tasks
               SET status = 'interrupted', ended_at = ?
               WHERE status IN ('running', 'pending', 'checking', 'stalled', 'looping')""",
            (time.time(),),
        )
        self._db.commit()
        return cur.rowcount
