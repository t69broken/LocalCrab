#!/usr/bin/env python3
"""
Auto-applied improvement for LocalCrab
Description: Error Recovery with automatic fallback when tools fail
Created: 2026-04-18T15:00:00
"""

import logging
import traceback
from typing import Callable, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def safe_tool_call(original_tool: Callable, fallback_tools: dict = None) -> Callable:
    """
    Decorator that wraps tool calls with automatic fallback logic.
    When a tool fails, tries alternative approaches from fallback_tools dict.
    
    Args:
        original_tool: The original tool function to wrap
        fallback_tools: Dict mapping tool names to alternative implementations
        
    Example fallback strategies:
        - terminal fails → try web_search for info tasks
        - web_search fails → try file search
        - memory_save fails → save to local file as fallback
    """
    def wrapper(*args, **kwargs):
        try:
            # Try original tool first
            result = original_tool(*args, **kwargs)
            logger.info(f"✓ {original_tool.__name__} succeeded")
            return result
        except Exception as e:
            logger.warning(f"✗ {original_tool.__name__} failed: {e}")
            
            # Try fallback tools if provided
            if fallback_tools:
                fallback_key = original_tool.__name__
                if fallback_key in fallback_tools:
                    try:
                        logger.info(f"Trying fallback: {fallback_tools[fallback_key].__name__}")
                        return fallback_tools[fallback_key](*args[1:], **kwargs)
                    except Exception as fallback_error:
                        logger.error(f"✗ Fallback {fallback_tools[fallback_key].__name__} also failed: {fallback_error}")
            
            # If all fails, provide a graceful degraded response
            if original_tool.__name__ in ["web_search", "web_fetch"]:
                return f"Unable to fetch: {args[0] if args else kwargs.get('query')}"
            elif original_tool.__name__ in ["memor
