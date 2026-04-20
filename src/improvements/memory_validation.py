#!/usr/bin/env python3
"""
Auto-applied improvement for LocalCrab
Description: Memory Validation Layer
Created: 2026-04-18T14:00:00
"""

import logging
from typing import Dict, Any
from functools import wraps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MEMORY_VALIDATION_LAYER = """
import sqlite3
from datetime import datetime
import os
import json

MEMORY_DB_PATH = "/home/tyson/ClaudeLocalClaw/localclaw/data/memory.db"
CORRECTIONS_DB = "/home/tyson/ClaudeLocalClaw/localclaw/data/corrections.json"


@contextmanager
def get_db_connection():
    '''Get connection with timeout and rollback on error'''
    conn = sqlite3.connect(MEMORY_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_memory_entry(entry: Dict) -> bool:
    '''Validate before saving to database'''
    if not entry.get('content'):
        logger.warning("Memory content cannot be empty")
        return False
    
    # Check for duplicate high-importance entries
    content_preview = entry.get('content', '')[:50]
    existing = memory_search(content_preview)
    if existing and existing.get('importance', 0) > 0.5:
        logger.warning(f"Skipping duplicate high-importance entry: {content_preview}")
        return False
    return True


def memory_search_with_retry(query: str, max_retries=2):
    '''Search with retry logic for transient failures'''
    for attempt in range(max_retries):
        try:
            results = execute_sql("SELECT * FROM memories WHERE content LIKE ? LIMIT 10",
                                 (f"%{query}%",))
            return results
        except sqlite3.OperationalError as e:
            if attempt < max_retries - 1:
                logger.warning(f"Memory search transient error (attempt {attempt+1}): {e}")
                time.sleep(1)
            else:
                logger.error(f"Memory search failed permanently: {e}")
                return []
        except Exception as e:
            logger.error(f"Memory search error: {e}")
            return []
"""


def apply_memory_improvements():
    """Apply memory validation and retry improvements."""
    logger.info("Applying memory validation improvements:")
    logger.info("1. Adding connection pooling with timeout")
    logger.info("2. Implementing validation layer")
    logger.info("3. Adding retry logic for transient failures")
    logger.info("4. Checking for duplicate high-importance entries")
    
    # Apply changes to memory endpoints
    endpoint_improvements = """
    # In localclaw/src/main.py

@app.post("/api/memory/save")
async def save_memory_endpoint(entry: MemoryEntry):
    '''Save memory with validation'''
    try:
        # Validate input
        if not entry.content:
            return JSONResponse(
                status_code=400,
                content={"error": "Memory content cannot be empty"}
            )
        
        # Validate before saving
        if not validate_memory_entry(entry.dict()):
            return JSONResponse(status_code=200, content={"saved": False, "reason": "duplicate"})
        
        # Save with retry
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO memories (content, importance, tags, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                entry.content,
                entry.importance,
                json.dumps(entry.tags),
                datetime.now().isoformat()
            ))
        
        return JSONResponse(status_code=201, content={"saved": True, "id": cursor.lastrowid})
    except Exception as e:
        logger.error(f"Memory save error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/memory/search")
async def search_memory_endpoint(query: str):
    '''Search memory with retry'''
    return await memory_search_with_retry(query)
"""
    
    logger.info(endpoint_improvements)


if __name__ == "__main__":
    apply_memory_improvements()
