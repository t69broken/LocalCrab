# LocalCrab — Installation Guide

## Requirements

| Requirement | Notes |
|---|---|
| **OS** | Ubuntu 22.04+ / Debian 12+ (other Linux distros should work but are untested) |
| **Docker** | 24.0+ with Compose v2 plugin |
| **GPU (recommended)** | NVIDIA GPU with 4 GB+ VRAM. CPU mode works but inference is slow. |
| **RAM** | 8 GB minimum; 16 GB+ recommended for larger models |
| **Disk** | 20 GB+ free for model storage |

---

## Quick Install

```bash
git clone https://github.com/t69broken/LocalCrab.git
cd LocalCrab
bash install.sh
```

The installer will:
1. Install Docker and Docker Compose v2 if not already present
2. Install NVIDIA Container Toolkit if an NVIDIA GPU is detected
3. Create a `.env` file from defaults
4. Open port 18798 via `ufw` or `firewalld`
5. Build and start `localclaw-app` and `localclaw-ollama`
6. Pull `llama3.2` as a starter model
7. Print the URL to the web UI

Once done, open **http://localhost:18798** in your browser.

---

## Install Options

### With Tailscale (remote access)

Get a Tailscale auth key from [tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys), then:

```bash
bash install.sh --tailscale=tskey-auth-xxxxx
```

LocalCrab will join your Tailnet as `localcrab` and be reachable at `https://localcrab.<your-tailnet>` from any device on the network.

You can also enable Tailscale after installation by editing `.env`:

```bash
# .env
TS_AUTHKEY=tskey-auth-xxxxx
TS_HOSTNAME=localcrab
```

Then start the Tailscale sidecar:

```bash
docker compose --profile tailscale up -d
```

### CPU Only (no GPU)

```bash
bash install.sh --no-gpu
```

Inference will be significantly slower. Stick to smaller models (1–3B parameters).

---

## Pulling Models

LocalCrab comes with no models pre-pulled (other than `llama3.2` from the install script). Add more with:

```bash
docker exec localclaw-ollama ollama pull <model>
```

Recommended starting set:

| Model | Size | Best for |
|---|---|---|
| `llama3.2` | 2 GB | Fast chat, general use |
| `qwen2.5-coder:7b` | 5 GB | Coding tasks |
| `deepseek-r1:7b` | 5 GB | Reasoning, analysis |
| `phi4` | 9 GB | Balanced all-rounder |
| `llama3.1:8b` | 5 GB | Instruction following |
| `gemma3` | 5 GB | Creative writing, chat |

List all pulled models:

```bash
docker exec localclaw-ollama ollama list
```

The model selector automatically routes tasks to the best-fit model based on task type and available VRAM. See [FEATURES.md](FEATURES.md#smart-model-routing) for details.

---

## Configuration

### `.env` (Docker secrets and watchdog settings)

Copy from the example file and edit:

```bash
cp .env.example .env
nano .env
```

| Variable | Default | Description |
|---|---|---|
| `TS_AUTHKEY` | _(empty)_ | Tailscale auth key — leave blank to skip |
| `TS_HOSTNAME` | `localcrab` | Tailscale device hostname |
| `RALPH_CHECK_IN_INTERVAL` | `12` | Seconds between watchdog check-ins |
| `RALPH_STALL_TIMEOUT` | `45` | Seconds of silence before a stall poke |
| `RALPH_MAX_RETRIES` | `3` | Stall/loop pokes before task fails |
| `RALPH_TASK_HARD_LIMIT` | `600` | Hard task kill at N seconds (10 min) |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOWED_USERS` | _(empty)_ | Comma-separated Telegram user IDs |

### `localclaw.env` (bare-metal / no Docker)

If running the app directly outside Docker, copy and edit the path template:

```bash
cp localclaw.env.example localclaw.env
nano localclaw.env
source localclaw.env
python -m uvicorn main:app --host 0.0.0.0 --port 18798 --app-dir src
```

---

## Managing the Stack

```bash
# Start everything
docker compose up -d

# With Tailscale
docker compose --profile tailscale up -d

# Stop
docker compose down

# Restart just the app (e.g. after editing src/)
docker compose restart localclaw

# Live logs
docker compose logs -f localclaw

# Rebuild after code changes
docker compose build localclaw && docker compose up -d localclaw
```

---

## NVIDIA GPU Troubleshooting

If you see `libnvidia-ml.so.1 not found` or containers fail to access the GPU:

```bash
sudo bash fixnvidia.sh
# or if that doesn't work:
sudo bash fixnvidia2.sh
```

These scripts re-configure the NVIDIA Container Toolkit and Docker daemon runtime.

Verify the GPU is visible inside containers:

```bash
docker exec localclaw-ollama nvidia-smi
```

---

## Upgrading

```bash
cd LocalCrab
git pull
docker compose build localclaw
docker compose up -d localclaw
```

Model data and chat history are stored in Docker volumes (`localclaw-data`, `ollama-models`) and are preserved across upgrades.

---

## Uninstalling

```bash
# Stop and remove containers
docker compose down

# Also remove volumes (chat history, memory, installed skills, pulled models)
docker compose down -v
```
