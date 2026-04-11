"""
TaskWatchdog — The Ralph Wiggum Loop.

Named after Ralph Wiggum's habit of narrating his current state unprompted.
Every running task gets a watchdog that:

  1. Fires periodic "check-in" prompts at the active agent mid-task.
  2. Collects the agent's self-report ("I'm still working on X…").
  3. Detects stalls (no output for N seconds) and pokes the agent.
  4. Detects loops (repeated identical output) and breaks them.
  5. Enforces step time-limits and can cancel + retry with a different model.
  6. Broadcasts status events over the WebSocket event bus.

The watchdog runs as an asyncio background task per active job.
Consumers (the frontend, other agents) subscribe to the event bus.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Optional

log = logging.getLogger("localclaw.ralph")

# ── Configuration ─────────────────────────────────────────────────────────────

CHECK_IN_INTERVAL   = 12     # seconds between automatic check-ins
STALL_TIMEOUT       = 45     # seconds of silence before "are you there?" poke
LOOP_DETECT_WINDOW  = 4      # compare last N outputs for repetition
MAX_RETRIES         = 3      # max poke-retries before escalation
TASK_HARD_LIMIT     = 600    # 10-minute hard kill on any task

# Ralph's check-in prompts — injected mid-task to elicit a status narration.
RALPH_PROMPTS = [
    "Continue executing. Call the next tool now — do not describe what you will do, just do it.",
    "Keep going. What is the next tool call? Output it immediately.",
    "Execute the next step now. One tool call, then wait for the result.",
    "Progress check: call the next required tool right now.",
    "Do not summarize. Execute. Output a tool call JSON block for the next action.",
    "Next tool call — go.",
]

STALL_POKES = [
    "You have gone quiet. Call a tool now to continue the task.",
    "No output detected. Resume by calling the next tool immediately.",
    "Still working? Execute the next step — output a tool call JSON block now.",
]

LOOP_BREAK_PROMPTS = [
    "You are repeating the same actions. Try a completely different tool or approach.",
    "Loop detected — stop what you are doing and take a different path to complete the task.",
    "Same result repeatedly. Change strategy: use a different tool or different arguments.",
]


# ── Data structures ───────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING      = "pending"
    RUNNING      = "running"
    CHECKING     = "checking"       # Ralph is checking in
    STALLED      = "stalled"
    LOOPING      = "looping"
    COMPLETED    = "completed"
    FAILED       = "failed"
    CANCELLED    = "cancelled"
    INTERRUPTED  = "interrupted"    # Was running when server shut down


@dataclass
class CheckIn:
    ts: float
    prompt: str
    response: str
    step: int


@dataclass
class TaskJob:
    job_id:     str
    agent_id:   str
    task:       str                    # short task description
    prompt:     str = ""              # full initial prompt text
    status:     TaskStatus = TaskStatus.PENDING
    created_at: float      = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at:   Optional[float] = None

    # Live state
    last_output_ts:  float = field(default_factory=time.time)
    last_output_hash: str  = ""
    repeat_count:    int   = 0
    poke_count:      int   = 0
    step:            int   = 0
    check_ins:       list[CheckIn] = field(default_factory=list)
    output_buffer:   list[str]     = field(default_factory=list)   # rolling last-N outputs
    result:          Optional[str] = None
    error:           Optional[str] = None
    model_used:      Optional[str] = None
    tokens_out:      int   = 0
    user_reply_queue: asyncio.Queue = field(default_factory=asyncio.Queue)

    def age(self) -> float:
        return time.time() - self.created_at

    def silence(self) -> float:
        return time.time() - self.last_output_ts

    def to_dict(self) -> dict:
        return {
            "job_id":      self.job_id,
            "agent_id":    self.agent_id,
            "task":        self.task,
            "prompt":      self.prompt,
            "status":      self.status,
            "created_at":  self.created_at,
            "started_at":  self.started_at,
            "ended_at":    self.ended_at,
            "age_s":       round(self.age(), 1),
            "silence_s":   round(self.silence(), 1),
            "step":        self.step,
            "poke_count":  self.poke_count,
            "check_ins":   len(self.check_ins),
            "model_used":  self.model_used,
            "result":      self.result,
            "error":       self.error,
        }


# ── Event bus ─────────────────────────────────────────────────────────────────

class EventBus:
    """Simple asyncio pub-sub for task status events."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}  # topic → queues

    def subscribe(self, topic: str = "*") -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.setdefault(topic, []).append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue, topic: str = "*"):
        for lst in self._subscribers.values():
            try:
                lst.remove(q)
            except ValueError:
                pass

    async def publish(self, topic: str, event: dict):
        event["topic"] = topic
        event["ts"]    = time.time()
        seen: set[int] = set()
        for lst in (
            self._subscribers.get(topic, []),
            self._subscribers.get("*", []),
        ):
            for q in list(lst):
                if id(q) in seen:
                    continue
                seen.add(id(q))
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # slow subscriber — drop


bus = EventBus()   # module-level singleton


# ── Watchdog ──────────────────────────────────────────────────────────────────

class TaskWatchdog:
    """
    The Ralph Loop for a single TaskJob.
    Runs as an asyncio.Task alongside the main inference coroutine.
    """

    def __init__(self, job: TaskJob, model_selector, agent_system_prompt: str = "", task_store=None):
        self.job   = job
        self.sel   = model_selector
        self.sys   = agent_system_prompt
        self._store = task_store
        self._stop = asyncio.Event()
        self._ralph_idx = 0
        self._poke_idx  = 0
        self._loop_idx  = 0

    async def run(self):
        """Main Ralph loop — runs until job finishes or hard-limit hit."""
        log.info(f"[Ralph] Watchdog started for job {self.job.job_id}")
        while not self._stop.is_set():
            # Wait for a user reply or time out and do a normal check-in cycle
            try:
                user_msg = await asyncio.wait_for(
                    self.job.user_reply_queue.get(), timeout=CHECK_IN_INTERVAL
                )
                if not self._stop.is_set():
                    await self._handle_user_reply(user_msg)
                continue
            except asyncio.TimeoutError:
                pass

            if self._stop.is_set():
                break

            job = self.job

            # Hard-limit
            if job.age() > TASK_HARD_LIMIT:
                await self._escalate("Hard time-limit exceeded")
                break

            # Job finished already
            if job.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                break

            # Stall detection
            if job.silence() > STALL_TIMEOUT:
                await self._poke_stall()
                continue

            # Loop detection
            if self._detect_loop():
                await self._break_loop()
                continue

            # Normal check-in
            await self._check_in()

    def stop(self):
        self._stop.set()

    async def _handle_user_reply(self, message: str):
        """Inject a user reply into the running task as a mid-task message."""
        log.info(f"[Ralph] User reply for job {self.job.job_id}: {message[:80]}")
        self.job.last_output_ts = time.time()  # reset stall timer

        await bus.publish(f"job.{self.job.job_id}", {
            "type":    "user_reply",
            "job_id":  self.job.job_id,
            "message": message,
        })

        prompt = (
            f"The user has sent you a message mid-task: {message}\n\n"
            "Acknowledge it briefly, then continue executing the task."
        )
        response = await self._ask(prompt)

        await bus.publish(f"job.{self.job.job_id}", {
            "type":     "user_reply_response",
            "job_id":   self.job.job_id,
            "message":  message,
            "response": response,
        })
        log.info(f"[Ralph] Reply response: {response[:120]}")

    async def record_output(self, text: str):
        """Called by the inference loop whenever new tokens arrive."""
        self.job.last_output_ts   = time.time()
        self.job.tokens_out      += len(text.split())
        self.job.step            += 1

        # Rolling hash window for loop detection
        h = hashlib.md5(text.strip().encode()).hexdigest()
        self.job.output_buffer.append(h)
        if len(self.job.output_buffer) > LOOP_DETECT_WINDOW * 2:
            self.job.output_buffer.pop(0)

        await bus.publish(f"job.{self.job.job_id}", {
            "type":    "delta",
            "job_id":  self.job.job_id,
            "content": text,
            "step":    self.job.step,
            "tokens":  self.job.tokens_out,
        })

    def _detect_loop(self) -> bool:
        buf = self.job.output_buffer
        if len(buf) < LOOP_DETECT_WINDOW:
            return False
        tail = buf[-LOOP_DETECT_WINDOW:]
        return len(set(tail)) == 1   # all identical hashes

    async def _check_in(self):
        prompt = RALPH_PROMPTS[self._ralph_idx % len(RALPH_PROMPTS)]
        self._ralph_idx += 1

        log.info(f"[Ralph] Check-in #{self._ralph_idx} for job {self.job.job_id}: «{prompt}»")
        self.job.status = TaskStatus.CHECKING

        await bus.publish(f"job.{self.job.job_id}", {
            "type":   "checkin",
            "job_id": self.job.job_id,
            "prompt": prompt,
        })

        response = await self._ask(prompt)
        check_in = CheckIn(
            ts=time.time(), prompt=prompt,
            response=response, step=self.job.step,
        )
        self.job.check_ins.append(check_in)
        self.job.status = TaskStatus.RUNNING

        if self._store:
            asyncio.create_task(self._store.save_checkin(
                self.job.job_id, check_in.ts, check_in.prompt, check_in.response, check_in.step
            ))
            asyncio.create_task(self._store.save_job(self.job))

        await bus.publish(f"job.{self.job.job_id}", {
            "type":     "checkin_response",
            "job_id":   self.job.job_id,
            "response": response,
            "step":     self.job.step,
        })
        log.info(f"[Ralph] Agent says: {response[:120]}")

    async def _poke_stall(self):
        prompt = STALL_POKES[self._poke_idx % len(STALL_POKES)]
        self._poke_idx += 1
        self.job.poke_count += 1

        log.warning(
            f"[Ralph] Stall detected on job {self.job.job_id} "
            f"({self.job.silence():.0f}s silence) — poking with: «{prompt}»"
        )
        self.job.status = TaskStatus.STALLED

        await bus.publish(f"job.{self.job.job_id}", {
            "type":    "stall",
            "job_id":  self.job.job_id,
            "silence": round(self.job.silence(), 1),
            "poke":    prompt,
        })

        response = await self._ask(prompt)
        self.job.last_output_ts = time.time()
        self.job.status = TaskStatus.RUNNING

        await bus.publish(f"job.{self.job.job_id}", {
            "type":     "poke_response",
            "job_id":   self.job.job_id,
            "response": response,
        })

        if self.job.poke_count >= MAX_RETRIES:
            await self._escalate(f"Failed to recover after {MAX_RETRIES} pokes")

    async def _break_loop(self):
        prompt = LOOP_BREAK_PROMPTS[self._loop_idx % len(LOOP_BREAK_PROMPTS)]
        self._loop_idx += 1

        log.warning(f"[Ralph] Loop detected on job {self.job.job_id} — breaking with: «{prompt}»")
        self.job.status = TaskStatus.LOOPING
        self.job.output_buffer.clear()

        await bus.publish(f"job.{self.job.job_id}", {
            "type":   "loop_break",
            "job_id": self.job.job_id,
            "prompt": prompt,
        })

        await self._ask(prompt)
        self.job.status = TaskStatus.RUNNING

    async def _escalate(self, reason: str):
        log.error(f"[Ralph] Escalating job {self.job.job_id}: {reason}")
        self.job.status = TaskStatus.FAILED
        self.job.error  = reason
        self.job.ended_at = time.time()
        self.stop()
        if self._store:
            asyncio.create_task(self._store.save_job(self.job))

        await bus.publish(f"job.{self.job.job_id}", {
            "type":   "escalated",
            "job_id": self.job.job_id,
            "reason": reason,
        })

    async def _ask(self, prompt: str) -> str:
        """Fire a quick single-turn request to the model with task context."""
        # Seed with the original task so the model knows what it's executing
        messages = []
        task_context = self.job.prompt or self.job.task
        if task_context:
            messages.append({"role": "user", "content": task_context})
            messages.append({"role": "assistant", "content": f"Understood. I am executing the task: {self.job.task}"})
        messages.append({"role": "user", "content": prompt})

        try:
            async for chunk in self.sel.generate(
                model=self.job.model_used or "llama3.2",
                messages=messages,
                system=self.sys or "You are a helpful AI agent narrating your progress.",
                stream=False,
            ):
                return chunk.get("message", {}).get("content", "")
        except Exception as e:
            log.warning(f"[Ralph] check-in request failed: {e}")
        return "(no response)"


# ── Task Registry ─────────────────────────────────────────────────────────────

class TaskRegistry:
    """
    Central registry of all TaskJobs.
    Integrates with AgentManager to wrap long-running tasks
    with a TaskWatchdog loop.
    """

    def __init__(self, model_selector, task_store=None):
        self.sel   = model_selector
        self._store = task_store
        self._jobs: dict[str, TaskJob] = {}
        self._watchdogs: dict[str, TaskWatchdog] = {}
        self._tasks: dict[str, asyncio.Task]     = {}

    async def initialize(self) -> None:
        """
        Load persisted jobs from the task store on startup.
        Running jobs that survived a crash are marked 'interrupted'.
        """
        if not self._store:
            return
        interrupted = await self._store.mark_interrupted()
        if interrupted:
            log.info(f"[Registry] Marked {interrupted} orphaned jobs as 'interrupted'")

        rows = await self._store.load_all_jobs()
        for row in rows:
            job = TaskJob(
                job_id     = row["job_id"],
                agent_id   = row["agent_id"],
                task       = row["task"],
                prompt     = row.get("prompt") or "",
                status     = TaskStatus(row["status"]) if row["status"] in TaskStatus._value2member_map_ else TaskStatus.FAILED,
                model_used = row.get("model_used"),
                result     = row.get("result"),
                error      = row.get("error"),
                created_at = row["created_at"],
                started_at = row.get("started_at"),
                ended_at   = row.get("ended_at"),
                poke_count = row.get("poke_count", 0),
                tokens_out = row.get("tokens_out", 0),
                step       = row.get("step", 0),
            )
            # Restore check-ins so the UI count is correct
            ci_rows = await self._store.load_checkins(job.job_id)
            for ci in ci_rows:
                job.check_ins.append(CheckIn(
                    ts=ci["ts"], prompt=ci["prompt"],
                    response=ci["response"], step=ci["step"],
                ))
            self._jobs[job.job_id] = job

        log.info(f"[Registry] Restored {len(rows)} jobs from task store")

    def create_job(self, agent_id: str, task: str, prompt: str = "") -> TaskJob:
        job_id = str(uuid.uuid4())[:8]
        job = TaskJob(job_id=job_id, agent_id=agent_id, task=task, prompt=prompt)
        self._jobs[job_id] = job
        if self._store:
            asyncio.create_task(self._store.save_job(job))
        return job

    async def run_job(
        self,
        job: TaskJob,
        inference_coro,
        system_prompt: str = "",
    ) -> AsyncGenerator[dict, None]:
        """
        Wrap an inference coroutine with the Ralph watchdog.

        Usage:
            async for event in registry.run_job(job, my_inference_gen(), sys):
                yield event
        """
        job.status     = TaskStatus.RUNNING
        job.started_at = time.time()

        # Determine which model to use (respect pre-selected model)
        if job.model_used:
            model, reason = job.model_used, "user selection"
        else:
            model, reason = await self.sel.select_model(job.task)
            job.model_used = model

        if self._store:
            asyncio.create_task(self._store.save_job(job))

        watchdog = TaskWatchdog(job, self.sel, system_prompt, task_store=self._store)
        self._watchdogs[job.job_id] = watchdog
        wd_task = asyncio.create_task(watchdog.run())
        self._tasks[job.job_id] = wd_task

        await bus.publish(f"job.{job.job_id}", {
            "type":   "started",
            "job_id": job.job_id,
            "task":   job.task,
            "model":  model,
            "reason": reason,
        })

        try:
            full_output = []
            async for chunk in inference_coro:
                text = chunk.get("content", "") or chunk.get("message", {}).get("content", "")
                if text:
                    full_output.append(text)
                    await watchdog.record_output(text)
                yield chunk

            job.result   = "".join(full_output)
            job.status   = TaskStatus.COMPLETED
            job.ended_at = time.time()

        except asyncio.CancelledError:
            job.status   = TaskStatus.CANCELLED
            job.ended_at = time.time()
            raise
        except Exception as e:
            job.status   = TaskStatus.FAILED
            job.error    = str(e)
            job.ended_at = time.time()
            raise
        finally:
            watchdog.stop()
            wd_task.cancel()
            try:
                await wd_task
            except asyncio.CancelledError:
                pass

            if self._store:
                asyncio.create_task(self._store.save_job(job))

            await bus.publish(f"job.{job.job_id}", {
                "type":      "finished",
                "job_id":    job.job_id,
                "status":    job.status,
                "result":    job.result,
                "check_ins": len(job.check_ins),
                "pokes":     job.poke_count,
                "tokens":    job.tokens_out,
                "elapsed_s": round(job.ended_at - job.started_at, 1) if job.ended_at and job.started_at else 0,
            })

    def get_job(self, job_id: str) -> Optional[TaskJob]:
        return self._jobs.get(job_id)

    def list_jobs(self, agent_id: Optional[str] = None) -> list[dict]:
        jobs = self._jobs.values()
        if agent_id:
            jobs = [j for j in jobs if j.agent_id == agent_id]
        return [j.to_dict() for j in jobs]

    async def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        wd = self._watchdogs.get(job_id)
        if wd:
            wd.stop()
        task = self._tasks.get(job_id)
        if task:
            task.cancel()
        job.status   = TaskStatus.CANCELLED
        job.ended_at = time.time()
        if self._store:
            asyncio.create_task(self._store.save_job(job))
        return True

    def get_check_ins(self, job_id: str) -> list[dict]:
        job = self._jobs.get(job_id)
        if not job:
            return []
        return [
            {
                "ts":       c.ts,
                "prompt":   c.prompt,
                "response": c.response,
                "step":     c.step,
            }
            for c in job.check_ins
        ]

    def summary(self) -> dict:
        all_jobs = list(self._jobs.values())
        return {
            "total":     len(all_jobs),
            "running":   sum(1 for j in all_jobs if j.status == TaskStatus.RUNNING),
            "completed": sum(1 for j in all_jobs if j.status == TaskStatus.COMPLETED),
            "stalled":   sum(1 for j in all_jobs if j.status == TaskStatus.STALLED),
            "failed":    sum(1 for j in all_jobs if j.status == TaskStatus.FAILED),
            "cancelled": sum(1 for j in all_jobs if j.status == TaskStatus.CANCELLED),
        }
