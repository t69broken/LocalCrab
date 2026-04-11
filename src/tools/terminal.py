"""
Terminal Tool — Execute shell commands directly on the local machine.
"""

import asyncio
import os
from typing import Optional
from .registry import tool, ToolResult


@tool(
    name="terminal",
    description="Execute a shell command on this machine. Returns stdout, stderr, and exit code.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute"
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 60)",
                "default": 60
            },
            "workdir": {
                "type": "string",
                "description": "Working directory (default: current)"
            }
        },
        "required": ["command"]
    },
    requires_confirmation=True,
    dangerous=True,
    category="terminal"
)
async def terminal_tool(
    command: str,
    timeout: int = 60,
    workdir: Optional[str] = None,
) -> ToolResult:
    """Execute a shell command locally."""
    cwd = workdir or os.getcwd()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, output="", error=f"Command timed out after {timeout}s")

        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        error  = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                output=output,
                error=f"Exit code {proc.returncode}: {error}" if error else f"Exit code {proc.returncode}",
            )

        return ToolResult(
            success=True,
            output=output or "(no output)",
            data={"exit_code": proc.returncode, "stderr": error},
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="background",
    description=(
        "Start a long-running background process and return immediately. "
        "The process keeps running after this call returns. Use for servers, watchers, etc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to run in the background"
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command"
            }
        },
        "required": ["command"]
    },
    requires_confirmation=True,
    dangerous=True,
    category="terminal"
)
async def background_tool(command: str, workdir: Optional[str] = None) -> ToolResult:
    """Start a background process locally."""
    cwd = workdir or os.getcwd()
    bg_command = f"nohup bash -c {repr(command)} </dev/null >/tmp/bg_$(date +%s).log 2>&1 &"
    try:
        proc = await asyncio.create_subprocess_shell(
            bg_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return ToolResult(success=False, output="", error="Background launch timed out")

        output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        error  = stderr.decode("utf-8", errors="replace").strip() if stderr else ""

        return ToolResult(
            success=True,
            output=f"Background process started.\n{output}".strip(),
            data={"stderr": error},
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))
