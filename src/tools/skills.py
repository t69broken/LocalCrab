"""
Skills Tools — Allow agent to search and install skills from ClaWHub.

These tools enable the agent to extend its own capabilities dynamically.
"""

import logging
from typing import Optional

from .registry import ToolDefinition,ToolResult, tool

log = logging.getLogger("localclaw.skills")

# These will be set by agent_manager during initialization
_skills_manager = None

def set_skills_manager(manager):
    """Set the skills manager reference."""
    global _skills_manager
    _skills_manager = manager


@tool(
    name="search_skills",
    description="Search for skills on ClaWHub that can extend your capabilities. Use this when you need a capability you don't have. Returns skill slugs, names, and descriptions.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query describing what capability you need (e.g., 'python', 'web scraping', 'data analysis')"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 5
            }
        },
        "required": ["query"]
    },
    category="skills"
)
async def search_skills_tool(query: str, limit: int = 5) -> ToolResult:
    """Search for skills on ClaWHub."""
    if not _skills_manager:
        return ToolResult(
            success=False,
            output="",
            error="Skills manager not initialized"
        )
    
    try:
        results = await _skills_manager.search_clawhub(query, limit)
        if not results:
            return ToolResult(
                success=True,
                output=f"No skills found for query: {query}"
            )
        
        lines = [f"Found {len(results)} skills for '{query}':\n"]
        for skill in results:
            lines.append(f"- {skill.get('slug', 'unknown')}: {skill.get('name', skill.get('slug'))}")
            if skill.get('description'):
                lines.append(f"  {skill['description'][:200]}")
            lines.append(f"  Use: install_skill(slug=\"{skill.get('slug')}\")")
            lines.append("")
        
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"skills": results}
        )
    except Exception as e:
        log.error(f"Skill search failed: {e}")
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to search skills: {e}"
        )


@tool(
    name="install_skill",
    description="Install a skill from ClaWHub by its slug. After installation, the skill will be available in your next response. Use search_skills to find skill slugs.",
    parameters={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "The skill slug to install (e.g., 'python-expert', 'web-scraper')"
            }
        },
        "required": ["slug"]
    },
    category="skills"
)
async def install_skill_tool(slug: str) -> ToolResult:
    """Install a skill from ClaWHub."""
    if not _skills_manager:
        return ToolResult(
            success=False,
            output="",
            error="Skills manager not initialized"
        )
    
    try:
        result = await _skills_manager.install_from_clawhub(slug)
        
        if result.get("status") == "ok":
            skill_info = _skills_manager.get_skill(slug)
            skill_name = skill_info.get("name", slug) if skill_info else slug
            
            return ToolResult(
                success=True,
                output=f"Successfully installed skill: {skill_name} ({slug})\nYou can now use this skill's capabilities. The skill instructions will be loaded automatically.",
                data={"slug": slug, "installed": True}
            )
        else:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to install skill: {result.get('error', 'Unknown error')}"
            )
    except Exception as e:
        log.error(f"Skill installation failed: {e}")
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to install skill: {e}"
        )


@tool(
    name="list_installed_skills",
    description="List all currently installed skills and their descriptions.",
    parameters={
        "type": "object",
        "properties": {}
    },
    category="skills"
)
async def list_installed_skills_tool() -> ToolResult:
    """List all installed skills."""
    if not _skills_manager:
        return ToolResult(
            success=False,
            output="",
            error="Skills manager not initialized"
        )
    
    try:
        skills = _skills_manager.list_skills()
        if not skills:
            return ToolResult(
                success=True,
                output="No skills installed. Use search_skills to find and install new capabilities."
            )
        
        lines = ["Installed skills:\n"]
        for skill in skills:
            builtin = " (built-in)" if skill.get("builtin") else ""
            lines.append(f"- {skill.get('name', skill.get('slug'))}{builtin}")
            if skill.get('description'):
                lines.append(f"  {skill['description'][:150]}")
            lines.append("")
        
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"skills": skills}
        )
    except Exception as e:
        log.error(f"Failed to list skills: {e}")
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to list skills: {e}"
        )