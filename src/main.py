"""
LocalClaw — OpenClaw-compatible local-first AI agent runtime.
Port 18798 | Ollama backend | GPU-aware | MCP memory | ClaWHub skills | Ralph loop
"""

import asyncio
import json
import logging
import time
import time as _time_module
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_manager import AgentManager
from gpu_manager import GPUManager
from history_store import ChatHistoryStore
from model_selector import ModelSelector
from skills.manager import SkillsManager
from memory.mcp_server import MCPMemoryServer
from memory.import_external import run_import as _import_external_memories
from personas.manager import PersonaManager
from task_watchdog import TaskRegistry, bus
from task_store import TaskStore
from telegram_bot import TelegramBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("localclaw")

# ── Token usage tracking ──────────────────────────────────────────────────────
_token_stats: dict = {
    "total_input": 0,
    "total_output": 0,
    "total_messages": 0,
    "by_model": {},
    "session_start": 0.0,
}

def _record_tokens(event: dict):
    model = event.get("model", "unknown")
    inp   = event.get("input_tokens", 0)
    out   = event.get("output_tokens", 0)
    _token_stats["total_input"]    += inp
    _token_stats["total_output"]   += out
    _token_stats["total_messages"] += 1
    if model not in _token_stats["by_model"]:
        _token_stats["by_model"][model] = {"input": 0, "output": 0, "messages": 0}
    _token_stats["by_model"][model]["input"]    += inp
    _token_stats["by_model"][model]["output"]   += out
    _token_stats["by_model"][model]["messages"] += 1

gpu_manager     = GPUManager()
model_selector  = ModelSelector(gpu_manager)
skills_manager  = SkillsManager()
memory_server   = MCPMemoryServer()
persona_manager = PersonaManager()
history_store   = ChatHistoryStore()
agent_manager   = AgentManager(
    model_selector=model_selector, skills_manager=skills_manager,
    memory_server=memory_server, persona_manager=persona_manager,
    gpu_manager=gpu_manager, history_store=history_store,
)
task_store     = TaskStore()
task_registry  = TaskRegistry(model_selector, task_store=task_store)
telegram_bot   = TelegramBot(agent_manager)


import os as _os
_HEARTBEAT_INTERVAL = int(_os.environ.get("HEARTBEAT_INTERVAL_S", 1800))
_HEARTBEAT_CHAT_ID  = _os.environ.get("HEARTBEAT_CHAT_ID", "").strip()

_MODEL_SETTINGS_PATH = _os.path.join(_os.path.dirname(__file__), "..", "data", "model_settings.json")

def _load_model_settings():
    try:
        with open(_MODEL_SETTINGS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_model_settings(data: dict):
    try:
        with open(_MODEL_SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save model settings: {e}")

async def _heartbeat_loop():
    """Send a periodic status ping via Telegram."""
    chat_id = int(_HEARTBEAT_CHAT_ID) if _HEARTBEAT_CHAT_ID else None
    if not chat_id:
        log.info("HEARTBEAT_CHAT_ID not set — heartbeat disabled")
        return
    log.info(f"Heartbeat enabled: every {_HEARTBEAT_INTERVAL}s → chat {chat_id}")
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            uptime_s = int(_time_module.time() - _token_stats["session_start"])
            h, remainder = divmod(uptime_s, 3600)
            m, s = divmod(remainder, 60)
            ollama = await model_selector.check_ollama()
            tasks  = task_registry.summary()
            msg = (
                f"*LocalClaw heartbeat*\n"
                f"Uptime: {h}h {m}m {s}s\n"
                f"Ollama: {'online' if ollama.get('online') else 'OFFLINE'}\n"
                f"Tasks: {tasks.get('running', 0)} running, "
                f"{tasks.get('completed', 0)} completed, "
                f"{tasks.get('failed', 0)} failed"
            )
            await telegram_bot.send_message(chat_id, msg)
            log.info("Heartbeat sent")
        except Exception as e:
            log.warning(f"Heartbeat error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _token_stats["session_start"] = _time_module.time()
    log.info("🦀 LocalClaw starting up...")
    await history_store.initialize()
    await task_store.initialize()
    await task_registry.initialize()
    await memory_server.initialize()
    await skills_manager.initialize()
    await persona_manager.initialize()
    await gpu_manager.start_monitoring()
    await telegram_bot.start()
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(_import_external_memories(memory_server))
    saved = _load_model_settings()
    if saved.get("preferred_model"):
        agent_manager.set_global_preferred_model(saved["preferred_model"])
        log.info(f"Restored preferred model: {saved['preferred_model']}")
    log.info("✅ LocalClaw ready on port 18798")
    yield
    log.info("🛑 Shutting down...")
    heartbeat_task.cancel()
    await telegram_bot.stop()
    await gpu_manager.stop_monitoring()
    await memory_server.close()
    await task_store.close()
    await history_store.close()


app = FastAPI(title="LocalClaw", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ── Models ───────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    agent_id: Optional[str] = None
    persona: Optional[str] = None
    task_hint: Optional[str] = None
    stream: bool = True
    context_id: Optional[str] = None

class TaskSubmitRequest(BaseModel):
    agent_id: str
    task: str
    messages: list[ChatMessage]
    model: Optional[str] = None
    task_hint: Optional[str] = None

class AgentCreateRequest(BaseModel):
    name: str
    persona_slug: Optional[str] = None
    skills: list[str] = []
    preferred_model: Optional[str] = None

class SkillInstallRequest(BaseModel):
    slug: str

class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 5
    agent_id: Optional[str] = None

class TaskReplyRequest(BaseModel):
    message: str


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok", "version": "1.0.0",
        "ollama": await model_selector.check_ollama(),
        "gpu": gpu_manager.get_status(),
        "agents": len(agent_manager.list_agents()),
        "skills": skills_manager.count(),
        "memory_entries": await memory_server.count(),
        "tasks": task_registry.summary(),
    }

@app.get("/status")
async def status():
    return {
        "gpu": gpu_manager.get_detailed_status(),
        "models": await model_selector.list_models(),
        "agents": agent_manager.list_agents(),
        "skills": skills_manager.list_skills(),
        "personas": persona_manager.list_personas(),
        "tasks": task_registry.summary(),
    }

@app.get("/tokens")
async def get_token_stats():
    return {
        **_token_stats,
        "uptime_s": round(_time_module.time() - _token_stats["session_start"]),
    }

@app.delete("/tokens")
async def reset_token_stats():
    _token_stats["total_input"]    = 0
    _token_stats["total_output"]   = 0
    _token_stats["total_messages"] = 0
    _token_stats["by_model"]       = {}
    _token_stats["session_start"]  = _time_module.time()
    return {"status": "reset"}


# ── Models ────────────────────────────────────────────────────────────────────

@app.get("/models")
async def list_models():
    return await model_selector.list_models()

@app.get("/models/recommend")
async def recommend_model(task: str = "chat"):
    model, reason = await model_selector.select_model(task)
    return {"model": model, "reason": reason, "task": task}

@app.post("/models/pull/{name}")
async def pull_model(name: str):
    return await model_selector.pull_model(name)

@app.get("/models/select/{name}")
async def select_model_globally(name: str):
    """Set a model as the globally preferred model for new agents."""
    agent_manager.set_global_preferred_model(name)
    _save_model_settings({"preferred_model": name})
    return {"preferred_model": name}

@app.get("/models/preferred")
async def get_global_preferred_model():
    """Get the globally preferred model."""
    return {"preferred_model": agent_manager._global_preferred_model}

@app.delete("/models/preferred")
async def clear_global_preferred_model():
    """Clear the globally preferred model (return to auto-selection)."""
    agent_manager._global_preferred_model = None
    # Also clear default agent's preference
    if "default" in agent_manager._agents:
        agent_manager._agents["default"].preferred_model = None
    _save_model_settings({"preferred_model": None})
    return {"preferred_model": None}


# ── GPU ───────────────────────────────────────────────────────────────────────

@app.get("/gpu")
async def gpu_status():
    return gpu_manager.get_detailed_status()

@app.get("/gpu/overflow")
async def gpu_overflow():
    return gpu_manager.get_overflow_status()

@app.post("/gpu/overflow/fix")
async def gpu_overflow_fix():
    """Immediately trigger overflow detection and correction."""
    await gpu_manager._check_and_correct_overflow()
    return gpu_manager.get_overflow_status()

@app.post("/gpu/optimize")
async def gpu_optimize():
    return await gpu_manager.optimize()


# ── Agents ────────────────────────────────────────────────────────────────────

@app.get("/agents")
async def list_agents():
    return agent_manager.list_agents()

@app.post("/agents")
async def create_agent(req: AgentCreateRequest):
    return await agent_manager.create_agent(
        name=req.name, persona_slug=req.persona_slug,
        skills=req.skills, preferred_model=req.preferred_model,
    )

@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    agent = agent_manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent

@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    ok = agent_manager.delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"deleted": agent_id}

@app.post("/agents/{agent_id}/reset")
async def reset_agent(agent_id: str):
    ok = await agent_manager.reset_agent(agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"reset": agent_id}


class SetModelRequest(BaseModel):
    model: Optional[str] = None

@app.put("/agents/{agent_id}/model")
async def set_agent_model(agent_id: str, req: SetModelRequest):
    """Set the preferred model for an agent. Pass null to clear."""
    ok = await agent_manager.set_preferred_model(agent_id, req.model)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"agent_id": agent_id, "preferred_model": req.model}


# ── Tools ─────────────────────────────────────────────────────────────────────

@app.get("/tools")
async def list_tools():
    """List all available tools."""
    return agent_manager.tool_registry.list_tools()


# ── Chat history ─────────────────────────────────────────────────────────────

@app.get("/history/{agent_id}")
async def get_history(agent_id: str, limit: int = 100):
    """Return persisted chat history for an agent (oldest-first)."""
    rows = await history_store.load(agent_id, limit=limit)
    return {"agent_id": agent_id, "messages": rows, "count": len(rows)}

@app.delete("/history/{agent_id}")
async def clear_history(agent_id: str):
    """Permanently delete the stored history log for an agent (also resets context)."""
    n = await agent_manager.clear_agent_history(agent_id)
    return {"agent_id": agent_id, "deleted": n}

@app.get("/history")
async def list_history_agents():
    """List all agents that have stored history."""
    return await history_store.list_agents()


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]
    return await agent_manager.chat(
        agent_id=req.agent_id or "default",
        messages=messages, task_hint=req.task_hint,
        persona=req.persona, context_id=req.context_id,
    )

@app.websocket("/ws/chat/{agent_id}")
async def ws_chat(websocket: WebSocket, agent_id: str):
    await websocket.accept()
    log.info(f"[WS] Client connected to agent: {agent_id}")
    try:
        while True:
            raw = await websocket.receive_text()
            payload = json.loads(raw)

            # Handle cancel message (client wants to stop current generation)
            if payload.get("type") == "cancel":
                log.info(f"[WS] Cancel received for agent: {agent_id}")
                continue

            log.info(f"[WS] Received message for {agent_id}: {len(payload.get('messages', []))} messages")
            cancel = asyncio.Event()
            chunk_count = 0
            stream_done = asyncio.Event()

            async def _stream():
                nonlocal chunk_count
                try:
                    async for chunk in agent_manager.stream_chat(
                        agent_id=agent_id,
                        messages=payload.get("messages", []),
                        task_hint=payload.get("task_hint"),
                        context_id=payload.get("context_id"),
                        model_override=payload.get("model") or None,
                        num_ctx=payload.get("num_ctx") or None,
                        chat_only=payload.get("chat_only", False),
                        cancel_event=cancel,
                    ):
                        if cancel.is_set():
                            await websocket.send_text(json.dumps({"type": "cancelled"}))
                            break
                        chunk_count += 1
                        if chunk.get("type") == "tokens":
                            _record_tokens(chunk)
                        await websocket.send_text(json.dumps(chunk))
                except Exception as e:
                    log.error(f"[WS] Stream error: {e}", exc_info=True)
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                    except Exception:
                        pass
                finally:
                    if not cancel.is_set():
                        await websocket.send_text(json.dumps({"type": "done"}))
                    stream_done.set()

            stream_task = asyncio.create_task(_stream())

            # Listen for cancel messages while streaming
            try:
                while not stream_done.is_set():
                    try:
                        msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                        msg_payload = json.loads(msg)
                        if msg_payload.get("type") == "cancel":
                            log.info(f"[WS] Cancelling stream for agent: {agent_id}")
                            cancel.set()
                            # Wait for stream to finish cleanly
                            await asyncio.wait_for(stream_task, timeout=5.0)
                            break
                    except asyncio.TimeoutError:
                        continue
                    except WebSocketDisconnect:
                        cancel.set()
                        stream_task.cancel()
                        return
            except WebSocketDisconnect:
                cancel.set()
                stream_task.cancel()
                return
            except Exception:
                cancel.set()

    except WebSocketDisconnect:
        log.info(f"[WS] Client disconnected from agent: {agent_id}")


# ── Tasks — Ralph Wiggum Loop ─────────────────────────────────────────────────

@app.post("/tasks")
async def submit_task(req: TaskSubmitRequest):
    """
    Submit a task to an agent with the Ralph Wiggum watchdog enabled.
    The watchdog fires periodic check-in prompts, detects stalls and loops,
    and emits events over the event bus.
    Returns a job_id immediately; stream events via /ws/tasks/{job_id}.
    """
    messages = [m.model_dump() for m in req.messages]
    initial_prompt = req.messages[0].content if req.messages else ""
    job = task_registry.create_job(agent_id=req.agent_id, task=req.task, prompt=initial_prompt)
    if req.model:
        job.model_used = req.model
    system_prompt = await agent_manager.build_system_prompt_for_job(req.agent_id, task=req.task)

    async def _run():
        try:
            stream = agent_manager.stream_chat(
                agent_id=req.agent_id, messages=messages, task_hint=req.task_hint,
                model_override=req.model,
            )
            async for _ in task_registry.run_job(job, stream, system_prompt):
                pass
        except Exception as e:
            log.error(f"[Ralph] Task _run() crashed for job {job.job_id}: {e}", exc_info=True)
            job.status = "failed"
            job.error = str(e)
            import time as _time; job.ended_at = _time.time()
            await bus.publish(f"job.{job.job_id}", {
                "type": "finished", "job_id": job.job_id,
                "status": "failed", "result": None,
            })

    asyncio.create_task(_run())
    log.info(f"[Ralph] Task submitted job={job.job_id} agent={req.agent_id}")
    return {
        "job_id": job.job_id, "agent_id": req.agent_id,
        "task": req.task, "prompt": job.prompt, "status": job.status,
        "ws_url": f"ws://<host>:18798/ws/tasks/{job.job_id}",
    }

@app.get("/tasks")
async def list_tasks(agent_id: Optional[str] = None):
    return task_registry.list_jobs(agent_id=agent_id)

@app.get("/tasks/summary")
async def tasks_summary():
    return task_registry.summary()

@app.get("/tasks/{job_id}")
async def get_task(job_id: str):
    job = task_registry.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()

@app.get("/tasks/{job_id}/checkins")
async def get_task_checkins(job_id: str):
    job = task_registry.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return task_registry.get_check_ins(job_id)

@app.post("/tasks/{job_id}/reply")
async def reply_to_task(job_id: str, req: TaskReplyRequest):
    """Send a mid-task message to a running job. The Ralph watchdog will inject it."""
    from task_watchdog import TaskStatus
    job = task_registry.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    active = {TaskStatus.RUNNING, TaskStatus.CHECKING, TaskStatus.STALLED, TaskStatus.LOOPING}
    if job.status not in active:
        raise HTTPException(400, f"Job is not active (status: {job.status})")
    await job.user_reply_queue.put(req.message)
    return {"job_id": job_id, "queued": True}

@app.delete("/tasks/{job_id}")
async def cancel_task(job_id: str):
    ok = await task_registry.cancel_job(job_id)
    if not ok:
        raise HTTPException(404, "Job not found")
    return {"cancelled": job_id}

@app.websocket("/ws/tasks/{job_id}")
async def ws_task_events(websocket: WebSocket, job_id: str):
    """Stream Ralph loop events for a specific task job."""
    from task_watchdog import TaskStatus
    await websocket.accept()
    q = bus.subscribe(f"job.{job_id}")
    try:
        job = task_registry.get_job(job_id)
        if job:
            # Always send current state first so the client can catch up
            await websocket.send_text(json.dumps({"type": "state", **job.to_dict()}))
            # If the job is already done, send a finished event immediately —
            # the real finished event was published before this WS existed and was lost
            terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            if job.status in terminal:
                await websocket.send_text(json.dumps({
                    "type":      "finished",
                    "job_id":    job_id,
                    "status":    job.status,
                    "result":    job.result,
                    "error":     job.error,
                    "check_ins": len(job.check_ins),
                    "pokes":     job.poke_count,
                    "tokens":    job.tokens_out,
                    "elapsed_s": round(job.ended_at - job.started_at, 1)
                                 if job.ended_at and job.started_at else 0,
                }))
                return  # nothing left to stream
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(json.dumps(event))
                if event.get("type") in ("finished", "escalated"):
                    break
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping", "job_id": job_id}))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q, f"job.{job_id}")

@app.websocket("/ws/events")
async def ws_all_events(websocket: WebSocket):
    """Firehose — all task events across all agents."""
    await websocket.accept()
    q = bus.subscribe("*")
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(q, "*")


# ── Skills ────────────────────────────────────────────────────────────────────

@app.get("/skills")
async def list_skills():
    return skills_manager.list_skills()

@app.get("/skills/search")
async def search_skills(q: str, limit: int = 10):
    return await skills_manager.search_clawhub(q, limit)

@app.post("/skills/install")
async def install_skill(req: SkillInstallRequest):
    return await skills_manager.install_from_clawhub(req.slug)

@app.delete("/skills/{slug}")
async def uninstall_skill(slug: str):
    return skills_manager.uninstall(slug)

@app.get("/skills/{slug}")
async def get_skill(slug: str):
    skill = skills_manager.get_skill(slug)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


# ── Personas ──────────────────────────────────────────────────────────────────

@app.get("/personas")
async def list_personas():
    return persona_manager.list_personas()

@app.get("/personas/search")
async def search_personas(q: str, limit: int = 10):
    return await persona_manager.search_souls(q, limit)

@app.post("/personas/install/{slug}")
async def install_persona(slug: str):
    return await persona_manager.install_soul(slug)

@app.get("/personas/{slug}")
async def get_persona(slug: str):
    persona = persona_manager.get_persona(slug)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


# ── Memory (MCP) ──────────────────────────────────────────────────────────────

@app.get("/memory")
async def list_memories(agent_id: Optional[str] = None, limit: int = 20):
    return await memory_server.list_memories(agent_id=agent_id, limit=limit)

@app.post("/memory/search")
async def search_memory(req: MemorySearchRequest):
    return await memory_server.search(req.query, limit=req.limit, agent_id=req.agent_id)

@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    ok = await memory_server.delete(memory_id)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"deleted": memory_id}

@app.get("/memory/export")
async def export_memories():
    return await memory_server.export_all()

@app.post("/mcp")
async def mcp_endpoint(payload: dict):
    return await memory_server.handle_mcp_request(payload)


# ── Communication ────────────────────────────────────────────────────────────

class TelegramConfigRequest(BaseModel):
    token: str = ""
    allowed_users: str = ""

@app.get("/comms/status")
async def comms_status():
    return telegram_bot.get_status()

@app.post("/comms/telegram")
async def comms_telegram_save(req: TelegramConfigRequest):
    result = await telegram_bot.restart(token=req.token, allowed_users=req.allowed_users)
    return {**result, **telegram_bot.get_status()}

@app.post("/comms/telegram/stop")
async def comms_telegram_stop():
    await telegram_bot.stop()
    return telegram_bot.get_status()

@app.post("/comms/telegram/start")
async def comms_telegram_start():
    result = await telegram_bot.start()
    return {**result, **telegram_bot.get_status()}


# ── App Update ────────────────────────────────────────────────────────────────

_APP_VERSION_FILE = _os.environ.get(
    "APP_VERSION_FILE",
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "app_version.json"),
)
_APK_FILE = _os.environ.get(
    "APK_FILE",
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "LocalCrab-app-debug.apk"),
)


def _read_app_version():
    """Read app version info from app_version.json, or infer from APK file."""
    if _os.path.exists(_APP_VERSION_FILE):
        try:
            with open(_APP_VERSION_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback: check if APK exists and return basic info
    if _os.path.exists(_APK_FILE):
        import time as _t
        mtime = _os.path.getmtime(_APK_FILE)
        size = _os.path.getsize(_APK_FILE)
        return {
            "versionCode": 1,
            "versionName": "1.0.0",
            "changelog": "Bug fixes and improvements",
            "updated": _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(mtime)),
            "size": size,
        }
    return None


@app.get("/app/update")
async def app_update_check(current_version_code: int = 0):
    """Check for app updates. Returns latest version info if newer than current."""
    info = _read_app_version()
    if info is None:
        raise HTTPException(status_code=404, detail="No APK available")
    if info.get("versionCode", 0) > current_version_code:
        info["downloadUrl"] = "/app/download"
        info["updateAvailable"] = True
    else:
        info["updateAvailable"] = False
    return info


@app.get("/app/download")
async def app_download():
    """Download the latest APK file."""
    if not _os.path.exists(_APK_FILE):
        raise HTTPException(status_code=404, detail="APK file not found")
    return FileResponse(
        _APK_FILE,
        media_type="application/vnd.android.package-archive",
        filename="LocalCrab-app-debug.apk",
    )


# ── Frontend ──────────────────────────────────────────────────────────────────

_FRONTEND_DIR = _os.environ.get(
    "FRONTEND_DIR",
    _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "frontend"),
)

@app.get("/", response_class=HTMLResponse)
async def root():
    try:
        with open(_os.path.join(_FRONTEND_DIR, "index.html")) as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>LocalClaw</h1><p>API docs: <a href='/docs'>/docs</a></p>"

try:
    app.mount("/static", StaticFiles(directory=_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "frontend")), name="static")
except Exception:
    pass

# ── Improved page ──
@app.get("/improved")
async def improved_page():
    """Serve the improvement monitor page."""
    try:
        with open(_os.path.join(_os.path.dirname(__file__), "../improved.html")) as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse('''<h1>Noimproved page Yet</h1><p>Scheduled for next release.</p>''')

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=18798, reload=False,
                ws_ping_interval=30, ws_ping_timeout=10)
