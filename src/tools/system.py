"""
System Tool — Get information about the host system and environment.
"""

import asyncio
import os
import platform
import subprocess
from typing import Optional
from .registry import tool, ToolResult

# Import SSH helpers from terminal module so system tools also run on the host
try:
    from .terminal import _get_host_ip, _ssh_available, _build_ssh_cmd, _KEY, _USER, _PORT
    _HAS_SSH_HELPERS = True
except ImportError:
    _HAS_SSH_HELPERS = False


async def _run_on_host(command: str, timeout: int = 15) -> tuple[bool, str, str]:
    """Run a command on the host via SSH (if available) or locally. Returns (ok, stdout, stderr)."""
    if _HAS_SSH_HELPERS and _ssh_available():
        full_cmd = _build_ssh_cmd(command, _get_host_ip(), None)
    else:
        full_cmd = command
    try:
        proc = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        ok = proc.returncode == 0
        return ok, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
    except Exception as e:
        return False, "", str(e)


@tool(
    name="system_info",
    description="Get information about the host system (OS, CPU, memory, GPU).",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    category="system"
)
async def system_info_tool() -> ToolResult:
    """Get system information from the host."""
    cmd = (
        "echo \"OS: $(uname -sr)\"; "
        "echo \"Hostname: $(hostname)\"; "
        "echo \"CPU: $(nproc) cores\"; "
        "echo \"Memory: $(free -h | awk '/^Mem:/{print $2\" total, \"$7\" available\"}')\"; "
        "echo \"Disk: $(df -h ~ | awk 'NR==2{print $4\" free of \"$2}')\"; "
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | "
        "  awk '{print \"GPU: \"$0}' || true"
    )
    ok, stdout, stderr = await _run_on_host(cmd)
    if not ok and not stdout:
        return ToolResult(success=False, output="", error=stderr or "Failed to get system info")
    return ToolResult(success=True, output=stdout.strip())


@tool(
    name="env_var",
    description="Get an environment variable value.",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Environment variable name"
            }
        },
        "required": ["name"]
    },
    category="system"
)
async def env_var_tool(name: str) -> ToolResult:
    """Get an environment variable."""
    value = os.environ.get(name)
    if value is None:
        return ToolResult(
            success=False,
            output="",
            error=f"Environment variable '{name}' not found"
        )
    
    # Hide sensitive values
    sensitive = ["KEY", "SECRET", "PASSWORD", "TOKEN", "API", "CRED"]
    is_sensitive = any(s in name.upper() for s in sensitive)
    
    if is_sensitive:
        return ToolResult(
            success=True,
            output=f"{name}: [REDACTED - contains sensitive keyword]",
            data={"name": name, "hidden": True}
        )
    
    return ToolResult(
        success=True,
        output=f"{name}: {value}",
        data={"name": name, "value": value}
    )


@tool(
    name="list_processes",
    description="List running processes (top CPU or memory consumers).",
    parameters={
        "type": "object",
        "properties": {
            "sort_by": {
                "type": "string",
                "description": "Sort by 'cpu' or 'memory'",
                "enum": ["cpu", "memory"],
                "default": "cpu"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum processes to show",
                "default": 10
            }
        }
    },
    category="system"
)
async def list_processes_tool(sort_by: str = "cpu", limit: int = 10) -> ToolResult:
    """List running processes on the host."""
    sort_flag = "--sort=-pcpu" if sort_by == "cpu" else "--sort=-pmem"
    cmd = f"ps aux {sort_flag} | head -{limit + 1}"
    ok, stdout, stderr = await _run_on_host(cmd)
    if not ok:
        return ToolResult(success=False, output=stdout, error=stderr or "Failed to list processes")
    return ToolResult(
        success=True,
        output=stdout.strip() or "(no output)",
        data={"sort": sort_by},
    )