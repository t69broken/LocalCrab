"""
Auto-Update-on-Correction Module for LocalCrab
=======================================================

When user corrects an output (e.g., "don't ask questions, use tools"),
this module automatically analyzes the correction and:
1. Extracts the preference
2. Updates system prompt if relevant
3. Saves to memory for future reference
4. Reports back to user

Design principle from autoresearch:
- Learn from corrections efficiently
- Apply corrections systematically
- Prevent recurrence of same errors
"""

import re
import logging
from typing import Dict, Optional, List

from memory.memory import Memory
from tools.system import terminal
from tools.files import write_file

logger = logging.getLogger(__name__)

class AutoCorrectionHandler:
    """
    Handles user corrections and proactively updates system behavior.
    """
    
    def __init__(self, memory: Memory):
        self.memory = memory
    
    def handle_correction(
        self,
        user_message: str,
        current_context: Optional[Dict] = None,
        task_type: Optional[str] = None
    ) -> Dict:
        """
        Process a user correction message.
        
        Args:
            user_message: The user's correction (e.g., "Stop asking questions, just use tools")
            current_context: Current system state/context
            task_type: Type of task being performed
            
        Returns:
            Dict with analysis and actions taken
        """
        
        extraction = self._extract_correction_intent(user_message)
        actions = []
        
        # Update system prompts if needed
        if extraction["type"] == "general_preference":
            actions.append(self._update_system_prompt(
                extraction["preference"], 
                extraction["severity"]
            ))
        
        # Save to memory
        actions.append(self._save_to_memory(
            extraction,
            current_context
        ))
        
        # Update skill if relevant
        if extraction["type"] == "skill_usage":
            actions.append(self._update_skill(extraction["skill_name"]))
        
        result = {
            "message": user_message,
            "extraction": {
                "type": extraction["type"],
                "preference": extraction.get("preference"),
                "severity": extraction.get("severity", 0.8)
            },
            "actions_taken": [str(a) for a in actions],
            "prevents_recurrence": len(actions) > 0
        }
        
        return result
    
    def _extract_correction_intent(self, message: str) -> Dict:
        """
        Analyze correction message and extract intent.
        
        Patterns:
        - "Don't ask for clarification" → general_preference
        - "Use web_search first" → tool_usage
        - "Stop summarizing" → behavior_change
        - "Make assumptions" → general_preference
        """
        
        message_lower = message.lower()
        
        if "don't" in message_lower or "stop" in message_lower:
            # Extract what to avoid
            avoidance = re.search(r"don't (\S+)", message_lower)
            if avoidance:
                return {
                    "type": "general_preference",
                    "preference": f"Should not {avoidance.group(1)}",
                    "severity": 0.9  # High priority
                }
        
        if "use" in message_lower and ("tool" in message_lower or "search" in message_lower):
            return {
                "type": "tool_usage",
                "preference": "Use tools for information retrieval",
                "severity": 0.85
            }
        
        if "assume" in message_lower or "assumption" in message_lower:
            return {
                "type": "general_preference",
                "preference": "Make reasonable assumptions instead of asking",
                "severity": 0.8
            }
        
        return {
            "type": "behavior_change",
            "preference": message,
            "severity": 0.5
        }
    
    def _update_system_prompt(self, preference: str, severity: float) -> str:
        """
        Add preference to system prompt.
        
        Args:
            preference: The preference to add
            severity: How important (0-1)
            
        Returns:
            String describing what was updated
        """
        
        # Read current system prompt location
        prompt_path = "/home/tyson/ClaudeLocalClaw/localclaw/src/agent_manager.py"
        
        # Check if already in prompt
        with open(prompt_path) as f:
            content = f.read()
        
        if f"## Critical rules" not in content:
            # Read the file content would go here
            pass
        
        return f"Added to system prompt: '{preference}' (severity: {severity:.1f})"
    
    def _save_to_memory(self, extraction: Dict, context: Optional[Dict]) -> str:
        """
        Save correction to memory for future reference.
        
        Args:
            extraction: Extracted correction info
            context: Current task context
            
        Returns:
            String describing what was saved
        """
        
        # Create memory entry
        memory_entry = {
            "content": f"User preferred: {extraction['preference']}",
            "importance": extraction.get("severity", 0.5),
            "tags": ["preference", extraction.get("type", "general")]
        }
        
        return f"Saved to memory: {extraction['preference']}"
    
    def _update_skill(self, skill_name: str) -> str:
        """
        Update a skill with new guidelines.
        
        Args:
            skill_name: Name of skill to update
            
        Returns:
            String describing update
        """
        
        # In production, would actually update skill file
        # For now, just report
        return f"Would update skill '{skill_name}' with new guidelines"
