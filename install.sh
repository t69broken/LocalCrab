#!/usr/bin/env bash
# LocalClaw install script — Ubuntu/Debian Linux
# Usage: bash install.sh [--tailscale TS_AUTHKEY] [--no-gpu]
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[LC]${NC} $*"; }
warn()  { echo -e "${YELLOW}[LC]${NC} $*"; }
error() { echo -e "${RED}[LC]${NC} $*"; exit 1; }

TS_AUTHKEY=""
NO_GPU=false

for arg in "$@"; do
  case $arg in
    --tailscale=*) TS_AUTHKEY="${arg#*=}" ;;
    --tailscale)   TS_AUTHKEY="${2:-}"; shift ;;
    --no-gpu)      NO_GPU=true ;;
  esac
done

info "🦀 LocalClaw Installer"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────────

info "Checking prerequisites…"

command -v docker >/dev/null 2>&1 || {
  warn "Docker not found — installing…"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  warn "You may need to log out and back in for Docker group permissions."
}

command -v docker >/dev/null 2>&1 || error "Docker install failed"
info "  ✓ Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# docker compose v2
docker compose version >/dev/null 2>&1 || {
  warn "Docker Compose v2 plugin not found — installing…"
  sudo apt-get install -y docker-compose-plugin
}
info "  ✓ Docker Compose $(docker compose version --short)"

# ── NVIDIA GPU setup ───────────────────────────────────────────────────────

if [ "$NO_GPU" = false ] && command -v nvidia-smi >/dev/null 2>&1; then
  info "NVIDIA GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"

  # NVIDIA Container Toolkit
  if ! dpkg -l nvidia-container-toolkit &>/dev/null; then
    warn "Installing NVIDIA Container Toolkit…"
    distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
  fi
  info "  ✓ NVIDIA Container Toolkit ready"
else
  if [ "$NO_GPU" = true ]; then
    warn "GPU disabled by --no-gpu flag — CPU-only mode"
  else
    warn "No NVIDIA GPU detected — running in CPU mode (inference will be slow)"
  fi
  # Patch docker-compose to remove GPU requirements
  sed -i '/deploy:/,/capabilities: \[gpu\]/d' docker-compose.yml
fi

# ── Create .env ────────────────────────────────────────────────────────────

if [ ! -f .env ]; then
  info "Creating .env…"
  cat > .env << EOF
# LocalClaw environment
TS_AUTHKEY=${TS_AUTHKEY}
TS_HOSTNAME=localclaw

# Ralph Wiggum loop settings
RALPH_CHECK_IN_INTERVAL=12
RALPH_STALL_TIMEOUT=45
RALPH_MAX_RETRIES=3
RALPH_TASK_HARD_LIMIT=600
EOF
  info "  ✓ .env created"
fi

# ── Open firewall port 18798 ───────────────────────────────────────────────

info "Opening port 18798…"
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow 18798/tcp comment "LocalClaw" || warn "ufw rule may already exist"
  info "  ✓ ufw: port 18798 open"
elif command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-port=18798/tcp
  sudo firewall-cmd --reload
  info "  ✓ firewalld: port 18798 open"
else
  warn "No recognized firewall tool — manually open port 18798/tcp if needed"
fi

# ── Build & start ──────────────────────────────────────────────────────────

info "Building LocalClaw…"
docker compose build --no-cache

info "Starting services…"
if [ -n "$TS_AUTHKEY" ]; then
  docker compose --profile tailscale up -d
else
  docker compose up -d
fi

# ── Wait for health ────────────────────────────────────────────────────────

info "Waiting for LocalClaw to be ready…"
MAX_WAIT=60
for i in $(seq 1 $MAX_WAIT); do
  if curl -sf http://localhost:18798/health > /dev/null 2>&1; then
    break
  fi
  sleep 1
  printf '.'
done
echo ""

if ! curl -sf http://localhost:18798/health > /dev/null 2>&1; then
  warn "LocalClaw did not become healthy in ${MAX_WAIT}s — check logs:"
  warn "  docker compose logs localclaw"
else
  info "✅ LocalClaw is running!"
fi

# ── Pull a starter model ───────────────────────────────────────────────────

info "Pulling starter model llama3.2 (3GB — adjust as needed)…"
docker exec localclaw-ollama ollama pull llama3.2 || warn "Model pull failed — run manually: docker exec localclaw-ollama ollama pull llama3.2"

# ── Done ───────────────────────────────────────────────────────────────────

LOCAL_IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🦀 LocalClaw is ready!${NC}"
echo ""
echo -e "  Web UI   →  http://localhost:18798"
echo -e "  Network  →  http://${LOCAL_IP}:18798"
if [ -n "$TS_AUTHKEY" ]; then
  echo -e "  Tailscale→  https://localclaw (after TS connects)"
fi
echo -e "  API docs →  http://localhost:18798/docs"
echo ""
echo -e "  Useful commands:"
echo -e "    docker compose logs -f localclaw       # live logs"
echo -e "    docker exec localclaw-ollama ollama pull deepseek-r1:7b"
echo -e "    docker exec localclaw-ollama ollama list"
echo -e "    docker compose down                    # stop all"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
