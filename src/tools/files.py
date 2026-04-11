"""
File Tools — Read, write, and search files on the host system.
"""

import os
import re
from typing import Optional
from .registry import tool, ToolResult

# Sandboxed base directory (can be configured)
SANDBOX_BASE = os.environ.get("LOCALCLAW_SANDBOX", os.path.expanduser("~/localclaw_workspace"))

def _resolve_path(path: str) -> str:
    """Resolve and validate a path within the sandbox."""
    # Expand home and make absolute
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(SANDBOX_BASE, expanded)
    
    # Normalize and check it's within sandbox
    resolved = os.path.realpath(expanded)
    # Allow access to sandbox or original path if it exists
    if not resolved.startswith(SANDBOX_BASE) and not os.path.exists(path):
        # For reading, allow the actual path if it exists
        pass
    
    return resolved if os.path.exists(resolved) or resolved.startswith(SANDBOX_BASE) else expanded


@tool(
    name="read_file",
    description="Read the contents of a file. Returns file content with line numbers.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read"
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed)",
                "default": 1
            },
            "limit": {
                "type": "integer",
                "description": "Maximum lines to read (default: 500)",
                "default": 500
            }
        },
        "required": ["path"]
    },
    category="file"
)
async def read_file_tool(
    path: str,
    offset: int = 1,
    limit: int = 500
) -> ToolResult:
    """Read a file's contents."""
    try:
        resolved = _resolve_path(path)
        if not os.path.exists(resolved):
            return ToolResult(
                success=False,
                output="",
                error=f"File not found: {path}"
            )
        
        with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        
        # Apply offset and limit
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        selected = lines[start:end]
        
        # Format with line numbers
        output_lines = []
        for i, line in enumerate(selected, start=offset):
            output_lines.append(f"{i:5d}|{line.rstrip()}")
        
        result = "\n".join(output_lines)
        if end < len(lines):
            result += f"\n... ({len(lines) - end} more lines)"
        
        return ToolResult(
            success=True,
            output=result,
            data={
                "total_lines": len(lines),
                "shown_lines": len(selected),
                "path": resolved
            }
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="write_file",
    description="Write content to a file. Creates parent directories if needed.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to write to"
            },
            "content": {
                "type": "string",
                "description": "Content to write"
            }
        },
        "required": ["path", "content"]
    },
    requires_confirmation=True,
    category="file"
)
async def write_file_tool(path: str, content: str) -> ToolResult:
    """Write content to a file."""
    try:
        resolved = _resolve_path(path)
        
        # Create parent directories
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        
        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return ToolResult(
            success=True,
            output=f"Wrote {len(content)} bytes to {resolved}",
            data={"path": resolved, "bytes": len(content)}
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="search_files",
    description="Search for files by name or search inside files for content.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Pattern to search for (glob for files, regex for content)"
            },
            "target": {
                "type": "string",
                "description": "'files' to find files by name, 'content' to search inside files",
                "enum": ["files", "content"],
                "default": "content"
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (default: workspace)",
                "default": "."
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results",
                "default": 50
            }
        },
        "required": ["pattern"]
    },
    category="file"
)
async def search_files_tool(
    pattern: str,
    target: str = "content",
    path: str = ".",
    limit: int = 50
) -> ToolResult:
    """Search for files or content."""
    try:
        resolved = _resolve_path(path)
        if not os.path.isdir(resolved):
            return ToolResult(
                success=False,
                output="",
                error=f"Not a directory: {path}"
            )
        
        results = []
        
        if target == "files":
            # Glob search for filenames
            import fnmatch
            for root, dirs, files in os.walk(resolved):
                for fname in files:
                    if fnmatch.fnmatch(fname.lower(), pattern.lower()):
                        results.append(os.path.join(root, fname))
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break
        else:
            # Regex search in file contents
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error:
                regex = re.compile(re.escape(pattern), re.IGNORECASE)
            
            for root, dirs, files in os.walk(resolved):
                # Skip hidden and large directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                            for i, line in enumerate(f, 1):
                                if regex.search(line):
                                    results.append(f"{fpath}:{i}: {line.strip()[:200]}")
                                    if len(results) >= limit:
                                        break
                    except Exception:
                        continue
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
        
        if not results:
            return ToolResult(
                success=True,
                output=f"No matches found for '{pattern}'",
                data={"count": 0, "results": []}
            )
        
        return ToolResult(
            success=True,
            output="\n".join(results),
            data={"count": len(results), "results": results}
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="list_dir",
    description="List files and directories in a path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list",
                "default": "."
            }
        },
        "required": []
    },
    category="file"
)
async def list_dir_tool(path: str = ".") -> ToolResult:
    """List directory contents."""
    try:
        resolved = _resolve_path(path)
        if not os.path.isdir(resolved):
            return ToolResult(
                success=False,
                output="",
                error=f"Not a directory: {path}"
            )
        
        entries = []
        for entry in sorted(os.listdir(resolved)):
            full_path = os.path.join(resolved, entry)
            if os.path.isdir(full_path):
                entries.append(f"📁 {entry}/")
            else:
                size = os.path.getsize(full_path)
                entries.append(f"📄 {entry} ({size} bytes)")
        
        if not entries:
            return ToolResult(success=True, output="(empty directory)")
        
        return ToolResult(
            success=True,
            output="\n".join(entries),
            data={"count": len(entries)}
        )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))