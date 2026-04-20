"""
Self-Planning Agent Module for LocalCrab
================================================

This module enables agents to create, execute, and adapt their own task plans.

Inspired by autoresearch's systematic exploration:
- Break complex tasks into subtasks
- Execute subtasks in parallel or sequence
- Adapt plan based on results
- Report progress at each step
"""

import asyncio
import json
import logging
from typing import List, Dict, Any, Optional

from agent_manager import AgentManager
from tools import tool_call

logger = logging.getLogger(__name__)

class SelfPlanningAgent:
    """
    Agent with self-planning capabilities.
    
    Can:
    - Receive a complex task
    - Generate a step-by-step plan
    - Execute plan with progress reporting
    - Adapt plan based on results
    - Report final answer
    """
    
    def __init__(self, agent_manager: AgentManager):
        self.agent_manager = agent_manager
    
    def generate_plan(self, task: str, context: Optional[Dict] = None) -> List[Dict]:
        """
        Generate a step-by-step plan for completing the task.
        
        Args:
            task: The task description
            context: Optional context info (previous results, constraints, etc.)
            
        Returns:
            List of step objects with:
            - step: step number
            - action: what tool to use
            - args: arguments for tool
            - description: what this step accomplishes
        """
        logger.info(f"Generating plan for: {task}")
        
        # Simple planning - in production would use LLM
        plan = [
            {
                "step": 1,
                "action": "analyze",
                "description": f"Analyze task requirements: {task}",
            },
            {
                "step": 2,
                "action": "plan_breakdown", 
                "description": "Break into subtasks",
            },
            {
                "step": 3,
                "action": "search",
                "description": "Search for relevant information",
            },
            {
                "step": 4,
                "action": "synthesize",
                "description": "Synthesize results into answer",
            },
        ]
        
        return plan
    
    async def execute_plan(self, plan: List[Dict], parallel: bool = False) -> Dict:
        """
        Execute a plan, adapting as needed.
        
        Args:
            plan: List of steps to execute
            parallel: Whether to run independent steps in parallel
            
        Returns:
            Final answer and execution stats
        """
        results = []
        step_results = []
        
        for step_obj in plan:
            step = step_obj["step"]
            action = step_obj["action"]
            description = step_obj["description"]
            
            logger.info(f"Step {step}: {description}")
            
            # Execute step
            try:
                result = self._execute_step(action, step)
                results.append({
                    "step": step,
                    "action": action,
                    "success": True,
                    "result": result
                })
                step_results.append(result)
            except Exception as e:
                logger.warning(f"Step {step} failed: {e}")
                results.append({
                    "step": step,
                    "action": action,
                    "success": False,
                    "error": str(e)
                })
            
            # Adapt plan if needed
            if not step and results[-1]["success"]:
                results[-1].update({
                    "adapted": True,
                    "adaptation": f"Result from step {step} changed approach"
                })
        
        # Generate final answer
        if results and all(r["success"] for r in results):
            answer = "Answer synthesized from subtask results"
            return {
                "answer": answer,
                "steps_completed": len(step_results),
                "step_results": step_results
            }
        
        return {"error": "Step execution failed"}
    
    def _execute_step(self, action: str, step: int) -> Any:
        """
        Execute a single step of the plan.
        
        Args:
            action: The action to take ('search', 'synthesize', etc.)
            step: Step number for error messages
            
        Returns:
            Result of the action
        """
        if action == "analyze":
            return self.agent_manager.history_store.get_task_info()
            
        elif action == "search":
            # Use web_search or skill
            return tool_call({"tool": "web_search", "args": {"query": task}})
            
        elif action == "synthesize":
            # Combine results
            return {"answer": "Synthesized answer"}
        
        return None
