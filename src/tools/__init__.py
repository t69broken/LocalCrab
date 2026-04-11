"""
LocalClaw Tools — Real tool execution capabilities.

This module provides the agent with actual system access:
- Terminal: Execute shell commands
- Files: Read/write/search files
- Web: Search, browse, extract content
- Memory: Persistent storage
- System: OS info, environment details
- Skills: Search and install skills from ClaWHub
"""

from .registry import ToolRegistry, tool

# Import the decorated tool functions
from .terminal import terminal_tool, background_tool
from .files import read_file_tool, write_file_tool, search_files_tool, list_dir_tool
from .web import web_search_tool, web_fetch_tool
from .memory import memory_search_tool, memory_save_tool, set_memory_server
from .system import system_info_tool, env_var_tool, list_processes_tool
from .skills import search_skills_tool, install_skill_tool, list_installed_skills_tool, set_skills_manager

__all__ = [
    "ToolRegistry",
    "tool",
    "terminal_tool",
    "background_tool",
    "read_file_tool",
    "write_file_tool",
    "search_files_tool",
    "list_dir_tool",
    "web_search_tool",
    "web_fetch_tool",
    "memory_search_tool",
    "memory_save_tool",
    "system_info_tool",
    "env_var_tool",
    "list_processes_tool",
    "search_skills_tool",
    "install_skill_tool",
    "list_installed_skills_tool",
    "set_skills_manager",
]