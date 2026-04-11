"""
GPUManager — Monitors and manages GPU VRAM for optimal model performance.

Goals:
  • Keep active models entirely in GPU VRAM (no CPU offload) for max speed.
  • Proactively warn Ollama when VRAM is tight (adjust num_gpu parameter).
  • Expose metrics for the dashboard.
"""

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("localclaw.gpu")


@dataclass
class GPUInfo:
    index: int
    name: str
    total_vram_mb: float
    used_vram_mb: float
    free_vram_mb: float
    utilization_pct: float
    temperature_c: float
    power_draw_w: float
    power_limit_w: float

    @property
    def total_vram_gb(self) -> float:
        return self.total_vram_mb / 1024

    @property
    def used_vram_gb(self) -> float:
        return self.used_vram_mb / 1024

    @property
    def free_vram_gb(self) -> float:
        return self.free_vram_mb / 1024

    @property
    def vram_pct(self) -> float:
        if self.total_vram_mb == 0:
            return 0
        return self.used_vram_mb / self.total_vram_mb * 100


@dataclass
class GPUState:
    gpus: list[GPUInfo] = field(default_factory=list)
    has_gpu: bool = False
    error: Optional[str] = None

    @property
    def total_free_vram_gb(self) -> float:
        return sum(g.free_vram_gb for g in self.gpus)

    @property
    def total_vram_gb(self) -> float:
        return sum(g.total_vram_gb for g in self.gpus)


class GPUManager:
    def __init__(self):
        self._state = GPUState()
        self._monitor_task: Optional[asyncio.Task] = None
        self._poll_interval = 5  # seconds
        self._history: list[dict] = []
        self._max_history = 60  # keep 5 minutes of data at 5s intervals
        # Overflow tracking
        self._overflow_models: dict[str, dict] = {}  # model_name -> overflow info
        self._last_overflow_check: float = 0.0
        self._overflow_check_interval = 15  # seconds
        self._correction_cooldown: dict[str, float] = {}  # model -> last correction time
        self._correction_cooldown_secs = 120  # wait 2 min between corrections per model
        self._ollama_base: str = __import__("os").getenv("OLLAMA_BASE_URL", "http://ollama:11434")

    async def start_monitoring(self):
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        log.info("GPU monitoring started")

    async def stop_monitoring(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self):
        import time
        while True:
            try:
                await self._poll()
                if self._state.has_gpu:
                    self._record_history()
                    await self._check_pressure()
                    # Check Ollama CPU offload overflow every N seconds
                    now = time.monotonic()
                    if now - self._last_overflow_check >= self._overflow_check_interval:
                        self._last_overflow_check = now
                        await self._check_and_correct_overflow()
            except Exception as e:
                log.debug(f"GPU monitor error: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _poll(self):
        """Query nvidia-smi for current GPU state."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._query_nvidia_smi)
            if result:
                self._state = result
        except Exception as e:
            self._state = GPUState(error=str(e))

    def _query_nvidia_smi(self) -> Optional[GPUState]:
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,memory.free,"
                "utilization.gpu,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5)
            gpus = []
            for line in output.decode().strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 9:
                    continue
                def safe_float(v):
                    try:
                        return float(v)
                    except Exception:
                        return 0.0
                gpus.append(GPUInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    total_vram_mb=safe_float(parts[2]),
                    used_vram_mb=safe_float(parts[3]),
                    free_vram_mb=safe_float(parts[4]),
                    utilization_pct=safe_float(parts[5]),
                    temperature_c=safe_float(parts[6]),
                    power_draw_w=safe_float(parts[7]),
                    power_limit_w=safe_float(parts[8]),
                ))
            return GPUState(gpus=gpus, has_gpu=len(gpus) > 0)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return GPUState(has_gpu=False, error="nvidia-smi not available — CPU mode")
        except Exception as e:
            return GPUState(has_gpu=False, error=str(e))

    def _record_history(self):
        if not self._state.gpus:
            return
        entry = {
            "ts": asyncio.get_event_loop().time(),
            "gpus": [
                {
                    "index": g.index,
                    "free_gb": round(g.free_vram_gb, 2),
                    "used_gb": round(g.used_vram_gb, 2),
                    "util_pct": g.utilization_pct,
                    "temp_c": g.temperature_c,
                }
                for g in self._state.gpus
            ],
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    async def _check_pressure(self):
        """Log a warning if VRAM is getting tight."""
        for gpu in self._state.gpus:
            if gpu.vram_pct > 92:
                log.warning(
                    f"⚠️  GPU {gpu.index} VRAM pressure: {gpu.used_vram_gb:.1f}/{gpu.total_vram_gb:.1f} GB used "
                    f"({gpu.vram_pct:.0f}%) — consider offloading layers"
                )

    async def _check_and_correct_overflow(self):
        """
        Poll Ollama /api/ps to find models with CPU-offloaded layers.
        If free VRAM is available, auto-correct by unloading the model so it
        reloads fully on GPU on the next inference call.
        """
        import httpx, time
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._ollama_base}/api/ps")
                if r.status_code != 200:
                    return
                data = r.json()
        except Exception:
            return

        models = data.get("models", [])
        new_overflow: dict[str, dict] = {}

        for m in models:
            name = m.get("name", "")
            size_total = m.get("size", 0)
            size_vram = m.get("size_vram", 0)

            if size_total == 0:
                continue

            gpu_pct = size_vram / size_total
            cpu_bytes = size_total - size_vram
            cpu_gb = cpu_bytes / 1e9

            if gpu_pct < 0.99 and cpu_gb > 0.1:  # >100MB in CPU RAM = overflow
                new_overflow[name] = {
                    "name": name,
                    "total_gb": round(size_total / 1e9, 2),
                    "vram_gb": round(size_vram / 1e9, 2),
                    "cpu_gb": round(cpu_gb, 2),
                    "gpu_pct": round(gpu_pct * 100, 1),
                }
                if name not in self._overflow_models:
                    log.warning(
                        f"⚠️  VRAM OVERFLOW: {name} — {cpu_gb:.1f} GB in CPU RAM "
                        f"({gpu_pct*100:.0f}% GPU, {size_vram/1e9:.1f}/{size_total/1e9:.1f} GB in VRAM)"
                    )

        self._overflow_models = new_overflow

        if not new_overflow:
            return  # nothing to fix

        # Attempt auto-correction for overflowing models
        free_vram_gb = self._state.total_free_vram_gb
        now = time.monotonic()

        for name, info in new_overflow.items():
            # Skip if corrected recently
            last_correction = self._correction_cooldown.get(name, 0)
            if now - last_correction < self._correction_cooldown_secs:
                continue

            cpu_gb = info["cpu_gb"]
            # Only try to fix if there's meaningful free VRAM that could absorb more layers
            if free_vram_gb >= 0.5:
                log.info(
                    f"[GPU AutoCorrect] {name}: {cpu_gb:.1f} GB CPU overflow, "
                    f"{free_vram_gb:.1f} GB VRAM free — unloading for GPU-full reload"
                )
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        # Unload the model (keep_alive=0 forces eviction)
                        await client.post(
                            f"{self._ollama_base}/api/generate",
                            json={"model": name, "keep_alive": 0},
                        )
                    self._correction_cooldown[name] = now
                    # Remove from overflow state since it's now unloaded
                    self._overflow_models.pop(name, None)
                    log.info(
                        f"[GPU AutoCorrect] ✓ Evicted {name} from RAM. "
                        f"Next inference will reload with full GPU layers."
                    )
                except Exception as e:
                    log.warning(f"[GPU AutoCorrect] Failed to evict {name}: {e}")
            else:
                log.warning(
                    f"[GPU AutoCorrect] {name} overflows by {cpu_gb:.1f} GB but "
                    f"only {free_vram_gb:.1f} GB VRAM free — cannot fully fix. "
                    f"Consider using a smaller/quantized model."
                )

    def get_status(self) -> dict:
        if not self._state.has_gpu:
            return {
                "has_gpu": False,
                "free_vram_gb": 0.0,
                "error": self._state.error,
            }
        return {
            "has_gpu": True,
            "free_vram_gb": round(self._state.total_free_vram_gb, 2),
            "total_vram_gb": round(self._state.total_vram_gb, 2),
            "gpu_count": len(self._state.gpus),
        }

    def get_detailed_status(self) -> dict:
        status = self.get_status()
        if self._state.has_gpu:
            status["gpus"] = [
                {
                    "index": g.index,
                    "name": g.name,
                    "total_gb": round(g.total_vram_gb, 2),
                    "used_gb": round(g.used_vram_gb, 2),
                    "free_gb": round(g.free_vram_gb, 2),
                    "vram_pct": round(g.vram_pct, 1),
                    "utilization_pct": g.utilization_pct,
                    "temperature_c": g.temperature_c,
                    "power_draw_w": g.power_draw_w,
                    "power_limit_w": g.power_limit_w,
                }
                for g in self._state.gpus
            ]
            status["history"] = self._history[-12:]  # last minute
        status["overflow_models"] = list(self._overflow_models.values())
        status["overflow_count"] = len(self._overflow_models)
        return status

    def get_overflow_status(self) -> dict:
        """Return current CPU-offload overflow state for all loaded Ollama models."""
        return {
            "overflow_models": list(self._overflow_models.values()),
            "overflow_count": len(self._overflow_models),
            "free_vram_gb": round(self._state.total_free_vram_gb, 2),
            "has_overflow": len(self._overflow_models) > 0,
        }

    def recommend_ollama_options(self, model_vram_gb: float) -> dict:
        """
        Return Ollama options dict that maximises GPU layer usage
        while keeping the model fully in VRAM.
        """
        if not self._state.has_gpu:
            # GPU not visible to this container (Ollama runs on host with its own GPU access).
            # Return empty dict so Ollama uses its own default — don't force num_gpu=0.
            return {}

        free_gb = self._state.total_free_vram_gb
        # Leave 1GB headroom
        usable_gb = max(0, free_gb - 1.0)

        if model_vram_gb <= usable_gb:
            # Full GPU — use all layers
            return {
                "num_gpu": 99,
                "num_thread": 8,
            }
        elif usable_gb > 0:
            # Partial GPU — estimate proportion of layers that fit
            ratio = usable_gb / model_vram_gb
            # Typical models have ~32-80 transformer layers; we approximate
            estimated_layers = int(ratio * 40)
            log.info(
                f"Model too large for full VRAM ({model_vram_gb:.1f}GB needed, "
                f"{usable_gb:.1f}GB free) — using ~{estimated_layers} GPU layers"
            )
            return {
                "num_gpu": estimated_layers,
                "num_thread": 8,
            }
        else:
            log.warning("VRAM full — falling back to CPU for this request")
            return {"num_gpu": 0, "num_thread": 8}

    async def optimize(self) -> dict:
        """
        Request Ollama to free up idle model memory.
        Ollama doesn't have an explicit evict API, but we can POST to /api/generate
        with keep_alive=0 to unload a model.
        """
        import httpx, os
        ollama_base = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        try:
            # Fetch list of running models from Ollama ps endpoint
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{ollama_base}/api/ps")
                data = r.json()
                running = data.get("models", [])

            unloaded = []
            for m in running:
                name = m.get("name", "")
                # Unload by setting keep_alive to 0
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(
                        f"{ollama_base}/api/generate",
                        json={"model": name, "keep_alive": 0},
                    )
                unloaded.append(name)
                log.info(f"Evicted model from VRAM: {name}")

            await self._poll()
            return {
                "status": "ok",
                "evicted": unloaded,
                "free_vram_gb": round(self._state.total_free_vram_gb, 2),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
