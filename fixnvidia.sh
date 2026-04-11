#!/usr/bin/env bash
# fix-nvidia-docker.sh
# Fixes: libnvidia-ml.so.1: cannot open shared object file
#        Auto-detected mode as 'legacy' (nvidia-container-cli)
#
# Run as: sudo bash fix-nvidia-docker.sh
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[fix]${NC} $*"; }
warn()  { echo -e "${YELLOW}[fix]${NC} $*"; }
error() { echo -e "${RED}[fix]${NC} $*" >&2; exit 1; }

[ "$EUID" -ne 0 ] && error "Run as root: sudo bash $0"

# ── 1. Verify the host driver is actually loaded ──────────────────────────

info "Checking host NVIDIA driver…"
if ! nvidia-smi &>/dev/null; then
  error "nvidia-smi failed — NVIDIA driver is not loaded on the host. Install it first:
  ubuntu-drivers install
  reboot"
fi
DRIVER_VER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
info "  Host driver: $DRIVER_VER"

# ── 2. Find where the host put libnvidia-ml.so.1 ─────────────────────────

info "Locating libnvidia-ml.so.1 on host…"
LIBML=$(ldconfig -p 2>/dev/null | awk '/libnvidia-ml\.so\.1/{print $NF}' | head -1)

if [ -z "$LIBML" ]; then
  # Try common fallback paths
  for candidate in \
    /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1 \
    /usr/lib64/libnvidia-ml.so.1 \
    /usr/local/lib/libnvidia-ml.so.1 \
    /usr/lib/libnvidia-ml.so.1; do
    if [ -f "$candidate" ]; then
      LIBML="$candidate"
      break
    fi
  done
fi

if [ -z "$LIBML" ]; then
  warn "libnvidia-ml.so.1 not found via ldconfig — trying find (slow)…"
  LIBML=$(find /usr /lib /opt -name "libnvidia-ml.so.1" 2>/dev/null | head -1 || true)
fi

if [ -z "$LIBML" ]; then
  error "Cannot locate libnvidia-ml.so.1 anywhere on this system.
  Your NVIDIA driver may be broken. Try:
    apt-get install --reinstall nvidia-driver-XXX   (where XXX = your version)
  Then rerun this script."
fi
info "  Found at: $LIBML"

# ── 3. Update ldconfig so the toolkit can find it ────────────────────────

LIBDIR=$(dirname "$LIBML")
info "Adding $LIBDIR to ldconfig…"
echo "$LIBDIR" > /etc/ld.so.conf.d/nvidia-localclaw.conf
ldconfig
info "  ldconfig updated"

# ── 4. Re-install / re-configure NVIDIA Container Toolkit ────────────────

info "Removing any old container toolkit packages…"
apt-get remove -y --purge \
  nvidia-docker nvidia-docker2 \
  nvidia-container-runtime \
  libnvidia-container0 libnvidia-container1 libnvidia-container-tools \
  nvidia-container-toolkit nvidia-container-toolkit-base \
  2>/dev/null || true

info "Adding NVIDIA Container Toolkit repository…"
DIST=$(. /etc/os-release; echo "$ID$VERSION_ID")
# Normalise: Ubuntu 22.04 → ubuntu22.04
DIST=$(echo "$DIST" | tr '[:upper:]' '[:lower:]' | tr -d ' ')

KEYRING=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o "$KEYRING"

REPO_URL="https://nvidia.github.io/libnvidia-container/${DIST}/libnvidia-container.list"
if ! curl -sf "$REPO_URL" -o /dev/null; then
  warn "Exact distro repo not found ($DIST) — falling back to stable"
  REPO_URL="https://nvidia.github.io/libnvidia-container/stable/deb/\$(ARCH)"
fi

curl -fsSL "$REPO_URL" \
  | sed "s#deb https://#deb [signed-by=${KEYRING}] https://#g" \
  | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update -qq
apt-get install -y nvidia-container-toolkit
info "  Toolkit installed: $(nvidia-container-toolkit --version 2>/dev/null || echo 'ok')"

# ── 5. Configure the Docker runtime ──────────────────────────────────────

info "Configuring Docker runtime (CDI mode)…"

# Prefer CDI mode over legacy; fall back gracefully
nvidia-ctk runtime configure --runtime=docker --set-as-default 2>/dev/null || \
nvidia-ctk runtime configure --runtime=docker

# Generate CDI spec (preferred for modern kernels)
if nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml 2>/dev/null; then
  info "  CDI spec generated at /etc/cdi/nvidia.yaml"
else
  warn "CDI generation failed — falling back to legacy mode (still works)"
fi

info "Restarting Docker…"
systemctl restart docker
sleep 2

# ── 6. Smoke-test ─────────────────────────────────────────────────────────

info "Running container smoke-test…"
if docker run --rm --gpus all ubuntu:22.04 \
     bash -c "apt-get install -y --no-install-recommends pciutils &>/dev/null && lspci | grep -i nvidia" \
     2>/dev/null | grep -qi nvidia; then
  info "  ✓ GPU visible inside container"
elif docker run --rm --gpus all nvidia/cuda:12.3.1-base-ubuntu22.04 \
       nvidia-smi 2>/dev/null | grep -q "Driver Version"; then
  info "  ✓ nvidia-smi works inside container"
else
  warn "Container GPU test inconclusive — checking docker info…"
  docker info 2>/dev/null | grep -i runtime || true
fi

# ── 7. Patch docker-compose.yml if present ───────────────────────────────

COMPOSE="$(dirname "$0")/docker-compose.yml"
if [ -f "$COMPOSE" ]; then
  # Ollama image now supports both GPU modes — no patch needed.
  # But ensure NVIDIA_VISIBLE_DEVICES env is set as a belt-and-suspenders.
  if ! grep -q "NVIDIA_VISIBLE_DEVICES" "$COMPOSE"; then
    info "Adding NVIDIA_VISIBLE_DEVICES=all to ollama service in docker-compose.yml…"
    sed -i '/container_name: localclaw-ollama/{
      n
      /restart:/i\    environment:\n      - NVIDIA_VISIBLE_DEVICES=all\n      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
    }' "$COMPOSE" || warn "Could not patch docker-compose.yml — add manually if needed"
  fi
fi

# ── 8. Restart LocalClaw stack if running ────────────────────────────────

if docker compose -f "$(dirname "$0")/docker-compose.yml" ps 2>/dev/null | grep -q "localclaw"; then
  info "Restarting LocalClaw stack…"
  docker compose -f "$(dirname "$0")/docker-compose.yml" restart ollama localclaw
fi

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ NVIDIA Container fix applied${NC}"
echo ""
echo -e "  Driver:   $DRIVER_VER"
echo -e "  Library:  $LIBML"
echo ""
echo -e "  Test manually:"
echo -e "    docker run --rm --gpus all nvidia/cuda:12.3.1-base-ubuntu22.04 nvidia-smi"
echo ""
echo -e "  Then restart LocalClaw:"
echo -e "    docker compose up -d"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
