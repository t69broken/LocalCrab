# 🦀 LocalCrab

An OpenClaw-compatible AI agent runtime that runs **entirely local** via Ollama. No data leaves your machine.

## Features

| Feature | Detail |
|---|---|
| **Smart model routing** | Ollama container picks the best pulled model per task type |
| **Ralph Wiggum loop** | Watchdog fires check-ins every 12s, detects stalls (45s), breaks loops |
| **ClaWHub skills** | Built-in skills + install any from `clawhub.ai` at runtime |
| **Personas (SOUL.md)** | Built-in personas + install from `onlycrabs.ai` |
| **Multi-agent** | Spawn N agents, each with their own persona, skills, and history |
| **MCP long-term memory** | SQLite + cosine vector search, persists across sessions |
| **GPU VRAM manager** | Monitors `nvidia-smi`, keeps models fully in VRAM, evicts idle ones |
| **Port 18798** | LAN + Tailscale mesh access |

## Quick Start

```bash
# Clone / place files, then:
bash install.sh

# With Tailscale:
bash install.sh --tailscale=tskey-auth-xxxxx

# CPU only (no GPU):
bash install.sh --no-gpu
```

## Ralph Wiggum Loop

When you submit a task via `POST /tasks` (or click **🔄 Task** in the UI), a watchdog wraps the inference:

```
Task starts
  ├── every 12s: "Brief status: what step are you on?" → agent narrates
  ├── if 45s silence: "You've gone quiet. Continue from where you left off."
  ├── if repeated output: "Loop detected. Take a different approach."
  └── after 3 failed pokes: task escalated → status = failed
```

Events stream over `ws://host:18798/ws/tasks/{job_id}`.

Configure via env vars:

| Var | Default | Meaning |
|---|---|---|
| `RALPH_CHECK_IN_INTERVAL` | `12` | seconds between check-ins |
| `RALPH_STALL_TIMEOUT` | `45` | silence before poke |
| `RALPH_MAX_RETRIES` | `3` | pokes before escalation |
| `RALPH_TASK_HARD_LIMIT` | `600` | hard kill at N seconds |

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | System status |
| `GET` | `/models` | List pulled Ollama models |
| `GET` | `/models/recommend?task=coding` | Best model for task |
| `POST` | `/chat` | Single-turn chat |
| `WS` | `/ws/chat/{agent_id}` | Streaming chat |
| `POST` | `/tasks` | Submit Ralph-loop task |
| `GET` | `/tasks/{id}/checkins` | All check-in logs |
| `WS` | `/ws/tasks/{id}` | Stream task events |
| `WS` | `/ws/events` | All events firehose |
| `GET` | `/agents` | List agents |
| `POST` | `/agents` | Create agent |
| `GET/POST` | `/skills` | List / install skills |
| `GET` | `/skills/search?q=…` | Search ClaWHub |
| `GET/POST` | `/personas` | List / install personas |
| `POST` | `/memory/search` | Semantic memory search |
| `POST` | `/mcp` | MCP JSON-RPC endpoint |
| `GET` | `/gpu` | GPU status |
| `POST` | `/gpu/optimize` | Evict idle models |

Full interactive docs: `http://localhost:18798/docs`

## Pulling Models

```bash
# Recommended starting set:
docker exec localclaw-ollama ollama pull llama3.2          # 2GB  — fast chat
docker exec localclaw-ollama ollama pull qwen2.5-coder:7b  # 5GB  — coding
docker exec localclaw-ollama ollama pull deepseek-r1:7b    # 5GB  — reasoning
docker exec localclaw-ollama ollama pull phi4              # 9GB  — balanced
```

## Tailscale

Set `TS_AUTHKEY` in `.env`, then:

```bash
docker compose --profile tailscale up -d
```

LocalCrab will be reachable at `https://localcrab.<your-tailnet>` from any device on your Tailscale network.
