"""
TelegramBot — Telegram messaging interface for LocalClaw.

Set TELEGRAM_BOT_TOKEN in .env to enable, or configure via the Communication tab in the UI.
Set TELEGRAM_ALLOWED_USERS to a comma-separated list of Telegram user IDs
to restrict access (leave blank to allow anyone).

Commands:
  /start   — greeting and help
  /agents  — list available agents
  /agent <id> — switch to a specific agent
  /clear   — reset context for current agent
  /status  — show GPU and model status
"""

import asyncio
import json
import logging
import os
from typing import Optional

log = logging.getLogger("localclaw.telegram")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ALLOWED_USERS = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()

# Runtime config file — overrides env vars when present
_CONFIG_PATH = os.environ.get("COMMS_CONFIG", "/app/data/comms_config.json")

# Telegram message length limit
_TG_MAX_LEN = 4000


def _load_config() -> dict:
    """Load runtime comms config from JSON file (overrides env vars)."""
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(token: str, allowed_users: str) -> None:
    """Persist Telegram settings to the runtime config file."""
    import os
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    cfg = _load_config()
    cfg["telegram_token"] = token.strip()
    cfg["telegram_allowed_users"] = allowed_users.strip()
    with open(_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    log.info("Telegram config saved to %s", _CONFIG_PATH)


def _split_message(text: str, max_len: int = _TG_MAX_LEN) -> list[str]:
    """Split long text into Telegram-sized chunks, preferring newline boundaries."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split on a newline near the limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class TelegramBot:
    def __init__(self, agent_manager):
        self.agent_manager = agent_manager
        self._app = None
        # Map telegram chat_id -> agent_id so each chat can target a different agent
        self._chat_agents: dict[int, str] = {}
        self._allowed_users: set[int] = set()
        self._token: str = ""
        self._running: bool = False
        self._bot_username: str = ""

    def get_status(self) -> dict:
        cfg = _load_config()
        token = cfg.get("telegram_token") or TELEGRAM_TOKEN
        allowed = cfg.get("telegram_allowed_users") or TELEGRAM_ALLOWED_USERS
        # Mask the token for display
        masked = ""
        if token:
            parts = token.split(":")
            masked = f"{parts[0]}:***" if len(parts) == 2 else "***"
        return {
            "running": self._running,
            "token_set": bool(token),
            "token_masked": masked,
            "allowed_users": allowed,
            "bot_username": self._bot_username,
        }

    async def restart(self, token: str = "", allowed_users: str = "") -> dict:
        """Stop any running bot and start fresh with new or existing config."""
        await self.stop()
        if token or allowed_users:
            save_config(token, allowed_users)
        return await self.start()

    async def start(self) -> dict:
        # Config file overrides env vars
        cfg = _load_config()
        token = cfg.get("telegram_token") or TELEGRAM_TOKEN
        allowed_users_str = cfg.get("telegram_allowed_users") or TELEGRAM_ALLOWED_USERS

        if not token:
            log.info("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
            return {"ok": False, "error": "No token configured"}

        try:
            from telegram.ext import (
                ApplicationBuilder,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            msg = "python-telegram-bot not installed"
            log.error(msg)
            return {"ok": False, "error": msg}

        # Parse allowed user whitelist
        self._allowed_users = set()
        if allowed_users_str:
            for uid in allowed_users_str.split(","):
                uid = uid.strip()
                if uid.isdigit():
                    self._allowed_users.add(int(uid))
            log.info(f"Telegram whitelist: {self._allowed_users}")
        else:
            log.warning("TELEGRAM_ALLOWED_USERS not set — bot is open to anyone with the link")

        try:
            app = ApplicationBuilder().token(token).build()
            app.add_handler(CommandHandler("start", self._cmd_start))
            app.add_handler(CommandHandler("agents", self._cmd_agents))
            app.add_handler(CommandHandler("agent", self._cmd_agent))
            app.add_handler(CommandHandler("clear", self._cmd_clear))
            app.add_handler(CommandHandler("status", self._cmd_status))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

            self._app = app
            self._token = token
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            # Fetch bot username for display
            try:
                me = await app.bot.get_me()
                self._bot_username = me.username or ""
            except Exception:
                self._bot_username = ""
            self._running = True
            log.info(f"Telegram bot @{self._bot_username} started and polling")
            return {"ok": True, "bot_username": self._bot_username}
        except Exception as e:
            log.error(f"Telegram bot failed to start: {e}")
            self._running = False
            return {"ok": False, "error": str(e)}

    async def stop(self):
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                log.warning(f"Telegram bot shutdown error: {e}")
            self._app = None
            self._running = False
            log.info("Telegram bot stopped")

    # ── Authorization ─────────────────────────────────────────────────────────

    def _is_allowed(self, user_id: int) -> bool:
        if not self._allowed_users:
            return True
        return user_id in self._allowed_users

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _cmd_start(self, update, context):
        if not self._is_allowed(update.effective_user.id):
            return
        agent_id = self._chat_agents.get(update.effective_chat.id, "default")
        await update.message.reply_text(
            f"LocalClaw AI ready.\n"
            f"Current agent: `{agent_id}`\n\n"
            f"Commands:\n"
            f"/agents — list all agents\n"
            f"/agent <id> — switch agent\n"
            f"/clear — reset current agent context\n"
            f"/status — GPU & model info\n\n"
            f"Just send a message to chat.",
            parse_mode="Markdown",
        )

    async def _cmd_agents(self, update, context):
        if not self._is_allowed(update.effective_user.id):
            return
        agents = self.agent_manager.list_agents()
        current = self._chat_agents.get(update.effective_chat.id, "default")
        lines = []
        for a in agents:
            marker = "▶" if a["agent_id"] == current else "•"
            lines.append(f"{marker} {a['name']} — `{a['agent_id']}`")
        await update.message.reply_text(
            "Available agents:\n" + "\n".join(lines),
            parse_mode="Markdown",
        )

    async def _cmd_agent(self, update, context):
        if not self._is_allowed(update.effective_user.id):
            return
        args = context.args
        if not args:
            current = self._chat_agents.get(update.effective_chat.id, "default")
            await update.message.reply_text(f"Current agent: `{current}`", parse_mode="Markdown")
            return
        agent_id = args[0].strip()
        agent = self.agent_manager.get_agent(agent_id)
        if not agent:
            await update.message.reply_text(f"Agent `{agent_id}` not found. Use /agents to list.", parse_mode="Markdown")
            return
        self._chat_agents[update.effective_chat.id] = agent_id
        await update.message.reply_text(
            f"Switched to: *{agent['name']}* (`{agent_id}`)",
            parse_mode="Markdown",
        )

    async def _cmd_clear(self, update, context):
        if not self._is_allowed(update.effective_user.id):
            return
        chat_id = update.effective_chat.id
        agent_id = self._chat_agents.get(chat_id, "default")
        await self.agent_manager.reset_agent(agent_id)
        await update.message.reply_text(f"Context cleared for agent `{agent_id}`.", parse_mode="Markdown")

    async def _cmd_status(self, update, context):
        if not self._is_allowed(update.effective_user.id):
            return
        try:
            from gpu_manager import GPUManager
            gpu = self.agent_manager.gpu_manager.get_status()
            models = await self.agent_manager.model_selector.list_models()
            model_names = ", ".join(m["name"] for m in models[:5]) or "none"
            vram = gpu.get("vram_used_mb", 0)
            vram_total = gpu.get("vram_total_mb", 0)
            util = gpu.get("utilization_pct", 0)
            await update.message.reply_text(
                f"*GPU*: {util}% util, {vram}/{vram_total} MB VRAM\n"
                f"*Models*: {model_names}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"Status error: {e}")

    # ── Outbound messaging ────────────────────────────────────────────────────

    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a message to a specific chat. Returns True on success."""
        if not self._running or not self._app:
            return False
        try:
            for part in _split_message(text):
                await self._app.bot.send_message(chat_id=chat_id, text=part, parse_mode="Markdown")
            return True
        except Exception as e:
            log.warning(f"Heartbeat send_message failed: {e}")
            return False

    # ── Message handler ───────────────────────────────────────────────────────

    async def _handle_message(self, update, context):
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            log.warning(f"Telegram: rejected message from user {user_id}")
            return

        chat_id = update.effective_chat.id
        agent_id = self._chat_agents.get(chat_id, "default")
        user_text = update.message.text

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            full_response = ""
            messages = [{"role": "user", "content": user_text}]

            async for chunk in self.agent_manager.stream_chat(
                agent_id=agent_id,
                messages=messages,
                chat_only=True,
            ):
                if chunk.get("type") == "delta":
                    full_response += chunk.get("content", "")

            if not full_response.strip():
                full_response = "(no response)"

            for part in _split_message(full_response):
                await update.message.reply_text(part)

        except Exception as e:
            log.error(f"Telegram message handler error: {e}", exc_info=True)
            await update.message.reply_text(f"Error: {str(e)[:300]}")
