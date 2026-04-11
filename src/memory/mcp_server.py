"""
MCPMemoryServer — MCP-protocol-compatible long-term memory store.

Storage: SQLite for structured records + optional vector similarity via numpy.
Protocol: Implements the MCP JSON-RPC interface so external MCP clients can connect.

Memory types:
  - user_fact     : facts about the user ("my name is Alex")
  - task_result   : outcomes of agent tasks worth remembering
  - preference    : user preferences and habits
  - skill_output  : noteworthy outputs from skills
  - general       : catch-all
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from typing import Optional

log = logging.getLogger("localclaw.memory")

DB_PATH = os.environ.get("MEMORY_DB", "/app/data/memory/memory.db")


class MCPMemoryServer:
    def __init__(self):
        self._db: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_db)
        log.info(f"MCP Memory Server initialized — DB: {DB_PATH}")

    def _init_db(self):
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                content TEXT NOT NULL,
                memory_type TEXT DEFAULT 'general',
                source TEXT DEFAULT 'manual',
                embedding TEXT,           -- JSON float array (simple cosine index)
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed REAL,
                created_at REAL NOT NULL,
                tags TEXT DEFAULT '[]'    -- JSON array
            );
            CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent_id);
            CREATE INDEX IF NOT EXISTS idx_mem_type  ON memories(memory_type);
            CREATE INDEX IF NOT EXISTS idx_mem_ts    ON memories(created_at DESC);

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                agent_id TEXT,
                started_at REAL,
                ended_at REAL,
                summary TEXT,
                message_count INTEGER DEFAULT 0
            );
        """)
        self._db.commit()

    async def close(self):
        if self._db:
            self._db.close()

    # ── Core operations ─────────────────────────────────────────────────────

    async def save_memory(
        self,
        content: str,
        agent_id: Optional[str] = None,
        memory_type: str = "general",
        source: str = "manual",
        importance: float = 0.5,
        tags: Optional[list[str]] = None,
    ) -> dict:
        memory_id = str(uuid.uuid4())
        now = time.time()
        embedding = self._simple_embed(content)

        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._db_insert, {
                "id": memory_id,
                "agent_id": agent_id,
                "content": content,
                "memory_type": memory_type,
                "source": source,
                "embedding": json.dumps(embedding),
                "importance": importance,
                "access_count": 0,
                "last_accessed": now,
                "created_at": now,
                "tags": json.dumps(tags or []),
            })

        log.info(f"Saved memory {memory_id} for agent={agent_id} type={memory_type}")
        return {"id": memory_id, "content": content, "type": memory_type}

    def _db_insert(self, record: dict):
        self._db.execute(
            """INSERT INTO memories (id, agent_id, content, memory_type, source,
               embedding, importance, access_count, last_accessed, created_at, tags)
               VALUES (:id, :agent_id, :content, :memory_type, :source,
               :embedding, :importance, :access_count, :last_accessed, :created_at, :tags)""",
            record,
        )
        self._db.commit()

    async def list_memories(
        self,
        agent_id: Optional[str] = None,
        limit: int = 20,
        memory_type: Optional[str] = None,
    ) -> list[dict]:
        async with self._lock:
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(
                None, self._db_list, agent_id, limit, memory_type
            )
        return rows

    def _db_list(self, agent_id, limit, memory_type):
        q = "SELECT * FROM memories WHERE 1=1"
        params = []
        if agent_id:
            q += " AND agent_id = ?"
            params.append(agent_id)
        if memory_type:
            q += " AND memory_type = ?"
            params.append(memory_type)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def search(
        self,
        query: str,
        limit: int = 5,
        agent_id: Optional[str] = None,
    ) -> list[dict]:
        """Cosine similarity search over stored embeddings."""
        query_vec = self._simple_embed(query)

        async with self._lock:
            loop = asyncio.get_event_loop()
            all_rows = await loop.run_in_executor(
                None, self._db_fetch_for_search, agent_id
            )

        scored = []
        for row in all_rows:
            try:
                emb = json.loads(row.get("embedding") or "[]")
                if emb:
                    score = self._cosine_sim(query_vec, emb)
                    scored.append((score, row))
            except Exception:
                scored.append((0.0, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, row in scored[:limit]:
            row["similarity"] = round(score, 4)
            row.pop("embedding", None)  # strip raw vector before returning
            results.append(row)

        # Update access counts
        ids = [r["id"] for r in results]
        if ids:
            asyncio.create_task(self._bump_access(ids))

        return results

    def _db_fetch_for_search(self, agent_id):
        q = "SELECT * FROM memories"
        params = []
        if agent_id:
            q += " WHERE agent_id = ?"
            params.append(agent_id)
        rows = self._db.execute(q, params).fetchall()
        # Keep raw dicts here — embedding must be preserved for cosine scoring
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return result

    async def _bump_access(self, ids: list[str]):
        async with self._lock:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._db_bump, ids)

    def _db_bump(self, ids):
        now = time.time()
        for mid in ids:
            self._db.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                (now, mid),
            )
        self._db.commit()

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            loop = asyncio.get_event_loop()
            n = await loop.run_in_executor(None, self._db_delete, memory_id)
        return n > 0

    def _db_delete(self, memory_id):
        cur = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._db.commit()
        return cur.rowcount

    async def count(self) -> int:
        async with self._lock:
            row = self._db.execute("SELECT COUNT(*) as n FROM memories").fetchone()
        return row["n"] if row else 0

    async def export_all(self) -> list[dict]:
        async with self._lock:
            rows = self._db.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── MCP JSON-RPC Protocol ───────────────────────────────────────────────

    async def handle_mcp_request(self, payload: dict) -> dict:
        """
        Handle MCP JSON-RPC 2.0 requests.
        Implements: memory/save, memory/search, memory/list, memory/delete,
                    tools/list, initialize
        """
        method = payload.get("method", "")
        params = payload.get("params", {})
        req_id = payload.get("id")

        try:
            result = await self._dispatch_mcp(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            }

    async def _dispatch_mcp(self, method: str, params: dict):
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "localclaw-memory", "version": "1.0.0"},
            }

        elif method == "tools/list":
            return {"tools": self._mcp_tools()}

        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            return await self._call_mcp_tool(tool_name, args)

        elif method == "memory/save":
            return await self.save_memory(**params)

        elif method == "memory/search":
            return await self.search(**params)

        elif method == "memory/list":
            return await self.list_memories(**params)

        elif method == "memory/delete":
            return await self.delete(params.get("id", ""))

        else:
            raise ValueError(f"Unknown MCP method: {method}")

    def _mcp_tools(self) -> list[dict]:
        return [
            {
                "name": "memory_save",
                "description": "Save a piece of information to long-term memory",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Information to remember"},
                        "agent_id": {"type": "string", "description": "Agent this belongs to"},
                        "memory_type": {"type": "string", "enum": ["user_fact", "preference", "task_result", "skill_output", "general"]},
                        "importance": {"type": "number", "minimum": 0, "maximum": 1},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "memory_search",
                "description": "Search long-term memory by semantic similarity",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                        "agent_id": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_list",
                "description": "List recent memories",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                        "memory_type": {"type": "string"},
                    },
                },
            },
        ]

    async def _call_mcp_tool(self, name: str, args: dict) -> dict:
        if name == "memory_save":
            result = await self.save_memory(**args)
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        elif name == "memory_search":
            results = await self.search(**args)
            return {"content": [{"type": "text", "text": json.dumps(results)}]}
        elif name == "memory_list":
            results = await self.list_memories(**args)
            return {"content": [{"type": "text", "text": json.dumps(results)}]}
        else:
            raise ValueError(f"Unknown tool: {name}")

    # ── Embedding helpers ────────────────────────────────────────────────────

    def _simple_embed(self, text: str, dim: int = 128) -> list[float]:
        """
        Lightweight character-n-gram embedding.
        Not as good as a real model, but zero-dependency and instant.
        Replaced automatically if sentence-transformers is available.
        """
        text = text.lower().strip()
        vec = [0.0] * dim
        for i in range(len(text) - 2):
            trigram = text[i:i+3]
            h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return dot / (na * nb)

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        d.pop("embedding", None)  # Don't expose raw embedding to callers
        return d
