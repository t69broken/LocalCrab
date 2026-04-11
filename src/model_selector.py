"""
ModelSelector — Intelligently routes tasks to the best locally-available Ollama model.

Strategy:
  1. Score all pulled models by their known capability profile vs. the task type.
  2. Filter to models that fit in available VRAM.
  3. Return the highest-scoring fit.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("localclaw.model_selector")

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

# ── Model capability profiles ────────────────────────────────────────────────
# Each profile maps task categories to a score 0-10.
# Models not in this table get a generic score of 5 for everything.

MODEL_PROFILES: dict[str, dict] = {
    # Coding / reasoning heavyweights
    "deepseek-coder-v2": {"coding": 10, "reasoning": 9, "math": 9, "analysis": 8, "chat": 6, "creative": 4, "vram_gb": 15},
    "deepseek-coder-v2:16b": {"coding": 10, "reasoning": 9, "math": 9, "analysis": 8, "chat": 6, "creative": 4, "vram_gb": 10},
    "deepseek-r1": {"coding": 9, "reasoning": 10, "math": 10, "analysis": 9, "chat": 7, "creative": 5, "vram_gb": 20},
    "deepseek-r1:7b": {"coding": 8, "reasoning": 9, "math": 9, "analysis": 8, "chat": 7, "creative": 5, "vram_gb": 5},
    "deepseek-r1:14b": {"coding": 9, "reasoning": 10, "math": 10, "analysis": 9, "chat": 7, "creative": 5, "vram_gb": 9},
    "qwen2.5-coder": {"coding": 9, "reasoning": 8, "math": 8, "analysis": 8, "chat": 6, "creative": 4, "vram_gb": 5},
    "qwen2.5-coder:7b": {"coding": 9, "reasoning": 8, "math": 8, "analysis": 8, "chat": 6, "creative": 4, "vram_gb": 5},
    "qwen2.5-coder:14b": {"coding": 10, "reasoning": 9, "math": 9, "analysis": 9, "chat": 7, "creative": 5, "vram_gb": 9},
    "codellama": {"coding": 8, "reasoning": 6, "math": 7, "analysis": 6, "chat": 5, "creative": 3, "vram_gb": 4},
    "codellama:13b": {"coding": 9, "reasoning": 7, "math": 8, "analysis": 7, "chat": 6, "creative": 4, "vram_gb": 8},
    # General purpose / chat
    "llama3.2": {"coding": 6, "reasoning": 7, "math": 6, "analysis": 7, "chat": 9, "creative": 8, "vram_gb": 2},
    "llama3.2:1b": {"coding": 4, "reasoning": 5, "math": 4, "analysis": 5, "chat": 7, "creative": 6, "vram_gb": 1},
    "llama3.2:3b": {"coding": 5, "reasoning": 6, "math": 5, "analysis": 6, "chat": 8, "creative": 7, "vram_gb": 2},
    "llama3.1": {"coding": 7, "reasoning": 8, "math": 7, "analysis": 8, "chat": 9, "creative": 8, "vram_gb": 5},
    "llama3.1:8b": {"coding": 7, "reasoning": 8, "math": 7, "analysis": 8, "chat": 9, "creative": 8, "vram_gb": 5},
    "llama3.1:70b": {"coding": 9, "reasoning": 9, "math": 9, "analysis": 9, "chat": 10, "creative": 9, "vram_gb": 40},
    "llama3.3": {"coding": 8, "reasoning": 9, "math": 8, "analysis": 9, "chat": 9, "creative": 8, "vram_gb": 5},
    "llama3.3:70b": {"coding": 9, "reasoning": 10, "math": 9, "analysis": 10, "chat": 10, "creative": 9, "vram_gb": 40},
    "mistral": {"coding": 7, "reasoning": 7, "math": 6, "analysis": 7, "chat": 8, "creative": 7, "vram_gb": 4},
    "mistral-nemo": {"coding": 7, "reasoning": 8, "math": 7, "analysis": 8, "chat": 8, "creative": 7, "vram_gb": 7},
    "mistral-small": {"coding": 7, "reasoning": 8, "math": 7, "analysis": 8, "chat": 8, "creative": 7, "vram_gb": 12},
    "mixtral": {"coding": 8, "reasoning": 8, "math": 8, "analysis": 8, "chat": 9, "creative": 8, "vram_gb": 26},
    "mixtral:8x7b": {"coding": 8, "reasoning": 8, "math": 8, "analysis": 8, "chat": 9, "creative": 8, "vram_gb": 26},
    "phi4": {"coding": 8, "reasoning": 9, "math": 9, "analysis": 8, "chat": 8, "creative": 7, "vram_gb": 9},
    "phi3.5": {"coding": 7, "reasoning": 8, "math": 8, "analysis": 7, "chat": 7, "creative": 6, "vram_gb": 2},
    "gemma2": {"coding": 7, "reasoning": 8, "math": 7, "analysis": 8, "chat": 8, "creative": 8, "vram_gb": 5},
    "gemma2:27b": {"coding": 8, "reasoning": 9, "math": 8, "analysis": 9, "chat": 9, "creative": 9, "vram_gb": 16},
    "gemma3": {"coding": 8, "reasoning": 8, "math": 8, "analysis": 8, "chat": 9, "creative": 8, "vram_gb": 5},
    "qwen2.5": {"coding": 8, "reasoning": 9, "math": 9, "analysis": 9, "chat": 9, "creative": 7, "vram_gb": 5},
    "qwen2.5:72b": {"coding": 9, "reasoning": 10, "math": 10, "analysis": 10, "chat": 10, "creative": 8, "vram_gb": 44},
    # Creative / writing
    "solar": {"coding": 5, "reasoning": 6, "math": 5, "analysis": 6, "chat": 8, "creative": 9, "vram_gb": 6},
    "orca-mini": {"coding": 5, "reasoning": 6, "math": 5, "analysis": 6, "chat": 7, "creative": 6, "vram_gb": 2},
    "neural-chat": {"coding": 5, "reasoning": 6, "math": 5, "analysis": 6, "chat": 8, "creative": 8, "vram_gb": 4},
    "starling-lm": {"coding": 6, "reasoning": 7, "math": 6, "analysis": 7, "chat": 8, "creative": 8, "vram_gb": 4},
    # Small / fast
    "tinyllama": {"coding": 3, "reasoning": 3, "math": 3, "analysis": 3, "chat": 5, "creative": 4, "vram_gb": 1},
    "smollm2": {"coding": 4, "reasoning": 4, "math": 4, "analysis": 4, "chat": 6, "creative": 5, "vram_gb": 1},
}

# Task aliases
TASK_ALIASES = {
    "code": "coding",
    "dev": "coding",
    "programming": "coding",
    "debug": "coding",
    "math": "math",
    "logic": "reasoning",
    "think": "reasoning",
    "write": "creative",
    "story": "creative",
    "poem": "creative",
    "summarize": "analysis",
    "analyze": "analysis",
    "research": "analysis",
    "explain": "chat",
    "question": "chat",
    "general": "chat",
}

VALID_TASKS = {"coding", "reasoning", "math", "analysis", "chat", "creative"}


class ModelSelector:
    def __init__(self, gpu_manager=None):
        self.gpu_manager = gpu_manager
        self._models_cache: list[dict] = []
        self._cache_time: float = 0

    async def check_ollama(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{OLLAMA_BASE}/api/ps")
                if r.status_code == 200:
                    return {"online": True}
                return {"online": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            log.warning(f"check_ollama exception ({type(e).__name__}): {e!r}")
            return {"online": False, "error": f"{type(e).__name__}: {e}"}

    async def list_models(self) -> list[dict]:
        """Fetch pulled models from Ollama with enriched metadata."""
        import time as _time
        now = _time.time()
        if now - self._cache_time < 30 and self._models_cache:
            return self._models_cache

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{OLLAMA_BASE}/api/tags")
                data = r.json()
        except Exception as e:
            log.warning(f"Cannot reach Ollama: {e}")
            return []

        models = []
        for m in data.get("models", []):
            name = m["name"]
            profile = self._get_profile(name)
            is_cloud = bool(m.get("remote_host")) or ":cloud" in name
            models.append({
                "name": name,
                "size_bytes": m.get("size", 0),
                "size_gb": round(m.get("size", 0) / 1e9, 1),
                "modified_at": m.get("modified_at"),
                "capabilities": {k: v for k, v in profile.items() if k in VALID_TASKS},
                "vram_estimate_gb": profile.get("vram_gb", round(m.get("size", 0) / 1e9 * 1.2, 1)),
                "cloud": is_cloud,
            })

        self._models_cache = models
        self._cache_time = now
        return models

    def _get_profile(self, name: str) -> dict:
        """Find profile for a model name, falling back to partial matches."""
        if name in MODEL_PROFILES:
            return MODEL_PROFILES[name]
        # Try stripping tag
        base = name.split(":")[0]
        if base in MODEL_PROFILES:
            return MODEL_PROFILES[base]
        # Try prefix matching
        for k, v in MODEL_PROFILES.items():
            if name.startswith(k.split(":")[0]):
                return v
        return {"coding": 5, "reasoning": 5, "math": 5, "analysis": 5, "chat": 5, "creative": 5, "vram_gb": 4}

    async def select_model(
        self,
        task: str = "chat",
        preferred: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Return the preferred model if set and available, otherwise the first
        available model.  Auto-scoring is disabled — model selection is explicit.
        """
        models = await self.list_models()
        if not models:
            return "llama3.2", "fallback — no models available"

        available_names = {m["name"] for m in models}
        log.info(f"[ModelSelector] preferred={preferred}, available={list(available_names)[:5]}...")

        if preferred and preferred in available_names:
            log.info(f"[ModelSelector] Using preferred model: {preferred}")
            return preferred, "user selection"

        # No explicit selection — fall back to first local (non-cloud) model
        local_models = [m for m in models if ":cloud" not in m["name"] and not m["name"].endswith(":cloud")]
        first = (local_models[0] if local_models else models[0])["name"]
        log.info(f"[ModelSelector] No model selected, using first local model: {first}")
        return first, "first available (no model selected)"

    async def pull_model(self, name: str) -> dict:
        """Trigger Ollama to pull a model."""
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                r = await client.post(
                    f"{OLLAMA_BASE}/api/pull",
                    json={"name": name, "stream": False},
                )
                return {"status": "ok", "model": name, "response": r.json()}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _compute_options(
        self,
        model: str,
        overrides: Optional[dict],
        force_ctx: Optional[int] = None,
    ) -> dict:
        """
        Build Ollama options.  force_ctx overrides the VRAM-based num_ctx
        calculation; caller-supplied overrides dict wins over everything else.
        """
        opts: dict = {}
        if self.gpu_manager:
            profile = self._get_profile(model)
            model_vram_gb = profile.get("vram_gb", 4.0)
            gpu_opts = self.gpu_manager.recommend_ollama_options(model_vram_gb)
            opts.update(gpu_opts)
            status = self.gpu_manager.get_status()
            free_gb = status.get("free_vram_gb", 0.0)
            if free_gb > 6:
                opts["num_ctx"] = 8192
            elif free_gb > 3:
                opts["num_ctx"] = 4096
            elif free_gb > 1.5:
                opts["num_ctx"] = 2048
            elif free_gb > 0:
                opts["num_ctx"] = 1024
            # If free_gb == 0 the GPU is not visible to this process (the app
            # container doesn't have GPU passthrough, but Ollama runs on the
            # host with full GPU access).  Use 8192 — enough for tool schemas
            # and conversation history without blowing out VRAM.
            else:
                opts["num_ctx"] = 8192
            log.debug(
                f"[GPU] {model}: num_gpu={opts.get('num_gpu')}, "
                f"num_ctx={opts.get('num_ctx')}, free_vram={free_gb:.1f}GB"
            )
        if force_ctx:
            opts["num_ctx"] = force_ctx  # user-specified context size wins over VRAM heuristic
        opts.update(overrides or {})   # explicit overrides dict wins over everything
        return opts

    async def generate(
        self,
        model: str,
        messages: list[dict],
        system: Optional[str] = None,
        stream: bool = False,
        options: Optional[dict] = None,
        tools: Optional[list[dict]] = None,
        num_ctx: Optional[int] = None,
    ):
        """Call Ollama chat API with optional tool support."""
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": self._compute_options(model, options, force_ctx=num_ctx),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=300) as client:
            if stream:
                # Try with tools first, fall back without tools on error
                try:
                    async with client.stream(
                        "POST",
                        f"{OLLAMA_BASE}/api/chat",
                        json=payload,
                    ) as response:
                        # Check for error status
                        if response.status_code >= 400:
                            error_body = await response.aread()
                            log.warning(f"Ollama returned {response.status_code}, retrying without tools")
                            if tools:
                                # Retry without tools
                                payload_no_tools = {k: v for k, v in payload.items() if k != "tools"}
                                async with client.stream(
                                    "POST",
                                    f"{OLLAMA_BASE}/api/chat",
                                    json=payload_no_tools,
                                ) as retry_response:
                                    async for line in retry_response.aiter_lines():
                                        if line.strip():
                                            import json as _json
                                            try:
                                                yield _json.loads(line)
                                            except Exception:
                                                continue
                            else:
                                # No tools to remove, just yield error
                                yield {"error": f"Ollama error: {response.status_code}", "done": True}
                            return
                        
                        async for line in response.aiter_lines():
                            if line.strip():
                                import json as _json
                                try:
                                    yield _json.loads(line)
                                except Exception:
                                    continue
                except httpx.HTTPStatusError as e:
                    if tools:
                        log.warning(f"HTTP error with tools, retrying without: {e}")
                        payload_no_tools = {k: v for k, v in payload.items() if k != "tools"}
                        async with client.stream(
                            "POST",
                            f"{OLLAMA_BASE}/api/chat",
                            json=payload_no_tools,
                        ) as retry_response:
                            async for line in retry_response.aiter_lines():
                                if line.strip():
                                    import json as _json
                                    try:
                                        yield _json.loads(line)
                                    except Exception:
                                        continue
                    else:
                        yield {"error": str(e), "done": True}
            else:
                r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
                if r.status_code >= 400 and tools:
                    log.warning(f"Ollama returned {r.status_code}, retrying without tools")
                    payload_no_tools = {k: v for k, v in payload.items() if k != "tools"}
                    r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload_no_tools)
                yield r.json()
