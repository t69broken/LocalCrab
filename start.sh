#!/bin/bash
# Launch script for LocalClaw on bare metal (non-Docker)
# Source the .env file before starting uvicorn

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load environment variables from .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# Start uvicorn on host
uvicorn localclaw.src.main:app --host 0.0.0.0 --port 18798 --reload
