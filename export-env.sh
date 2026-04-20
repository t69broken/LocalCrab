#!/bin/bash
# Export environment variables from .env before launching LocalClaw
cd /home/tyson/ClaudeLocalClaw/localclaw
grep -v '^#' ./.env | xargs
uvicorn src.main:app --host 0.0.0.0 --port 18798
