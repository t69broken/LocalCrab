#!/bin/bash
# Launch script for LocalClaw on bare metal (non-Docker)
# Run from /home/tyson/ClaudeLocalClaw/localclaw/

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Set PATH to include venv
export PATH="$SCRIPT_DIR/venv/bin:$PATH"

# Load environment variables from .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

echo "=== Starting LocalClaw ==="
echo "PID: $$"
echo "PORT: 18798"
echo "ENV: $(cat .env | grep -v '^#' | head -5 | tr '\n' ' ')"
echo ""
echo uvicorn src.main:app --host 0.0.0.0 --port 18798

# Run uvicorn from the src directory
uvicorn src.main:app --host 0.0.0.0 --port 18798
