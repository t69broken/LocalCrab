"""
Task Persistence and Recovery Module for LocalCrab
=============================================

Ensures tasks survive server restarts or interruptions by:
1. Saving task state periodically
2. Storing conversation history to disk
3. Resuming tasks from last checkpoint on restart
4. Detecting stale tasks and cleaning up

Design principle from autoresearch:
- Persistent state survives failures
- Graceful recovery from interruptions
- No data loss on crashes
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from task_monitor import TaskRegistry, TaskJob, TaskStatus

logger = logging.getLogger(__name__)

class TaskPersistenceManager:
    """
    Manages persistent storage and recovery of tasks.
    """
    
    def __init__(
        self,
        task_registry: TaskRegistry,
        checkpoint_interval: int = 30,  # seconds
        checkpoints_dir: str = "/home/tyson/ClaudeLocalClaw/localclaw/data/checkpoints"
    ):
        self.task_registry = task_registry
        self.checkpoint_interval = checkpoint_interval
        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        
        # Background checkpoint writer
        self._checkpoint_task = None
    
    async def start(self):
        """Start background checkpointing."""
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())
    
    async def _checkpoint_loop(self):
        """Periodically save task state."""
        while True:
            try:
                checkpoint_now()
                elapsed = time.time() - self._checkpoint_loop_start
                if elapsed >= self.checkpoint_interval:
                    await asyncio.sleep(self.checkpoint_interval)
            except asyncio.CancelledError:
                break
    
    async def save_task_checkpoint(
        self,
        job_id: str,
        task: str,
        status: TaskStatus,
        step: int,
        last_output: Optional[str] = None,
        token_usage: Optional[Dict] = None
    ):
        """
        Save current state of a task for recovery.
        
        Args:
            job_id: Task identifier
            task: Task description
            status: Current task status
            step: Step count
            last_output: Latest output/error
            token_usage: Token usage stats
        """
        
        checkpoint_file = self.checkpoints_dir / f"{job_id}.json"
        
        checkpoint = {
            "job_id": job_id,
            "task": task,
            "status": status.value,
            "step": step,
            "saved_at": datetime.now().isoformat(),
            "last_output": last_output,
            "token_usage": token_usage
        }
        
        # Write checkpoint
        try:
            with open(checkpoint_file, "w") as f:
                json.dump(checkpoint, f, indent=2)
            
            logger.info(f"Checkpoint saved: {job_id} (step {step}, status {status})")
            
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            return False
        
        return True
    
    async def save_task(self) -> List[str]:
        """Save checkpoints for all in-progress tasks."""
        pending_tasks = self.task_registry.pending_tasks()
        checkpoints_saved = 0
        
        for task in pending_tasks:
            if task.status == TaskStatus.RUNNING:
                filepath = self.checkpoints_dir / f"{task.job_id}.json"
                
                checkpoint = {
                    "job_id": task.job_id,
                    "task": task.task,
                    "status": task.status.value,
                    "step": task.step,
                    "last_output": task.last_output or "",
                    "silence": task.silence()
                }
                
                with open(filepath, "w") as f:
                    json.dump(checkpoint, f, indent=2)
                
                checkpoints_saved += 1
        
        if checkpoints_saved:
            logger.info(f"Saved {checkpoints_saved} checkpoints")
        
        return [f"Saved {checkpoints_saved} checkpoints"]
    
    async def load_checkpoint(self, job_id: str) -> Optional[Dict]:
        """
        Load checkpoint for a task.
        
        Args:
            job_id: Task ID
            
        Returns:
            Checkpoint dict or None
        """
        
        checkpoint_file = self.checkpoints_dir / f"{job_id}.json"
        
        if not checkpoint_file.exists():
            logger.warning(f"No checkpoint found for {job_id}")
            return None
        
        try:
            with open(checkpoint_file) as f:
                checkpoint = json.load(f)
            
            return checkpoint
            
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return None
    
    async def recover_tasks(self) -> List[str]:
        """
        Recover in-progress tasks from saved checkpoints.
        
        Returns:
            List of status messages
        """
        
        messages = []
        
        # Load all checkpoints
        checkpoints = list(self.checkpoints_dir.glob("*.json"))
        
        for checkpoint_file in checkpoints:
            try:
                checkpoint = json.loads(checkpoint_file.read_text())
                job_id = checkpoint["job_id"]
                task = checkpoint["task"]
                status = TaskStatus(checkpoint.get("status", "running"))
                step = checkpoint.get("step", 0)
                
                # Find or create task
                task_obj = self.task_registry.find_task(job_id)
                
                if task_obj:
                    # Restore task state
                    task_obj.task = task
                    task_obj.status = status
                    task_obj.step = step
                    task_obj.last_output = checkpoint.get("last_output")
                    
                    logger.info(f"Recovered task: {job_id} (step {step})")
                    messages.append(f"Recovered: {job_id} (step {step})")
                
                # Clean up old checkpoint
                checkpoint_file.unlink()
                
            except Exception as e:
                logger.error(f"Failed to recover checkpoint: {e}")
        
        return messages
    
    async def cleanup_old_checkpoints(self, max_age_hours: int = 24):
        """
        Remove checkpoints older than max_age_hours.
        """
        
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        files_to_remove = []
        
        for f in self.checkpoints_dir.glob("*.json"):
            stat = f.stat()
            modified = datetime.fromtimestamp(stat.st_mtime)
            
            if modified < cutoff:
                files_to_remove.append(f)
        
        for f in files_to_remove:
            try:
                f.unlink()
            except Exception as e:
                logger.error(f"Failed to remove old checkpoint: {e}")
        
        logger.info(f"Cleaned up {len(files_to_remove)} old checkpoints")
