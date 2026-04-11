# 🦀 LocalCrab

An OpenClaw-compatible AI agent runtime that runs **entirely local** via Ollama. No API keys. No cloud. No data leaves your machine.

## What it does

LocalCrab wraps Ollama with a full agent runtime — tool use, long-term memory, multi-agent support, a task watchdog, and a web UI — all behind a REST + WebSocket API on port 18798.

| | |
|---|---|
| **Runs on** | Any Linux machine with Docker (GPU recommended) |
| **Models** | Any model pulled in Ollama — llama3, qwen, deepseek, phi, gemma, mistral, and more |
| **Access** | LAN, or anywhere via Tailscale mesh |
| **API** | OpenClaw-compatible REST + WebSocket |

---

## Features

| Feature | Detail |
|---|---|
| **Smart model routing** | Scores pulled models by task type (coding, reasoning, chat, creative) and available VRAM |
| **Ralph Wiggum loop** | Per-task watchdog — check-ins every 12s, stall detection, loop breaking, hard kill |
| **Tool use** | File, terminal, web, memory, system, and skills tools — full parameter schemas in every prompt |
| **ClaWHub skills** | Built-in skills + install any from `clawhub.ai` at runtime via API or agent |
| **Personas (SOUL.md)** | Built-in personas + install from `onlycrabs.ai` |
| **Multi-agent** | N independent agents, each with persona, skills, history, and preferred model |
| **MCP long-term memory** | SQLite + cosine vector search, persists across sessions |
| **GPU VRAM manager** | Tracks `nvidia-smi`, evicts idle models, prevents overflow |
| **Telegram bot** | Optional bot integration with status heartbeat |
| **Hermes tool format** | Reliable XML tool-calling for llama, qwen, deepseek, mistral, phi, gemma, and more |

---

## Quick Start

```bash
git clone https://github.com/t69broken/LocalCrab.git
cd LocalCrab
bash install.sh
```

The installer handles Docker, NVIDIA Container Toolkit, `.env`, firewall, build, and a starter model pull.

```bash
# With Tailscale:
bash install.sh --tailscale=tskey-auth-xxxxx

# CPU only (no GPU):
bash install.sh --no-gpu
```

Then open **http://localhost:18798**.

→ Full instructions: [INSTALL.md](INSTALL.md)

---

## Pulling Models

```bash
docker exec localclaw-ollama ollama pull llama3.2          # 2 GB — fast chat
docker exec localclaw-ollama ollama pull qwen2.5-coder:7b  # 5 GB — coding
docker exec localclaw-ollama ollama pull deepseek-r1:7b    # 5 GB — reasoning
docker exec localclaw-ollama ollama pull phi4              # 9 GB — balanced
```

LocalCrab automatically routes each request to the best available model. You can also pin a model globally via the API or per-agent.

---

## Ralph Wiggum Loop

Submit long-running tasks via `POST /tasks`. A watchdog runs alongside the agent:

```
Task starts
  ├── every 12s   → "Keep going. Call the next tool now."
  ├── 45s silence → "You've gone quiet. Continue."
  ├── repeated output → "Loop detected. Try a different approach."
  └── after 3 failed pokes → status = failed
```

Events stream live over WebSocket at `ws://host:18798/ws/tasks/{job_id}`.

→ Full details: [FEATURES.md#ralph-wiggum-loop](FEATURES.md#ralph-wiggum-loop-task-watchdog)

---

## API (Quick Reference)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | System status |
| `GET` | `/models` | List pulled models |
| `GET` | `/models/recommend?task=coding` | Best model for a task |
| `POST` | `/chat` | Single-turn chat |
| `WS` | `/ws/chat/{agent_id}` | Streaming chat |
| `POST` | `/tasks` | Submit Ralph-loop task |
| `WS` | `/ws/tasks/{id}` | Stream task events |
| `GET` | `/agents` | List agents |
| `POST` | `/agents` | Create agent |
| `GET/POST` | `/skills` | List / install skills |
| `GET/POST` | `/personas` | List / install personas |
| `POST` | `/memory/search` | Semantic memory search |
| `POST` | `/mcp` | MCP JSON-RPC endpoint |
| `GET` | `/gpu` | GPU status |

Full interactive docs (Swagger UI): **http://localhost:18798/docs**

→ Complete API reference: [FEATURES.md#rest-api-reference](FEATURES.md#rest-api-reference)

---

## Tailscale

Add `TS_AUTHKEY` to `.env`, then:

```bash
docker compose --profile tailscale up -d
```

LocalCrab joins your Tailnet as `localcrab` and is reachable at `https://localcrab.<your-tailnet>` from any device.

---

## Docs

- [INSTALL.md](INSTALL.md) — Full install guide, configuration, GPU troubleshooting, upgrade/uninstall
- [FEATURES.md](FEATURES.md) — Deep dive on every feature with API details
