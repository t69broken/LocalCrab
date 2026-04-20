#!/usr/bin/env python3
"""
Auto-applied improvement for LocalCrab
Description: Agent Execution Reliability - Add retry logic and timeout handling
Created: 2026-04-18T14:00:00
"""

import asyncio
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ASYNC_RETRY_DECORATOR = """
import asyncio
from functools import wraps
import logging
import time

logger = logging.getLogger(__name__)

async def retry_on_error(max_retries=3, delay=2.0):
    '''Decorator for async functions - retries on error with exponential backoff'''
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    logger.warning(f"Attempt {attempt+1} failed for {func.__name__}: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay * (2 ** attempt))  # Exponential backoff
            logger.error(f"All retries exhausted for {func.__name__}, last error: {last_error}")
            raise last_error
        return wrapper
    return decorator
"""

# Apply retry decorator to execute function
apply_improvement = """
# In localclaw/src/main.py or task_watchdog.py

from improvements.agent_execution import retry_on_error

# Wrap execute endpoint
@retry_on_error(max_retries=3, delay=1.0)
async def execute_endpoint(self, task: str, prompt: Optional[str] = None, max_steps: int = 10):
    '''Execute with automatic retry on transient failures'''
    return await self.agent_manager.execute(task, prompt, max_steps)

# Also update task_watchdog.py
async def execute_task_with_reliability(self, task, prompt, max_steps=10):
    for attempt in range(3):
        try:
            result = await self.execute(task, prompt, max_steps)
            if self._parse_result(result):
                return result
        except Exception as e:
            logger.warning(f"Task execution failed (attempt {attempt + 1}): {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    # Return degraded response if all fails
    return {"error": "Task execution failed after retries", "task": task}
"""

# Validate endpoints
apply_improvement_validation = """
# Add validation and retry to execute API
async def validated_execute(self, task: str, prompt: Optional[str] = None, max_steps: int = 10):
    # Validate inputs
    if not task:
        raise ValueError("Task cannot be empty")
    
    # Execute with retry
    for attempt in range(3):
        try:
            result = await self.agent_manager.execute(task, prompt, max_steps)
            # Check for valid response
            if result and "error" not in str(result):
                return result
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on attempt {attempt + 1}")
        except Exception as e:
            logger.warning(f"Error on attempt {attempt + 1}: {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
    
    return {"error": "Task execution failed", "details": "See logs for details"}
"""


def apply_this_improvement():
    """Apply agent execution reliability improvements."""
    logger.info("Applying agent execution reliability improvements:")
    logger.info("1. Adding retry decorator to execute endpoints")
    logger.info("2. Implementing timeout handling")
    logger.info("3. Adding input validation")
    logger.info("\nTo apply these changes manually:")
    logger.info("- Copy ASYNC_RETRY_DECORATOR to main.py")
    logger.info("- Wrap execute_endpoint with @retry_on_error")
    logger.info("- Update task_watchdog.py execute_task function")
    logger.info("- Run: docker compose restart localclaw")


if __name__ == "__main__":
    apply_improvement()
