# LocalCrab — Features

## Overview

LocalCrab is a local-first AI agent runtime built on [Ollama](https://ollama.com). Everything runs on your machine — no API keys, no cloud, no data leaving your network. It exposes an OpenClaw-compatible REST + WebSocket API on port 18798 and includes a built-in web UI.

---

## Smart Model Routing

LocalCrab automatically selects the best locally-pulled model for each request based on:

- **Task type** — `coding`, `reasoning`, `math`, `analysis`, `chat`, `creative`
- **Available VRAM** — models that don't fit in GPU memory are skipped
- **Capability profiles** — each known model has a scored profile; unknown models get sensible defaults

For example, a `task_hint="coding"` request with a 6 GB GPU will route to `qwen2.5-coder:7b` over `llama3.2` because it scores higher on coding and fits in VRAM.

**API:**
```
GET /models/recommend?task=coding
GET /models                          # list all pulled models
POST /models/pull/{name}             # pull a new model
GET /models/select/{name}            # pin a global preferred model
DELETE /models/preferred             # return to auto-selection
```

---

## Ralph Wiggum Loop (Task Watchdog)

Named after Ralph Wiggum's habit of unpromptedly narrating his own state. When you submit a long-running task via `POST /tasks`, a per-job watchdog fires alongside the agent:

```
Task starts
  ├── every 12s   → "Keep going. Call the next tool now."  (check-in)
  ├── 45s silence → "You've gone quiet. Continue."         (stall poke)
  ├── repeated output → "Loop detected. Try a different approach."
  └── after 3 failed pokes → task status = failed
```

Check-in prompts are injected directly into the agent's conversation history between steps, so the model actually sees them and responds. Each check-in response is logged and available via the API.

All events stream over WebSocket in real time:

```
WS /ws/tasks/{job_id}     # events for one task
WS /ws/events             # firehose of all task events
```

**Event types:** `checkin`, `stall`, `loop`, `poke`, `finished`, `escalated`, `ping`

**API:**
```
POST /tasks                      # submit a task
GET  /tasks                      # list all tasks
GET  /tasks/{id}                 # get job state
GET  /tasks/{id}/checkins        # all check-in logs for a job
POST /tasks/{id}/reply           # send a mid-task message to a running job
DELETE /tasks/{id}               # cancel a running job
```

**Configurable via env:**

| Variable | Default | Meaning |
|---|---|---|
| `RALPH_CHECK_IN_INTERVAL` | `12` | Seconds between check-ins |
| `RALPH_STALL_TIMEOUT` | `45` | Seconds of silence before poke |
| `RALPH_MAX_RETRIES` | `3` | Pokes before task fails |
| `RALPH_TASK_HARD_LIMIT` | `600` | Hard kill at N seconds |

---

## Tool Use

Agents have access to six categories of tools. Tool schemas (parameter names and types) are injected into each model's system prompt so the model knows exactly how to call them.

### File Tools
Read, write, list, move, and delete files on the host filesystem.

### Terminal Tools
Run shell commands on the host. The container connects back to the host via SSH, so commands execute with your user's permissions on the actual machine — not inside the container.

### Web Tools
Fetch URLs, search the web, and extract page content for use in tasks.

### Memory Tools
Store, search, and retrieve memories from the long-term memory store. Backed by SQLite with cosine vector search — memories persist across sessions and are scoped per-agent.

### System Tools
Query system status, list running processes, check GPU state.

### Skills Tools
Search ClaWHub for skills, install them at runtime, and list installed skills.

---

## ClaWHub Skills

Skills are markdown files (`.SKILL.md`) that extend an agent's behavior by adding task-specific instructions and tool recommendations. Three skills are built in:

| Skill | Purpose |
|---|---|
| `code-assistant` | Code review, debugging, refactoring |
| `researcher` | Web research, summarization, citation |
| `sysadmin` | System administration, file management, shell scripting |

Install additional skills at runtime via the API or the web UI:

```
GET  /skills/search?q=<query>    # search ClaWHub
POST /skills/install             # install by slug
GET  /skills                     # list installed skills
DELETE /skills/{slug}            # uninstall
```

Or let an agent do it using the `search_skills` / `install_skill` tools.

---

## Personas (SOUL.md)

Each agent can be given a persona — a `SOUL.md` file that defines its personality, communication style, and defaults. Two personas are built in:

| Persona | Description |
|---|---|
| `assistant` | Helpful, clear, neutral general-purpose assistant |
| `hacker` | Technical, terse, prefers the command line |

Install more from `onlycrabs.ai` at runtime:

```
GET  /personas/search?q=<query>    # search
POST /personas/install/{slug}      # install
GET  /personas                     # list installed personas
```

Assign a persona when creating an agent or per-request via the `persona` field on a chat request.

---

## Multi-Agent

Spawn multiple independent agents, each with:
- Its own conversation history
- Its own persona
- Its own preferred model
- Its own skill set

```
GET    /agents             # list agents
POST   /agents             # create agent (name, persona_slug, skills, preferred_model)
GET    /agents/{id}        # get agent state
DELETE /agents/{id}        # delete agent
POST   /agents/{id}/reset  # clear context without deleting the agent
PUT    /agents/{id}/model  # change model preference
```

Chat with any agent by agent ID:

```
POST /chat                   # single-turn
WS   /ws/chat/{agent_id}     # streaming
```

History is persisted per-agent and reloaded on restart:

```
GET    /history/{agent_id}    # retrieve stored history
DELETE /history/{agent_id}    # clear stored history
```

---

## MCP Long-Term Memory

Each agent has access to a persistent memory store backed by SQLite with vector search (cosine similarity). Memories are stored as text with embeddings and can be:

- Saved during a session by the agent itself (via the `memory` tool)
- Searched semantically by query
- Scoped to a specific agent or global

Memories survive container restarts. The MCP endpoint at `POST /mcp` makes LocalCrab a compatible MCP memory server for other tools.

```
GET  /memory                   # list memories (agent_id filter optional)
POST /memory/search            # semantic search by query
DELETE /memory/{id}            # delete a specific memory
GET  /memory/export            # export all memories as JSON
POST /mcp                      # MCP JSON-RPC endpoint
```

---

## GPU VRAM Manager

The GPU manager monitors `nvidia-smi` in the background and keeps track of which models are loaded in VRAM. When memory pressure is detected:

- Idle models (unused for 5+ minutes) are evicted
- Overflow is detected and corrected automatically
- Load decisions for new models are VRAM-aware

```
GET  /gpu                   # current GPU status
GET  /gpu/overflow          # overflow detection status
POST /gpu/overflow/fix      # trigger immediate overflow correction
POST /gpu/optimize          # evict idle models now
```

---

## Hermes Tool-Calling Format

Local models vary wildly in how they handle tool calls. LocalCrab uses two strategies:

- **Hermes XML format** — for most models (llama, qwen, gemma, phi, deepseek, mistral, mixtral, codellama, solar, and more). Full JSON schemas are injected into the system prompt as an XML `<tools>` block. The model emits `<tool_call>` tags that are parsed reliably.
- **Native Ollama tool_calls API** — for models that support it natively, with a JSON block fallback.

This means models that don't support native tool calling still get full parameter-aware tool use.

---

## Telegram Bot

Connect LocalCrab to a Telegram bot for mobile-friendly chat and status heartbeats. Configure via `.env` or the API at runtime:

```
GET  /comms/status            # bot status
POST /comms/telegram          # set token + allowed users
POST /comms/telegram/start    # start bot
POST /comms/telegram/stop     # stop bot
```

Optional: set `HEARTBEAT_CHAT_ID` in `.env` to receive periodic status pings (uptime, Ollama health, task counts) at a configurable interval.

---

## Token Usage Tracking

LocalCrab tracks input/output tokens per model for the current session:

```
GET    /tokens    # usage stats broken down by model
DELETE /tokens    # reset counters
```

---

## Tailscale Mesh Access

Run the Tailscale sidecar to make LocalCrab accessible from any device on your Tailnet — phone, laptop, another server — without opening public ports.

```bash
# In .env:
TS_AUTHKEY=tskey-auth-xxxxx
TS_HOSTNAME=localcrab

# Start with sidecar:
docker compose --profile tailscale up -d
```

The sidecar shares the `localclaw` container's network, so `https://localcrab.<tailnet>` routes directly to port 18798 via the Tailscale serve config.

---

## REST API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | System health and component status |
| `GET` | `/status` | Detailed status: GPU, models, agents, skills, tasks |
| `GET` | `/tokens` | Token usage stats |
| `DELETE` | `/tokens` | Reset token counters |
| `GET` | `/models` | List pulled Ollama models |
| `GET` | `/models/recommend?task=` | Best model for a task type |
| `POST` | `/models/pull/{name}` | Pull a new model |
| `GET/DELETE` | `/models/preferred` | Get/clear global preferred model |
| `GET` | `/agents` | List agents |
| `POST` | `/agents` | Create agent |
| `GET/DELETE` | `/agents/{id}` | Get / delete agent |
| `POST` | `/agents/{id}/reset` | Clear agent context |
| `PUT` | `/agents/{id}/model` | Set agent's preferred model |
| `GET` | `/tools` | List all available tools |
| `POST` | `/chat` | Single-turn chat |
| `WS` | `/ws/chat/{agent_id}` | Streaming chat |
| `GET/DELETE` | `/history/{agent_id}` | Get / clear agent history |
| `POST` | `/tasks` | Submit a Ralph-loop task |
| `GET` | `/tasks` | List all tasks |
| `GET` | `/tasks/{id}` | Get task state |
| `GET` | `/tasks/{id}/checkins` | Task check-in log |
| `POST` | `/tasks/{id}/reply` | Send mid-task message |
| `DELETE` | `/tasks/{id}` | Cancel task |
| `WS` | `/ws/tasks/{id}` | Stream task events |
| `WS` | `/ws/events` | Firehose of all events |
| `GET` | `/skills` | List installed skills |
| `GET` | `/skills/search?q=` | Search ClaWHub |
| `POST` | `/skills/install` | Install a skill |
| `DELETE` | `/skills/{slug}` | Uninstall a skill |
| `GET` | `/personas` | List installed personas |
| `GET` | `/personas/search?q=` | Search OnlyCrabs |
| `POST` | `/personas/install/{slug}` | Install a persona |
| `GET` | `/memory` | List memories |
| `POST` | `/memory/search` | Semantic memory search |
| `DELETE` | `/memory/{id}` | Delete a memory |
| `GET` | `/memory/export` | Export all memories |
| `POST` | `/mcp` | MCP JSON-RPC endpoint |
| `GET` | `/gpu` | GPU status |
| `POST` | `/gpu/optimize` | Evict idle models |
| `GET/POST` | `/comms/telegram` | Telegram bot config |

Full interactive docs (Swagger UI): **http://localhost:18798/docs**
