#!/usr/bin/env python3
"""
Auto-applied improvement for LocalCrab
Description: Proactive Updates from user corrections
Created: 2026-04-18T15:00:00
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOCALCRAW_BASE = "/home/tyson/ClaudeLocalClaw/localclaw"
SYSTEM_PROMPTS_DIR = os.path.join(LOCALCRAW_BASE, "data", "system_prompts")


def extract_preferences(user_message: str) -> list[dict]:
    """
    Extract user corrections/preferences from messages.
    
    Examples:
    - "Don't use cat, use read_file instead" → save as file handling preference
    - "Use web_search before terminal for network info" → save as tool priority
    """
    preferences = []
    
    # Pattern: "Don't use X, use Y instead"
    dont_pattern = r"Don\'t\s+(refuse|avoid|skip)\s+(?:use\s+)?(\w+(?:\s+\w+)*)\s*(?:instead|rather|rather than)\s+(?:use\s+)?(\w+(?:\s+\w+)*)"
    
    # Pattern: "Use X instead of Y"
    use_pattern = r"Use\s+(\w+(?:\s+\w+)*)\s+instead\s+of\s+(\w+(?:\s+\w+)*)\s+[\,\.]??\s*(.*)?"
    
    # Pattern: "Remember to do X"
    remember_pattern = r"Remember\s+(?:to\s+)?(\S.+?)[\,\.]??"
    
    # Pattern: "Please don't do X anymore"
    please_dont_pattern = r"Please\s+donth\s+(do|use|refuse)\s+(.+?)[\,\.]?\s*anymore?"
    
    for pattern in [dont_pattern, use_pattern, remember_pattern, please_dont_pattern]:
        matches = re.finditer(pattern, user_message, re.IGNORECASE)
        for match in matches:
            group = match.groups()
            if group:
                description = group[2] if len(group) > 2 else f"{group[0]}, not {group[1]}"
                preferences.append({
                    "description": description.lower(),
                    "importance": 0.8,
                    "tags": ["user_correction", "preference"],
                    "created_at": datetime.now().isoformat()
                })
                break
    
    # Also check memory entries for recent corrections
    memory_file = os.path.join(LOCALCRAW_BASE, "data", "corrections.json")
    if os.path.exists(memory_file):
        try:
            with open(memory_file) as f:
                corrections = json.load(f)
            for corr in corrections.get("recent", []):
                if corr.get("type") == "correction":
                    preferences.append({
                        "description": str(corr.get("preference", "")).lower(),
                        "importance": corr.get("importance", 0.7),
                        "tags": ["memory_correction"],
                        "created_at": corr.get("timestamp", "")
                    })
        except Exception as e:
            logger.warning(f"Error reading corrections: {e}")
    
    return preferences


async def update_system_prompts(preference: str) -> bool:
    """
    Update system prompts with user preferences.
    
    Strategy:
    1. Save preference to corrections database
    2. Generate updated system prompt variant
    3. Inject into agent session context
    """
    logger.info(f"Processing preference: {preference}")
    
    # Step 1: Save to corrections database
    corrections_file = os.path.join(system_prompts_dir, "corrections.json")
    os.makedirs(os.path.dirname(corrections_file), exist_ok=True)
    
    with open(corrections_file, "a") as f:
        f.write(json.dumps({
            "preference": preference,
            "importance": 0.7,
            "tags": ["user_correction", "preference"],
            "timestamp": datetime.now().isoformat()
        }) + "\n")
    
    # Step 2: Create updated prompt variant
    updated_prompt = generate_updated_prompt()
    
    # Step 3: Save updated prompt for injection
    prompt_file = os.path.join(system_prompts_dir, "updated_prompt_{}.md".format(
        datetime.now().strftime("%Y%m%d_%H%M%S")
    ))
    with open(prompt_file, "w") as f:
        f.write(updated_prompt)
    
    logger.info(f"Saved updated prompt to {prompt_file}")
    return True


def generate_updated_prompt() -> str:
    """
    Generate updated system prompt incorporating recent corrections.
    
    This function reads recent corrections and injects them into the base system prompt.
    """
    corrections_file = os.path.join(sys
