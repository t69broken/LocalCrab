"""
SkillsManager — Manages built-in and ClaWHub-installed skills.

Skills follow the SKILL.md format from ClaWHub.
Install via `npx clawhub@latest install <slug>` or the HTTP API.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("localclaw.skills")

BUILTIN_DIR = Path(__file__).parent / "builtin"
INSTALLED_DIR = Path(os.environ.get("SKILLS_DIR", "/app/data/skills"))
CLAWHUB_API = "https://clawhub.ai"
CLAWHUB_CONVEX = "https://wry-manatee-359.convex.site"

# Popular/featured skills for browsing (fetched dynamically when possible)
POPULAR_SKILLS = [
    "python", "code-assistant", "web-scraper", "browse", "job-search",
    "resume", "research", "writing", "analysis", "git",
    "docker", "aws", "data-analysis", "api-design", "testing"
]


class SkillsManager:
    def __init__(self):
        self._skills: dict[str, dict] = {}

    async def initialize(self):
        INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
        self._load_builtin()
        self._load_installed()
        log.info(f"Skills loaded: {len(self._skills)} total")

    def _load_builtin(self):
        if BUILTIN_DIR.exists():
            for skill_dir in BUILTIN_DIR.iterdir():
                if skill_dir.is_dir():
                    self._load_skill_from_dir(skill_dir, builtin=True)

    def _load_installed(self):
        if INSTALLED_DIR.exists():
            for skill_dir in INSTALLED_DIR.iterdir():
                if skill_dir.is_dir():
                    self._load_skill_from_dir(skill_dir, builtin=False)

    def _load_skill_from_dir(self, path: Path, builtin: bool = False):
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            return

        content = skill_md.read_text(encoding="utf-8")
        meta = self._parse_frontmatter(content)
        slug = meta.get("name") or path.name

        self._skills[slug] = {
            "slug": slug,
            "name": meta.get("name", slug),
            "description": meta.get("description", ""),
            "version": meta.get("version", "1.0.0"),
            "content": content,
            "path": str(path),
            "builtin": builtin,
            "metadata": meta,
        }

    def _parse_frontmatter(self, content: str) -> dict:
        """Parse YAML frontmatter from a SKILL.md file."""
        if not content.startswith("---"):
            return {}
        try:
            end = content.index("---", 3)
            fm = content[3:end].strip()
            meta = {}
            for line in fm.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            return meta
        except Exception:
            return {}

    def get_skill(self, slug: str) -> Optional[dict]:
        return self._skills.get(slug)

    def list_skills(self) -> list[dict]:
        return [
            {k: v for k, v in s.items() if k != "content"}
            for s in self._skills.values()
        ]

    def count(self) -> int:
        return len(self._skills)

    def uninstall(self, slug: str) -> dict:
        skill = self._skills.get(slug)
        if not skill:
            return {"error": f"Skill '{slug}' not found"}
        if skill.get("builtin"):
            return {"error": "Cannot uninstall built-in skills"}
        import shutil
        shutil.rmtree(skill["path"], ignore_errors=True)
        del self._skills[slug]
        return {"uninstalled": slug}

    async def search_clawhub(self, query: str, limit: int = 10) -> list[dict]:
        """Search ClaWHub skills registry.
        
        Uses the Convex backend API to fetch skill metadata.
        Searches both by direct slug lookup and by checking popular skills.
        """
        results = []
        seen_slugs = set()
        query_lower = query.lower()
        
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # First, try direct slug lookup (in case user typed exact slug)
                try:
                    r = await client.get(
                        f"{CLAWHUB_CONVEX}/api/v1/skills/{query_lower}",
                    )
                    if r.status_code == 200:
                        data = r.json()
                        skill = data.get("skill", {})
                        slug = skill.get("slug")
                        if slug and slug not in seen_slugs:
                            seen_slugs.add(slug)
                            results.append({
                                "slug": slug,
                                "name": skill.get("displayName", slug),
                                "description": skill.get("summary", ""),
                                "downloads": skill.get("stats", {}).get("downloads", 0),
                                "stars": skill.get("stats", {}).get("stars", 0),
                                "version": data.get("latestVersion", {}).get("version", "1.0.0"),
                                "author": data.get("owner", {}).get("handle", "unknown"),
                            })
                except Exception:
                    pass
                
                # Then search through popular skills (fuzzy match)
                for slug in POPULAR_SKILLS:
                    if len(results) >= limit:
                        break
                    if slug in seen_slugs:
                        continue
                    
                    # Check if slug matches query
                    if query_lower in slug or slug in query_lower:
                        try:
                            r = await client.get(
                                f"{CLAWHUB_CONVEX}/api/v1/skills/{slug}",
                            )
                            if r.status_code == 200:
                                data = r.json()
                                skill = data.get("skill", {})
                                slug = skill.get("slug")
                                if slug and slug not in seen_slugs:
                                    seen_slugs.add(slug)
                                    results.append({
                                        "slug": slug,
                                        "name": skill.get("displayName", slug),
                                        "description": skill.get("summary", ""),
                                        "downloads": skill.get("stats", {}).get("downloads", 0),
                                        "stars": skill.get("stats", {}).get("stars", 0),
                                        "version": data.get("latestVersion", {}).get("version", "1.0.0"),
                                        "author": data.get("owner", {}).get("handle", "unknown"),
                                    })
                        except Exception:
                            pass
                
                # If still no results, try keyword-based discovery on popular skills
                if not results:
                    for slug in POPULAR_SKILLS[:10]:  # Check first 10 popular skills
                        if len(results) >= limit:
                            break
                        try:
                            r = await client.get(
                                f"{CLAWHUB_CONVEX}/api/v1/skills/{slug}",
                            )
                            if r.status_code == 200:
                                data = r.json()
                                skill = data.get("skill", {})
                                slug = skill.get("slug")
                                if slug in seen_slugs:
                                    continue
                                    
                                summary = skill.get("summary", "").lower()
                                name = skill.get("displayName", "").lower()
                                
                                # Check if query matches name or description
                                if query_lower in name or query_lower in summary:
                                    seen_slugs.add(slug)
                                    results.append({
                                        "slug": slug,
                                        "name": skill.get("displayName", slug),
                                        "description": skill.get("summary", ""),
                                        "downloads": skill.get("stats", {}).get("downloads", 0),
                                        "stars": skill.get("stats", {}).get("stars", 0),
                                        "version": data.get("latestVersion", {}).get("version", "1.0.0"),
                                        "author": data.get("owner", {}).get("handle", "unknown"),
                                    })
                        except Exception:
                            pass
                        
        except Exception as e:
            log.warning(f"ClaWHub search failed: {e}")
        
        return results[:limit]

    async def install_from_clawhub(self, slug: str) -> dict:
        """
        Install a skill from ClaWHub using the clawhub CLI.
        Falls back to direct HTTP download if CLI unavailable.
        """
        target_dir = INSTALLED_DIR / slug

        # Try CLI first
        cli_result = await self._install_via_cli(slug, target_dir)
        if cli_result.get("status") == "ok":
            self._load_skill_from_dir(target_dir)
            return cli_result

        # Fallback: fetch SKILL.md directly from ClaWHub
        fallback = await self._install_via_http(slug, target_dir)
        if fallback.get("status") == "ok":
            self._load_skill_from_dir(target_dir)
        return fallback

    async def _install_via_cli(self, slug: str, target_dir: Path) -> dict:
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["npx", "clawhub@latest", "install", slug, "--output-dir", str(target_dir)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            )
            if result.returncode == 0:
                return {"status": "ok", "slug": slug, "method": "cli", "output": result.stdout}
            return {"status": "error", "error": result.stderr}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _install_via_http(self, slug: str, target_dir: Path) -> dict:
        """Direct HTTP download of skill from ClaWHub."""
        import zipfile
        import io
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # ClawHub uses Convex backend for downloads
                # Format: https://wry-manatee-359.convex.site/api/v1/download?slug=<slug>
                download_url = "https://wry-manatee-359.convex.site/api/v1/download"
                
                try:
                    r = await client.get(download_url, params={"slug": slug})
                    if r.status_code == 200:
                        # Response is a zip file
                        target_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Extract zip in memory
                        zip_data = io.BytesIO(r.content)
                        with zipfile.ZipFile(zip_data, 'r') as zf:
                            zf.extractall(target_dir)
                        
                        self._load_skill_from_dir(target_dir)
                        return {"status": "ok", "slug": slug, "method": "http-zip"}
                except Exception as e:
                    log.warning(f"ClawHub zip download failed: {e}")
                
                # Fallback: try raw SKILL.md endpoints
                urls = [
                    f"{CLAWHUB_API}/skills/{slug}/raw",
                    f"{CLAWHUB_API}/api/skills/{slug}",
                ]
                for url in urls:
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            content = r.text
                            target_dir.mkdir(parents=True, exist_ok=True)
                            (target_dir / "SKILL.md").write_text(content)
                            return {"status": "ok", "slug": slug, "method": "http"}
                    except Exception:
                        continue

            return {"status": "error", "error": f"Could not fetch skill '{slug}' from ClaWHub"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
