# LocalClaw — Project Context

## What this is
OpenClaw-compatible local AI agent runtime. Ollama backend, port 18798,
GPU-aware, MCP long-term memory, ClaWHub skills, multi-agent, Tailscale.

## Key bug fixed (task_watchdog.py)
Original: one giant Ollama stream + parallel timer = Ralph never actually
interrupted the agent. Tasks "completed" instantly with 0 check-ins.

Fix: true agentic step loop in run_agentic_job(). Each step is a separate
Ollama call. Ralph injects check-ins INTO conversation history between steps
so the agent actually sees them. Agent signals completion with [DONE].

## Stack
- docker compose up -d (from this directory)
- localclaw-app: FastAPI on port 18798
- localclaw-ollama: Ollama on port 11434 (internal)
- Tailscale sidecar: optional, --profile tailscale

## Recent issues fixed
- duplicate environment key in docker-compose.yml (sed bug in fix-nvidia-docker.sh)
- libnvidia-ml.so.1 not found: run sudo bash fix-nvidia-docker.sh
- task_watchdog.py agentic loop: replace src/task_watchdog.py and src/main.py

## Useful commands
docker compose logs -f localclaw
docker compose restart localclaw
docker exec localclaw-ollama ollama list
