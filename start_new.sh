#!/bin/bash
# start.sh - Improved with process management
# Ensures clean restart and single healthy process

set -euo pipefail

LOGFILE="/tmp/localclaw_start.log"
PORT=18798
BIND="0.0.0.0:18798"

echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Cleaning up before start" >> "$LOGFILE"

# Clean any existing healthy process
PID_EXISTS=$(ss -tlnp "sport=$PORT" 2>/dev/null | grep -c "LISTEN" || echo "0")
if [ "$PID_EXISTS" -gt "0" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Process already running on port $PORT" >> "$LOGFILE"
    
    # Kill zombie if present
    ZOMBIE_PID=$(ss -tlnp "sport=$PORT" 2>/dev/null | grep "root" | awk '{print $7}' | grep -oP '\d+' | head -1)
    if [ -n "$ZOMBIE_PID" ]; then
        ps -p "$ZOMBIE_PID" -o state= 2>/dev/null | grep -q "^Z" && \
            echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] Killing zombie $ZOMBIE_PID" >> "$LOGFILE" && \
            kill -9 "$ZOMBIE_PID" 2>/dev/null || true
    fi
else
    # Clean zombies first
    ZOMBIES=$(ps -eo pid,state | grep "^[[:space:]]*[0-9]*[[:space:]]*Z$" || true)
    if [ -n "$ZOMBIES" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] Killing zombie processes found:" >> "$LOGFILE"
        echo "$ZOMBIES" >> "$LOGFILE"
        for PID in $(echo "$ZOMBIES" | awk '{print $1}'); do
            kill -9 "$PID" 2>/dev/null || true
        done
    fi
fi

# Start fresh
cd /home/tyson/ClaudeLocalClaw/localclaw

nohup ./venv/bin/python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 18798 \
    --app-dir ./src \
    --log-level info \
    --ws-ping-interval 30 \
    --ws-ping-timeout 10 \
    > /tmp/localclaw.log 2>&1 &

# Record PID
NEW_PID=$!
echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Started new process: PID $NEW_PID" >> "$LOGFILE"

# Wait to ensure it's running
sleep 2
ps -p "$NEW_PID" > /dev/null 2>&1 || {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Process $NEW_PID died immediately" >> "$LOGFILE"
    echo "Process failed to start. Check /tmp/localclaw.log"
}

echo "$(date '+%Y-%m-%d %H:%M:%S') [OK] LocalClaw server started on port $PORT"
tail -5 /tmp/localclaw.log