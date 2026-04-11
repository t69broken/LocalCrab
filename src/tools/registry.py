"""
Tool Registry — Manages tool definitions and execution.

Each tool is a function decorated with @tool that can be called by the agent.
Tools return structured results that are formatted back to the agent.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("localclaw.tools")

@dataclass
class ToolResult:
    """Result from a tool execution."""
    success: bool
    output: str
    error: Optional[str] = None
    data: Optional[dict] = None
    needs_confirmation: bool = False
    confirm_message: Optional[str] = None

@dataclass
class ToolDefinition:
    """A tool's metadata and implementation."""
    name: str
    description: str
    parameters: dict  # JSON schema
    implementation: Callable
    requires_confirmation: bool = False
    dangerous: bool = False
    category: str = "general"

class ToolRegistry:
    """Registry of all available tools."""
    
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._enabled_categories: set[str] = {
            "file", "terminal", "web", "memory", "system", "skills"
        }
    
    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool."""
        self._tools[tool_def.name] = tool_def
        log.debug(f"Registered tool: {tool_def.name}")
    
    def get(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def list_tools(self, category: Optional[str] = None) -> list[dict]:
        """List all available tools with their schemas."""
        tools = []
        for name, tool in self._tools.items():
            if category and tool.category != category:
                continue
            if tool.category not in self._enabled_categories:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            })
        return tools
    
    def get_system_prompt(self) -> str:
        """Generate the system prompt section describing available tools."""
        if not self._tools:
            return ""
        
        lines = ["You have access to the following tools. Use them to accomplish tasks:"]
        
        # Group by category
        by_category: dict[str, list[ToolDefinition]] = {}
        for tool in self._tools.values():
            if tool.category not in self._enabled_categories:
                continue
            by_category.setdefault(tool.category, []).append(tool)
        
        for category, tools in sorted(by_category.items()):
            lines.append(f"\n## {category.title()} Tools")
            for tool in tools:
                params = tool.parameters.get("properties", {})
                required = tool.parameters.get("required", [])
                param_parts = []
                for pname, pinfo in params.items():
                    ptype = pinfo.get("type", "any")
                    req_mark = "*" if pname in required else "?"
                    param_parts.append(f"{pname}{req_mark}:{ptype}")
                param_str = f"({', '.join(param_parts)})" if param_parts else "()"
                lines.append(f"- {tool.name}{param_str}: {tool.description}")
        
        lines.append("\nTo use a tool, output a JSON block with this format:")
        lines.append('```json')
        lines.append('{"tool": "tool_name", "args": {"arg1": "value1"}}')
        lines.append('```')
        
        return "\n".join(lines)
    
    async def execute(self, name: str, args: dict) -> ToolResult:
        """Execute a tool and return the result."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown tool: {name}"
            )
        
        try:
            log.info(f"Executing tool: {name}({args})")
            result = await tool.implementation(**args)
            if isinstance(result, ToolResult):
                return result
            elif isinstance(result, dict):
                return ToolResult(**result)
            elif isinstance(result, str):
                return ToolResult(success=True, output=result)
            else:
                return ToolResult(success=True, output=str(result))
        except Exception as e:
            log.error(f"Tool {name} failed: {e}")
            return ToolResult(
                success=False,
                output="",
                error=f"Tool execution failed: {e}"
            )


# Decorator for defining tools
def tool(
    name: str,
    description: str,
    parameters: dict,
    requires_confirmation: bool = False,
    dangerous: bool = False,
    category: str = "general",
):
    """Decorator to register a function as a tool."""
    def decorator(func: Callable) -> Callable:
        tool_def = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            implementation=func,
            requires_confirmation=requires_confirmation,
            dangerous=dangerous,
            category=category,
        )
        return tool_def
    return decorator