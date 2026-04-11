"""
Memory Tool — Access to persistent long-term memory.
"""

from typing import Optional
from .registry import tool, ToolResult

# Will be injected by AgentManager
_memory_server = None

def set_memory_server(server):
    """Set the memory server instance."""
    global _memory_server
    _memory_server = server


@tool(
    name="memory_search",
    description="Search long-term memory for relevant information.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results (default: 5)",
                "default": 5
            }
        },
        "required": ["query"]
    },
    category="memory"
)
async def memory_search_tool(query: str, limit: int = 5) -> ToolResult:
    """Search memory for relevant entries."""
    if not _memory_server:
        return ToolResult(
            success=False,
            output="",
            error="Memory server not available"
        )
    
    try:
        results = await _memory_server.search(query, limit=limit)
        
        if not results:
            return ToolResult(
                success=True,
                output=f"No memories found for '{query}'",
                data={"query": query, "results": []}
            )
        
        lines = []
        for mem in results:
            mem_type = mem.get("type", "fact")
            content = mem.get("content", "")
            lines.append(f"- [{mem_type}] {content}")
        
        return ToolResult(
            success=True,
            output="Relevant memories:\n" + "\n".join(lines),
            data={"query": query, "results": results}
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="memory_save",
    description="Save information to long-term memory for future reference.",
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Information to remember"
            },
            "memory_type": {
                "type": "string",
                "description": "Type of memory (fact, preference, note, etc.)",
                "default": "fact"
            }
        },
        "required": ["content"]
    },
    category="memory"
)
async def memory_save_tool(content: str, memory_type: str = "fact") -> ToolResult:
    """Save something to memory."""
    if not _memory_server:
        return ToolResult(
            success=False,
            output="",
            error="Memory server not available"
        )
    
    try:
        result = await _memory_server.save_memory(
            content=content,
            memory_type=memory_type,
            source="tool"
        )
        
        return ToolResult(
            success=True,
            output=f"Saved to memory: {content[:100]}..." if len(content) > 100 else f"Saved to memory: {content}",
            data=result
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))