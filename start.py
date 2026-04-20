#!/bin/bash
# Export environment variables from .env before launching LocalClaw

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Source the .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
    echo "✅ Loaded .env file:"
    echo "   HISTORY_DB=$HISTORY_DB"
    echo "   TASKS_DB=$TASKS_DB"
    echo "   MEMORY_DB=$MEMORY_DB"
    echo "   SKILLS_DIR=$SKILLS_DIR"
    echo "   PERSONAS_DIR=$PERSONAS_DIR"
    echo "   COMMS_CONFIG=$COMMS_CONFIG"
else
    echo "⚠️  .env file not found - using default paths"
fi

# Export PYTHONPATH to include src module
export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"

# Run uvicorn
uvicorn src.main:app --host 0.0.0.0 --port 18798
