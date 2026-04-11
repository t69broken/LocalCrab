"""
AgentManager — Manages multiple concurrent AI agents.

Each agent has:
  - A unique ID and name
  - An optional persona (SOUL.md loaded system prompt)
  - A set of active skills
  - A conversation history (short-term context)
  - Access to long-term MCP memory
  - Automatic model selection per request
  - Real tool execution capabilities (terminal, files, web, etc.)

Tools are provided to the model and executed agent-side, with results
flowed back into the conversation for multi-turn task completion.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator, Optional

from tools import ToolRegistry
from tools.terminal import terminal_tool, background_tool
from tools.files import read_file_tool, write_file_tool, search_files_tool, list_dir_tool
from tools.web import web_search_tool, web_fetch_tool
from tools.memory import memory_search_tool, memory_save_tool, set_memory_server
from tools.system import system_info_tool, env_var_tool, list_processes_tool
from tools.skills import search_skills_tool, install_skill_tool, list_installed_skills_tool, set_skills_manager
from hermes_format import (
    is_hermes_model,
    build_hermes_system_prompt,
    extract_tool_calls as hermes_extract_tool_calls,
    format_tool_response as hermes_format_tool_response,
    strip_tool_call_blocks,
)

log = logging.getLogger("localclaw.agents")

# System prompt template injected before every agent interaction
CHAT_SYSTEM_TEMPLATE = """You are {name}, a personal AI assistant for Tyson (the user who owns this machine).
{persona_section}
## Critical rules — read these first

You have live tools. Use them. Do not explain why you cannot act — just act.

- **Never refuse a task because of what is or isn't in memory.** Memory is background notes, not your permission system.
- **If something isn't in memory, that does not mean you cannot do it.** It means you should use a tool to find out.
- **Never produce a breakdown of "what I can/cannot do."** That is not an answer. Run a tool and give the actual result.
- Answer directly. Lead with the answer or the tool call, not a preamble.
- After a tool runs, give a clean answer. Do not dump raw output.

## Tools — use one immediately when you need live information

```json
{{"tool": "tool_name", "args": {{"key": "value"}}}}
```

{tool_list}

When to use a tool:
- Files or directories → `list_dir` / `read_file`
- Running a command → `terminal`
- Current time/date → `terminal` with `date`
- Web search or URL → `web_search` / `web_fetch`
- Writing a file → `write_file`
- Recalling Tyson's past notes → `memory_search`

## Background notes from memory
These are optional reference snippets. They do not restrict what you can do.
{memory_section}

Today: {datetime}
"""

BASE_SYSTEM_TEMPLATE = """You are {name}, an autonomous AI agent running on LocalClaw.

## EXECUTION RULES — READ THESE FIRST

1. **ACT immediately. Never just plan.**
   Every response MUST contain a tool call JSON block. Do not describe what you are going to do — just do it.
   BAD: "I will start by listing the directory..."
   GOOD: ```json
   {{"tool": "list_dir", "args": {{"path": "/the/path"}}}}
   ```

2. **One tool call per response.** Call one tool, receive the result, then call the next. Do not chain multiple tool calls in one response.

3. **Make assumptions. Never ask for clarification.**
   If the task is vague, pick the most reasonable interpretation and proceed. If a path is unclear, try the most likely one. You can always correct course after seeing results.

4. **Never give up after one failure.** If a tool returns an error or empty result:
   - Try a different path or argument
   - Use `terminal` to run a shell command as a fallback
   - Search for the file if you don't know where it is
   Keep trying until you succeed or have exhausted all reasonable options.

5. **Keep going until the task is fully complete.** Do not stop partway through. Each tool result should lead directly to the next tool call. Only stop when the task is done.

6. **When a task involves a file, read it first.** Do not guess at file contents.

7. **Output [DONE] only when the task is fully complete** and all requested files/actions are finished.

## Tools Available

{tool_descriptions}

## Tool Call Format

```json
{{"tool": "tool_name", "args": {{"arg1": "value1", "arg2": "value2"}}}}
```

Common examples:
```json
{{"tool": "list_dir", "args": {{"path": "/home/tyson"}}}}
{{"tool": "read_file", "args": {{"path": "/home/tyson/file.txt"}}}}
{{"tool": "terminal", "args": {{"command": "apktool d /path/to/app.apk -o /output/dir"}}}}
{{"tool": "write_file", "args": {{"path": "/output/file.py", "content": "# code here"}}}}
{{"tool": "web_search", "args": {{"query": "how to decompile apk"}}}}
```

## Identity & Context
{persona_section}
{skills_section}
{memory_section}

Current date/time: {datetime}
Host system: {system_info} | Running on: {hostname}

Available models:
{available_models}
"""

SKILL_PROMPT_SECTION = """
You have the following skills available. Follow the SKILL.md instructions precisely for each:

{skill_list}
"""

MEMORY_PROMPT_SECTION = """
From your long-term memory (relevant to this conversation):
{memories}
"""


class Agent:
    def __init__(self, agent_id: str, name: str, persona=None, skills=None, preferred_model=None):
        self.agent_id = agent_id
        self.name = name
        self.persona = persona  # dict with slug, system_prompt, etc.
        self.skills: list[str] = skills or []
        self.preferred_model = preferred_model
        self.history: list[dict] = []
        self._history_loaded: bool = False  # True once loaded from SQLite
        self.created_at = time.time()
        self.last_active = time.time()
        self.stats = {"messages": 0, "model_uses": {}}

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "persona": self.persona.get("slug") if self.persona else None,
            "skills": self.skills,
            "preferred_model": self.preferred_model,
            "message_count": len(self.history),
            "created_at": self.created_at,
            "last_active": self.last_active,
            "stats": self.stats,
        }


class AgentManager:
    def __init__(self, model_selector, skills_manager, memory_server, persona_manager, gpu_manager, history_store=None):
        self.model_selector = model_selector
        self.skills_manager = skills_manager
        self.memory_server = memory_server
        self.persona_manager = persona_manager
        self.gpu_manager = gpu_manager
        self.history_store = history_store
        self._agents: dict[str, Agent] = {}
        self._global_preferred_model: Optional[str] = None
        
        # Initialize tool registry
        self.tool_registry = ToolRegistry()
        self._register_tools()
        
        # Connect memory server to memory tool
        if memory_server:
            set_memory_server(memory_server)
        
        # Connect skills manager to skills tools
        if skills_manager:
            set_skills_manager(skills_manager)
        
        # Always have a default agent
        self._ensure_default_agent()
    
    def _register_tools(self):
        """Register all available tools."""
        # Terminal tools
        self.tool_registry.register(terminal_tool)
        self.tool_registry.register(background_tool)
        
        # File tools
        self.tool_registry.register(read_file_tool)
        self.tool_registry.register(write_file_tool)
        self.tool_registry.register(search_files_tool)
        self.tool_registry.register(list_dir_tool)
        
        # Web tools
        self.tool_registry.register(web_search_tool)
        self.tool_registry.register(web_fetch_tool)
        
        # Memory tools
        self.tool_registry.register(memory_search_tool)
        self.tool_registry.register(memory_save_tool)
        
        # System tools
        self.tool_registry.register(system_info_tool)
        self.tool_registry.register(env_var_tool)
        self.tool_registry.register(list_processes_tool)
        
        # Skills tools
        self.tool_registry.register(search_skills_tool)
        self.tool_registry.register(install_skill_tool)
        self.tool_registry.register(list_installed_skills_tool)
        
        log.info(f"Registered {len(self.tool_registry._tools)} tools")

    def _ensure_default_agent(self):
        if "default" not in self._agents:
            self._agents["default"] = Agent(
                agent_id="default",
                name="LocalClaw Assistant",
                preferred_model=self._global_preferred_model,  # Use global if set
            )

    async def _load_history(self, agent: Agent):
        """
        Restore the last N messages from SQLite into agent.history on first use.
        This lets conversations continue across server restarts.
        Only runs once per agent instance (guarded by _history_loaded).
        """
        if agent._history_loaded:
            return
        agent._history_loaded = True
        if not self.history_store:
            return
        limit = int(os.environ.get("HISTORY_CONTEXT_MESSAGES", "50"))
        rows = await self.history_store.load(agent.agent_id, limit=limit)
        if rows:
            agent.history = [{"role": r["role"], "content": r["content"]} for r in rows]
            log.info(f"Restored {len(rows)} messages into agent {agent.agent_id} context")

    async def create_agent(
        self,
        name: str,
        persona_slug: Optional[str] = None,
        skills: Optional[list[str]] = None,
        preferred_model: Optional[str] = None,
    ) -> dict:
        agent_id = str(uuid.uuid4())[:8]
        persona = None
        if persona_slug:
            persona = self.persona_manager.get_persona(persona_slug)
            if not persona:
                # Try to install it
                try:
                    result = await self.persona_manager.install_soul(persona_slug)
                    persona = self.persona_manager.get_persona(persona_slug)
                except Exception:
                    pass

        agent = Agent(
            agent_id=agent_id,
            name=name,
            persona=persona,
            skills=skills or [],
            preferred_model=preferred_model,
        )
        self._agents[agent_id] = agent
        log.info(f"Created agent {agent_id}: {name} (persona={persona_slug})")
        return agent.to_dict()

    def get_agent(self, agent_id: str) -> Optional[dict]:
        agent = self._agents.get(agent_id)
        return agent.to_dict() if agent else None

    def list_agents(self) -> list[dict]:
        return [a.to_dict() for a in self._agents.values()]

    def delete_agent(self, agent_id: str) -> bool:
        if agent_id == "default":
            return False  # Cannot delete default
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False

    async def reset_agent(self, agent_id: str) -> bool:
        """Clear the active context window for this agent (starts a fresh session).
        The history log in SQLite is preserved — use clear_agent_history() to wipe that."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        agent.history = []
        agent._history_loaded = True
        log.info(f"Reset agent {agent_id} context (history log preserved)")
        return True

    async def clear_agent_history(self, agent_id: str) -> int:
        """Permanently delete the stored history log for this agent."""
        agent = self._agents.get(agent_id)
        if agent:
            agent.history = []
            agent._history_loaded = True
        n = 0
        if self.history_store:
            n = await self.history_store.clear(agent_id)
        log.info(f"Cleared {n} history messages for agent {agent_id}")
        return n

    async def set_preferred_model(self, agent_id: str, model: Optional[str]) -> bool:
        """Set the preferred model for a specific agent. Pass None to clear."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False
        agent.preferred_model = model
        log.info(f"Set preferred model for agent {agent_id}: {model}")
        return True

    def set_global_preferred_model(self, model: str):
        """Set the global preferred model (applied to new default agent sessions)."""
        self._global_preferred_model = model
        # Also apply to current default agent
        if "default" in self._agents:
            self._agents["default"].preferred_model = model
        log.info(f"Set global preferred model: {model}")

    async def _build_chat_system_prompt(self, agent: Agent, query: Optional[str] = None) -> str:
        """System prompt for conversational chat — direct, tool-aware, no forced agentic loop."""
        import datetime
        persona_section = ""
        if agent.persona:
            soul_md = agent.persona.get("soul_md", "")
            if soul_md:
                persona_section = f"\nYour persona:\n{soul_md}\n"
        memory_section = "(none)"
        try:
            if query:
                results = await self.memory_server.search(query, limit=4)
            else:
                results = await self.memory_server.list_memories(agent_id=None, limit=4)
            if results:
                lines = []
                for m in results:
                    content = m.get('content', '')[:300]
                    lines.append(f"• {content}")
                memory_section = "\n".join(lines)
        except Exception:
            pass
        # Full tool list with parameter signatures so the model knows arg names
        tool_list = self.tool_registry.get_system_prompt() or "(none)"
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return CHAT_SYSTEM_TEMPLATE.format(
            name=agent.name,
            persona_section=persona_section,
            memory_section=memory_section,
            tool_list=tool_list,
            datetime=now,
        )

    async def _build_system_prompt(self, agent: Agent, context_id: Optional[str]) -> str:
        import datetime
        import platform
        import socket

        # Persona section
        persona_section = ""
        if agent.persona:
            soul_md = agent.persona.get("soul_md", "")
            if soul_md:
                persona_section = f"Your persona and character:\n{soul_md}"

        # Skills section
        skill_contents = []
        for slug in agent.skills:
            skill = self.skills_manager.get_skill(slug)
            if skill:
                preview = skill.get("content", "")[:500]
                skill_contents.append(f"### Skill: {slug}\n{preview}")
        skills_section = ""
        if skill_contents:
            skills_section = SKILL_PROMPT_SECTION.format(
                skill_list="\n\n".join(skill_contents)
            )

        # Memory section
        memory_section = ""
        try:
            recent = await self.memory_server.list_memories(
                agent_id=agent.agent_id, limit=5
            )
            if recent:
                mem_lines = []
                for m in recent:
                    mem_lines.append(f"- [{m.get('type', 'fact')}] {m.get('content', '')}")
                memory_section = MEMORY_PROMPT_SECTION.format(
                    memories="\n".join(mem_lines)
                )
        except Exception:
            pass

        # System info
        try:
            hostname = socket.gethostname()
            system_info = f"{platform.system()} {platform.release()}"
        except Exception:
            hostname = "unknown"
            system_info = "unknown"

        # Available models
        models = await self.model_selector.list_models()
        model_lines = []
        for m in models[:10]:  # Limit to 10
            caps = m.get("capabilities", {})
            best_for = max(caps.items(), key=lambda x: x[1])[0] if caps else "general"
            model_lines.append(f"- **{m['name']}** ({m.get('size_gb', 0):.1f}GB) — best for {best_for}")
        available_models = "\n".join(model_lines) if model_lines else "(No models pulled yet)"

        # Tool descriptions from registry
        tool_descriptions = self.tool_registry.get_system_prompt()

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        return BASE_SYSTEM_TEMPLATE.format(
            name=agent.name,
            persona_section=persona_section,
            skills_section=skills_section,
            memory_section=memory_section,
            datetime=now,
            system_info=system_info,
            hostname=hostname,
            available_models=available_models,
            tool_descriptions=tool_descriptions,
        )

    def _extract_tool_call(self, response: str) -> Optional[dict]:
        """Extract a tool call JSON from the model response.

        Supports:
        1. Native Ollama tool_calls (handled separately via _extract_native_tool_call)
        2. Hermes XML: <tool_call>{"name":...,"arguments":{...}}</tool_call>
        3. Markdown code fence:  ```json\n{"tool": "...", "args": {...}}\n```
        4. Bare JSON object anywhere in the response
        """
        # Hermes XML format (highest priority for text-based detection)
        hermes_calls = hermes_extract_tool_calls(response)
        if hermes_calls:
            return hermes_calls[0]  # caller handles parallel calls separately

        # Code fences (most explicit signal from the model)
        for m in re.finditer(r'```(?:json)?\s*([\s\S]*?)\s*```', response, re.IGNORECASE):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    if "tool" in data:
                        return {"tool": data["tool"], "args": data.get("args", {})}
                    if "name" in data and "arguments" in data:
                        return {"tool": data["name"], "args": data["arguments"]}
            except (json.JSONDecodeError, ValueError):
                continue

        # Fall back: scan for brace-balanced JSON objects containing "tool" or "name"+"arguments"
        for m in re.finditer(r'\{', response):
            start = m.start()
            depth = 0
            for i, ch in enumerate(response[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(response[start:i + 1])
                            if isinstance(data, dict):
                                if "tool" in data:
                                    return {"tool": data["tool"], "args": data.get("args", {})}
                                # OpenAI-style: {"name": "...", "arguments": {...}}
                                if "name" in data and "arguments" in data:
                                    return {"tool": data["name"], "args": data["arguments"]}
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break
        return None

    def _extract_all_tool_calls(self, response: str) -> list[dict]:
        """Extract ALL tool calls from a response (for parallel Hermes calls)."""
        hermes_calls = hermes_extract_tool_calls(response)
        if hermes_calls:
            return hermes_calls
        single = self._extract_tool_call(response)
        return [single] if single else []

    def _infer_first_step(self, response: str, conversation: list) -> Optional[tuple]:
        """
        When the model describes an action but won't emit a tool call,
        infer the most likely first tool from its planning text + conversation.
        Returns (tool_name, args) or None.
        """
        import re
        # Collect all text to scan — planning response + original user message
        user_msgs = " ".join(m.get("content", "") for m in conversation if m.get("role") == "user")
        full_text = response + " " + user_msgs
        text_lower = full_text.lower()

        # Extract all unix-style paths mentioned
        paths = re.findall(r'(/[\w./\-]+)', full_text)
        # Prefer paths that look like directories (no extension or trailing slash)
        dir_paths = [p for p in paths if '.' not in p.rsplit('/', 1)[-1] or p.endswith('/')]

        if any(w in text_lower for w in ['list', 'explore', 'ls', 'directory', 'folder', 'structure', 'files']):
            path = dir_paths[0] if dir_paths else (paths[0] if paths else '/home')
            return 'list_dir', {'path': path}
        if any(w in text_lower for w in ['read', 'examine', 'open', 'view', 'look at', 'check']):
            if paths:
                return 'read_file', {'path': paths[0]}
        if any(w in text_lower for w in ['run', 'execute', 'terminal', 'shell', 'command']):
            return 'terminal', {'command': f'ls -la {dir_paths[0] if dir_paths else "/home"}'}
        if any(w in text_lower for w in ['search', 'web', 'internet', 'online']):
            m = re.search(r'search (?:for |online )?["\']?([^"\'\n.]{5,60})', text_lower)
            return 'web_search', {'query': m.group(1) if m else response[:80]}

        # Default: list the first dir path mentioned, or home
        return 'list_dir', {'path': dir_paths[0] if dir_paths else '/home'}

    def _extract_native_tool_call(self, response_dict: dict) -> Optional[dict]:
        """Extract native Ollama tool call from response."""
        message = response_dict.get("message", {})
        tool_calls = message.get("tool_calls", [])
        if tool_calls:
            tc = tool_calls[0]
            func = tc.get("function", {})
            return {
                "tool": func.get("name", ""),
                "args": func.get("arguments", {}),
            }
        return None

    def _resolve_tool_name(self, tool_name: str) -> str:
        """Resolve a model-generated tool name to a registered tool name.

        Some models (e.g. Gemma 4) emit their own internal capability names
        ($language_runtime.processes.info, dashboard/processes, $python, etc.)
        rather than the tools we registered. This method uses keyword matching
        to find the closest registered tool.
        """
        if tool_name in self.tool_registry._tools:
            return tool_name

        key = tool_name.lower().replace("$", "").replace(".", " ").replace("/", " ").replace("_", " ")

        rules: list[tuple[tuple[str, ...], str]] = [
            # Terminal / shell execution
            (("bash", "shell", "exec", "run", "terminal", "command", "python", "code", "script"), "terminal"),
            # Process listing
            (("process", "processes", "ps", "task", "tasks", "dashboard"), "list_processes"),
            # Web search
            (("search", "google", "bing", "query", "internet"), "web_search"),
            # Web fetch
            (("fetch", "browser", "browse", "url", "http", "web page", "website"), "web_fetch"),
            # File ops
            (("read file", "open file", "get file", "cat"), "read_file"),
            (("list dir", "list files", "ls ", "directory", "folder"), "list_dir"),
            (("write file", "save file", "create file"), "write_file"),
            (("find file", "search file", "grep"), "search_files"),
            # System / environment
            (("system info", "sysinfo", "hardware", "memory info", "cpu"), "system_info"),
            (("env", "environ", "variable"), "env_var"),
            # Memory
            (("memory", "remember", "recall", "knowledge"), "memory_search"),
        ]

        for keywords, mapped in rules:
            if any(kw in key for kw in keywords):
                log.info(f"Fuzzy-mapped unknown tool {tool_name!r} → {mapped!r}")
                return mapped

        log.warning(f"No translation found for unknown tool: {tool_name!r}")
        return tool_name  # let execute() return the "unknown tool" error

    async def _execute_tool(self, tool_name: str, args: dict) -> dict:
        """Execute a tool and return formatted result."""
        tool_name = self._resolve_tool_name(tool_name)
        result = await self.tool_registry.execute(tool_name, args)
        return {
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }

    async def chat(
        self,
        agent_id: str,
        messages: list[dict],
        task_hint: Optional[str] = None,
        persona: Optional[str] = None,
        context_id: Optional[str] = None,
        max_tool_iterations: int = 30,
    ) -> dict:
        """Chat with the agent, supporting multi-turn tool execution."""
        agent = self._agents.get(agent_id)
        if not agent:
            agent = self._agents["default"]

        # Restore history from SQLite on first use
        await self._load_history(agent)

        model, reason = await self.model_selector.select_model(
            task=task_hint or "chat",
            preferred=agent.preferred_model,
        )
        system = await self._build_system_prompt(agent, context_id)

        # Build conversation for this turn
        conversation = agent.history[-20:] + messages

        # Get tool definitions for models that support them
        tools = self.tool_registry.list_tools()

        # Hermes XML format: inject tools into system prompt, skip native tools API
        use_hermes = is_hermes_model(model)
        if use_hermes:
            log.info(f"[Chat] Using Hermes XML tool-calling format for model: {model}")
            system = build_hermes_system_prompt(system, tools)
            native_tools_param = None
        else:
            native_tools_param = tools if tools else None

        # Track tool calls for response
        tool_calls = []

        # Agentic loop: generate, check for tools, execute, repeat
        pushed_to_act = False
        for iteration in range(max_tool_iterations):
            full_response = ""
            response_chunk = None
            async for chunk in self.model_selector.generate(
                model=model,
                messages=conversation,
                system=system,
                stream=False,
                tools=native_tools_param,
            ):
                full_response = chunk.get("message", {}).get("content", "")
                response_chunk = chunk

            # Hermes: extract all <tool_call> blocks (supports parallel calls)
            if use_hermes:
                hermes_calls = hermes_extract_tool_calls(full_response)
                if hermes_calls:
                    response_blocks = []
                    for tc in hermes_calls:
                        t_name, t_args = tc["tool"], tc.get("args", {})
                        log.info(f"[Hermes/Chat] Executing tool: {t_name}({t_args})")
                        t_result = await self._execute_tool(t_name, t_args)
                        tool_calls.append({"tool": t_name, "args": t_args, "result": t_result})
                        content = t_result["output"] if t_result["success"] else f"Error: {t_result['error']}"
                        response_blocks.append(hermes_format_tool_response(t_name, content))
                    conversation.append({"role": "assistant", "content": full_response})
                    conversation.append({"role": "user", "content": "\n".join(response_blocks)})
                    continue
                # No tool calls — done
                if "[DONE]" in full_response or "[done]" in full_response.lower():
                    break
                if full_response.strip():
                    break
                break

            # Non-Hermes: native tool_calls first, then text-based JSON
            tool_call = self._extract_native_tool_call(response_chunk) if response_chunk else None
            if not tool_call:
                tool_call = self._extract_tool_call(full_response)

            if not tool_call:
                if "[DONE]" in full_response or "[done]" in full_response.lower():
                    break
                if full_response.strip() and not pushed_to_act:
                    pushed_to_act = True
                    conversation.append({"role": "assistant", "content": full_response})
                    conversation.append({"role": "user", "content":
                        "Good. Now execute the first step using the appropriate tool. "
                        "Output a JSON tool call block exactly like:\n"
                        "```json\n{\"tool\": \"tool_name\", \"args\": {\"key\": \"value\"}}\n```\n"
                        "When you have fully completed the task, output [DONE]."
                    })
                    continue
                break

            # Execute the tool
            tool_name = tool_call["tool"]
            tool_args = tool_call.get("args", {})

            log.info(f"Executing tool: {tool_name}({tool_args})")
            tool_result = await self._execute_tool(tool_name, tool_args)

            tool_calls.append({
                "tool": tool_name,
                "args": tool_args,
                "result": tool_result,
            })

            # Check if tool needs confirmation (for dangerous operations)
            tool_def = self.tool_registry.get(tool_name)
            if tool_def and tool_def.requires_confirmation:
                log.warning(f"Auto-approved dangerous tool: {tool_name}")

            # Add assistant's tool call to conversation
            conversation.append({"role": "assistant", "content": full_response})

            # Add tool result as user message
            result_text = f"Tool result for {tool_name}:\n"
            if tool_result["success"]:
                result_text += tool_result["output"]
            else:
                result_text += f"Error: {tool_result['error']}"
            conversation.append({"role": "user", "content": result_text})

            # After a real tool execution, treat the model's next plain-text
            # response as the final answer rather than pushing it to act again.
            pushed_to_act = True

        # Update agent history
        for m in messages:
            agent.history.append(m)
        agent.history.append({"role": "assistant", "content": full_response})
        agent.last_active = time.time()
        agent.stats["messages"] += 1
        agent.stats["model_uses"][model] = agent.stats["model_uses"].get(model, 0) + 1

        # Persist exchange to SQLite history store
        if self.history_store and full_response:
            asyncio.create_task(
                self.history_store.append_exchange(
                    agent_id=agent.agent_id,
                    messages=messages,
                    response=full_response,
                    model=model,
                )
            )

        # Auto-save important facts to memory
        asyncio.create_task(
            self._auto_memorize(agent, messages, full_response)
        )

        return {
            "agent_id": agent_id,
            "model": model,
            "model_reason": reason,
            "content": full_response,
            "history_length": len(agent.history),
            "tool_calls": tool_calls if tool_calls else None,
        }

    async def stream_chat(
        self,
        agent_id: str,
        messages: list[dict],
        task_hint: Optional[str] = None,
        context_id: Optional[str] = None,
        max_tool_iterations: int = 30,
        model_override: Optional[str] = None,
        chat_only: bool = False,
        num_ctx: Optional[int] = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream chat with tool execution support.
        chat_only=True uses a tighter loop (max 6 iterations) with no tool-forcing.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            agent = self._agents["default"]

        # Restore history from SQLite on first use (survives container restarts)
        await self._load_history(agent)

        if model_override:
            model, reason = model_override, "user selection"
            log.info(f"[Stream] Using model override: {model}")
        else:
            log.info(f"[Stream] Agent '{agent_id}' preferred_model: {agent.preferred_model}")
            model, reason = await self.model_selector.select_model(
                task=task_hint or "chat",
                preferred=agent.preferred_model,
            )
        # Extract the user's latest message to use for memory retrieval
        user_query = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            None
        )
        if chat_only:
            system = await self._build_chat_system_prompt(agent, query=user_query)
        else:
            system = await self._build_system_prompt(agent, context_id)
        log.info(f"[Stream] Using model {model} (reason: {reason}) chat_only={chat_only}")

        yield {"type": "model", "model": model, "reason": reason}

        # Memory is already in the system prompt — do not duplicate into user messages.
        # Injecting memory into user messages causes models to treat memory as their
        # only allowed input and refuse to use tools for anything not covered by it.
        messages_with_context = list(messages)

        # Build conversation
        conversation = agent.history[-20:] + messages_with_context

        # Get tool definitions
        tools = self.tool_registry.list_tools()

        # Hermes XML format: inject tools into system prompt, skip native tools API
        use_hermes = is_hermes_model(model)
        if use_hermes:
            log.info(f"[Stream] Using Hermes XML tool-calling format for model: {model}")
            system = build_hermes_system_prompt(system, tools)
            native_tools_param = None  # tools are in system prompt, not API param
        else:
            native_tools_param = tools if tools else None

        # Agentic loop for tools
        # Chat mode caps at 6 iterations (tool + synthesize = 2 per round, 3 rounds max)
        loop_limit = 6 if chat_only else max_tool_iterations
        pushed_to_act = False  # track if we already nudged the model once
        total_input_tokens = 0
        total_output_tokens = 0
        total_eval_duration_ns = 0  # nanoseconds spent generating tokens
        for iteration in range(loop_limit):
            full_response = ""
            last_chunk = None
            native_tool_call = None  # collect from any chunk, not just last

            async for chunk in self.model_selector.generate(
                model=model,
                messages=conversation,
                system=system,
                stream=True,
                tools=native_tools_param,
                num_ctx=num_ctx,
            ):
                msg = chunk.get("message", {})
                delta = msg.get("content", "")
                thinking = msg.get("thinking", "")
                last_chunk = chunk

                # Collect native tool call from whichever chunk carries it
                if not native_tool_call and msg.get("tool_calls"):
                    tc = msg["tool_calls"][0]
                    func = tc.get("function", {})
                    native_tool_call = {
                        "tool": func.get("name", ""),
                        "args": func.get("arguments", {}),
                    }

                if thinking:
                    # Internal reasoning — suppress from UI
                    pass
                elif delta:
                    full_response += delta
                    yield {"type": "delta", "content": delta}

                if chunk.get("done"):
                    total_input_tokens += chunk.get("prompt_eval_count", 0)
                    total_output_tokens += chunk.get("eval_count", 0)
                    total_eval_duration_ns += chunk.get("eval_duration", 0)
                    log.info(f"[Stream] Model finished, full_response={len(full_response)}chars native_tool={native_tool_call is not None}")
                    break

            # Prefer native tool call, fall back to text-based detection
            # Hermes models: extract all calls (supports parallel) from text
            if use_hermes:
                hermes_calls = hermes_extract_tool_calls(full_response)
                if hermes_calls:
                    # Execute all parallel tool calls, collect <tool_response> blocks
                    prose = strip_tool_call_blocks(full_response)
                    if prose:
                        yield {"type": "delta", "content": ""}  # flush any buffered delta
                    response_blocks = []
                    for tc in hermes_calls:
                        t_name = tc["tool"]
                        t_args = tc.get("args", {})
                        log.info(f"[Hermes] Executing tool: {t_name}({t_args})")
                        yield {"type": "tool_call", "tool": t_name, "args": t_args}
                        t_result = await self._execute_tool(t_name, t_args)
                        yield {
                            "type": "tool_result",
                            "success": t_result["success"],
                            "output": t_result.get("output", ""),
                            "error": t_result.get("error"),
                        }
                        content = t_result["output"] if t_result["success"] else f"Error: {t_result['error']}"
                        response_blocks.append(hermes_format_tool_response(t_name, content))
                    # Feed all results back as a single user turn
                    conversation.append({"role": "assistant", "content": full_response})
                    conversation.append({"role": "user", "content": "\n".join(response_blocks)})
                    continue
                # No Hermes tool calls found — treat as final text response
                tool_call = None
            else:
                tool_call = native_tool_call or self._extract_tool_call(full_response)

            if not tool_call:
                # Model said it's done explicitly
                if "[DONE]" in full_response or "[done]" in full_response.lower():
                    log.info(f"[Stream] Agent signalled [DONE]")
                    break

                # In chat-only mode, a plain text response is the final answer — never force tools
                if chat_only:
                    break

                # Model planned but didn't act — push it once to actually execute
                if full_response.strip() and not pushed_to_act:
                    pushed_to_act = True
                    log.info(f"[Stream] Model planned without acting, pushing to execute (iteration {iteration})")
                    conversation.append({"role": "assistant", "content": full_response})
                    conversation.append({"role": "user", "content":
                        "Good. Now execute the first step using the appropriate tool. "
                        "Output a JSON tool call block exactly like:\n"
                        "```json\n{\"tool\": \"tool_name\", \"args\": {\"key\": \"value\"}}\n```\n"
                        "When you have fully completed the task, output [DONE]."
                    })
                    continue

                # Still planning after push — infer and auto-execute the first step
                if full_response.strip() and pushed_to_act:
                    inferred = self._infer_first_step(full_response, conversation)
                    if inferred:
                        tool_name, tool_args = inferred
                        log.info(f"[Stream] Auto-executing inferred first step: {tool_name}({tool_args})")
                        yield {"type": "tool_call", "tool": tool_name, "args": tool_args}
                        tool_result = await self._execute_tool(tool_name, tool_args)
                        yield {
                            "type": "tool_result",
                            "success": tool_result["success"],
                            "output": tool_result.get("output", ""),
                            "error": tool_result.get("error"),
                        }
                        conversation.append({"role": "assistant", "content": full_response})
                        result = tool_result["output"] if tool_result["success"] else f"Error: {tool_result['error']}"
                        conversation.append({"role": "user", "content":
                            f"I ran {tool_name} for you. Here is the result:\n{result}\n\n"
                            "Continue the task from here. Use tool calls for each action."
                        })
                        pushed_to_act = False  # allow re-push if needed
                        continue

                # No tool call, no actionable plan — done
                break

            # Execute the tool
            tool_name = tool_call["tool"]
            tool_args = tool_call.get("args", {})

            log.info(f"Executing tool in stream: {tool_name}({tool_args})")
            yield {"type": "tool_call", "tool": tool_name, "args": tool_args}

            tool_result = await self._execute_tool(tool_name, tool_args)

            yield {
                "type": "tool_result",
                "success": tool_result["success"],
                "output": tool_result.get("output", ""),
                "error": tool_result.get("error"),
            }

            # Update conversation for next iteration
            conversation.append({"role": "assistant", "content": full_response})
            if tool_result["success"]:
                result_text = tool_result["output"]
                if chat_only:
                    result_text = f"[{tool_name} result]\n{result_text}\nNow give a clean, direct answer based on this."
            else:
                result_text = f"[{tool_name} error: {tool_result['error']}]\nExplain briefly what went wrong."
            conversation.append({"role": "user", "content": result_text})

            # After executing a real tool, treat the model's next plain-text
            # response as the final answer rather than pushing it to act again.
            pushed_to_act = True

        # Emit token usage for this exchange
        if total_input_tokens or total_output_tokens:
            tps = round(total_output_tokens / (total_eval_duration_ns / 1e9), 1) if total_eval_duration_ns else None
            yield {
                "type": "tokens",
                "model": model,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "tokens_per_second": tps,
            }

        # Persist to in-memory history
        for m in messages:
            agent.history.append(m)
        agent.history.append({"role": "assistant", "content": full_response})
        agent.last_active = time.time()
        agent.stats["messages"] += 1
        agent.stats["model_uses"][model] = agent.stats["model_uses"].get(model, 0) + 1

        # Persist exchange to SQLite history store
        if self.history_store and full_response:
            asyncio.create_task(
                self.history_store.append_exchange(
                    agent_id=agent.agent_id,
                    messages=messages,
                    response=full_response,
                    model=model,
                )
            )

        asyncio.create_task(
            self._auto_memorize(agent, messages, full_response)
        )

    async def _auto_memorize(self, agent: Agent, messages: list[dict], response: str):
        """
        Heuristically decide if any information from this exchange is worth
        saving to long-term memory.
        """
        try:
            # Look for explicit memory cues in the user message
            user_text = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")

            memory_triggers = [
                "remember", "note that", "my name is", "i am ", "i'm ",
                "i prefer", "i like", "i work", "always ", "never ",
                "important:", "save this", "keep in mind",
            ]

            should_save = any(t in user_text.lower() for t in memory_triggers)

            if should_save:
                await self.memory_server.save_memory(
                    agent_id=agent.agent_id,
                    content=user_text[:500],
                    memory_type="user_fact",
                    source="auto",
                )
                log.debug(f"Auto-saved memory for agent {agent.agent_id}")
        except Exception as e:
            log.debug(f"Auto-memorize failed: {e}")

    async def build_system_prompt_for_job(self, agent_id: str, task: str = "") -> str:
        """Public wrapper used by TaskRegistry when launching a watched job."""
        agent = self._agents.get(agent_id) or self._agents["default"]
        base = await self._build_system_prompt(agent, context_id=None)
        if task:
            base += f"\n\n## Current Task\n{task}\n\n"
            base += (
                "Start executing immediately. Your first response must be a tool call — "
                "not a plan, not a description. Just the JSON tool call block.\n"
                "When the task is fully complete, output [DONE]."
            )
        return base
