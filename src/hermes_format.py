"""
hermes_format.py — Hermes XML tool-calling format adapter for LocalClaw.

Implements the NousResearch Hermes agent tool-calling convention:
  - Tool definitions embedded in system prompt as <tools>[...]</tools>
  - Model outputs tool calls as <tool_call>{"name":...,"arguments":{...}}</tool_call>
  - Tool results fed back as <tool_response>{"name":...,"content":...}</tool_response>

This bypasses the native Ollama/OpenAI tool_calls API and works with any
text-generation model, including Hermes-2-Pro, Hermes-3, and fine-tunes.

Reference: https://github.com/NousResearch/hermes-agent
"""

import json
import logging
import re
from typing import Optional

log = logging.getLogger("localclaw.hermes")

# Model name substrings that trigger Hermes XML tool-calling format.
# ONLY add models that were specifically fine-tuned on the Hermes tool-calling
# format and emit <tool_call> XML natively.  Modern models (Gemma 4, Llama 3.2,
# Qwen 2.5, etc.) support Ollama's native tool_calls API and should NOT be
# listed here — adding them breaks native tool calling by suppressing the tools
# API parameter.
HERMES_MODEL_PATTERNS = (
    "hermes",
    "nous",
    "functionary",
    "mistral-nemo",
    "nexusraven",
)

HERMES_SYSTEM_HEADER = """\
You are a function calling AI model. You are provided with function signatures \
within <tools> </tools> XML tags. You may call one or more functions to assist \
with the user query. If available tools are not relevant, just respond in natural \
conversational language. Don't make assumptions about what values to plug into \
functions. After calling & executing the functions, you will be provided with \
function results within <tool_response> </tool_response> XML tags.

Here are the available tools:
<tools>
{tools_json}
</tools>

For each function call, return a JSON object enclosed within <tool_call> </tool_call> XML tags:
<tool_call>
{{"name": "<function-name>", "arguments": {{<args-dict>}}}}
</tool_call>

You may emit multiple <tool_call> blocks in a single response to run tools in parallel.\
"""

HERMES_TOOL_USE_RULES = """\

## Tool-use discipline
- Use tools whenever they improve correctness, completeness, or grounding.
- MUST use a tool immediately when the task requires live information (files, commands, web).
- Do not stop early when another tool call would materially improve the result.
- Keep calling tools until: (1) the task is complete AND (2) you have verified the result.
- Before finalizing, confirm output satisfies the stated requirements.\
"""


def is_hermes_model(model_name: str) -> bool:
    """Return True if this model should use the Hermes XML tool-calling format."""
    low = model_name.lower()
    return any(p in low for p in HERMES_MODEL_PATTERNS)


def format_tools_for_hermes(tools: list[dict]) -> str:
    """
    Convert LocalClaw's OpenAI-style tool list to Hermes <tools> JSON format.

    Input: [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
    Output: JSON array with {name, description, parameters, required: null}
    """
    hermes_tools = []
    for tool in tools:
        func = tool.get("function", tool)  # handle both wrapped and raw formats
        hermes_tools.append({
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
            "required": None,
        })
    return json.dumps(hermes_tools, ensure_ascii=False, indent=2)


def build_hermes_system_prompt(base_system: str, tools: list[dict]) -> str:
    """
    Prepend the Hermes tool-calling header to an existing system prompt.
    The header defines tools in <tools> XML and instructs the model to
    emit <tool_call> blocks. The base system prompt follows after.
    """
    tools_json = format_tools_for_hermes(tools)
    header = HERMES_SYSTEM_HEADER.format(tools_json=tools_json)
    enforcement = HERMES_TOOL_USE_RULES
    return f"{header}{enforcement}\n\n---\n\n{base_system}"


def extract_tool_calls(text: str) -> list[dict]:
    """
    Parse all <tool_call> blocks from model output text.

    Returns a list of {"tool": name, "args": {...}} dicts, one per block.
    Handles both JSON (double-quote) and Python-dict-style (single-quote) output.
    """
    results = []
    for m in re.finditer(r"<tool_call>\s*([\s\S]*?)\s*</tool_call>", text, re.IGNORECASE):
        raw = m.group(1).strip()
        # Models sometimes emit Python-style single-quoted dicts — coerce to JSON
        raw_json = _coerce_to_json(raw)
        try:
            data = json.loads(raw_json)
            if not isinstance(data, dict):
                continue
            name = data.get("name", "")
            args = data.get("arguments", data.get("args", {}))
            if name:
                results.append({"tool": name, "args": args})
        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"[Hermes] Failed to parse tool_call block: {e}\nRaw: {raw[:200]}")
    return results


def format_tool_response(name: str, content, call_id: str = "") -> str:
    """
    Wrap a tool result in a Hermes <tool_response> block for injection
    into the conversation as a user message.
    """
    payload = {
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }
    return f"<tool_response>\n{json.dumps(payload, ensure_ascii=False)}\n</tool_response>"


def strip_tool_call_blocks(text: str) -> str:
    """Remove <tool_call> blocks from text, leaving any surrounding prose."""
    return re.sub(r"<tool_call>\s*[\s\S]*?\s*</tool_call>", "", text, flags=re.IGNORECASE).strip()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _coerce_to_json(s: str) -> str:
    """
    Best-effort coercion of Python-style dict strings to valid JSON.
    Handles single quotes → double quotes and None/True/False literals.
    """
    # Already valid JSON? Return as-is.
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    # Replace single-quoted strings with double-quoted, carefully.
    # Strategy: use re to swap unescaped single-quote delimiters.
    try:
        # Replace Python-style True/False/None
        coerced = s.replace("True", "true").replace("False", "false").replace("None", "null")
        # Swap single-quotes for double-quotes (only at string boundaries)
        coerced = re.sub(r"(?<![\\])'", '"', coerced)
        json.loads(coerced)
        return coerced
    except (json.JSONDecodeError, Exception):
        pass

    return s  # return original; caller handles the parse error
