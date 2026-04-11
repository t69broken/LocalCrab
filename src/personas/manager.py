"""
PersonaManager — Manages agent personas via SOUL.md files.

Personas come from:
  1. Built-in SOUL.md files (shipped with LocalClaw)
  2. ClawHub Souls registry (fetched on demand via Convex API)
"""

import asyncio
import logging
import os
import zipfile
import io
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("localclaw.personas")

BUILTIN_DIR = Path(__file__).parent / "builtin"
INSTALLED_DIR = Path(os.environ.get("PERSONAS_DIR", "/app/data/personas"))
CLAWHUB_CONVEX = "https://wry-manatee-359.convex.site"


class PersonaManager:
    def __init__(self):
        self._personas: dict[str, dict] = {}

    async def initialize(self):
        INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
        self._load_builtin()
        self._load_installed()
        log.info(f"Personas loaded: {len(self._personas)} total")

    def _load_builtin(self):
        if BUILTIN_DIR.exists():
            for p in BUILTIN_DIR.glob("*/SOUL.md"):
                self._load_from_file(p, builtin=True)

    def _load_installed(self):
        if INSTALLED_DIR.exists():
            for p in INSTALLED_DIR.glob("*/SOUL.md"):
                self._load_from_file(p, builtin=False)

    def _load_from_file(self, path: Path, builtin: bool = False):
        content = path.read_text(encoding="utf-8")
        meta = self._parse_frontmatter(content)
        slug = meta.get("name") or path.parent.name

        self._personas[slug] = {
            "slug": slug,
            "name": meta.get("name", slug),
            "description": meta.get("description", ""),
            "author": meta.get("author", ""),
            "soul_md": content,
            "path": str(path),
            "builtin": builtin,
        }

    def _parse_frontmatter(self, content: str) -> dict:
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

    def get_persona(self, slug: str) -> Optional[dict]:
        return self._personas.get(slug)

    def list_personas(self) -> list[dict]:
        return [
            {k: v for k, v in p.items() if k != "soul_md"}
            for p in self._personas.values()
        ]

    async def search_souls(self, query: str, limit: int = 10) -> list[dict]:
        """Search ClawHub Souls registry for personas."""
        results = []
        query_lower = query.lower()
        
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # First, try direct slug lookup
                try:
                    r = await client.get(
                        f"{CLAWHUB_CONVEX}/api/v1/souls/{query_lower}",
                    )
                    if r.status_code == 200:
                        data = r.json()
                        soul = data.get("soul", {})
                        if soul:
                            results.append({
                                "slug": soul.get("slug"),
                                "name": soul.get("displayName", soul.get("slug")),
                                "description": soul.get("summary", ""),
                                "downloads": soul.get("stats", {}).get("downloads", 0),
                                "stars": soul.get("stats", {}).get("stars", 0),
                                "author": data.get("owner", {}).get("handle", "unknown"),
                            })
                except Exception:
                    pass
                
                # Then get all souls and filter
                if len(results) < limit:
                    try:
                        r = await client.get(
                            f"{CLAWHUB_CONVEX}/api/v1/souls",
                            params={"limit": 50},
                        )
                        if r.status_code == 200:
                            data = r.json()
                            items = data.get("items", [])
                            for item in items:
                                if len(results) >= limit:
                                    break
                                
                                name = (item.get("displayName") or item.get("slug") or "").lower()
                                summary = (item.get("summary") or "").lower()
                                
                                if query_lower in name or query_lower in summary:
                                    results.append({
                                        "slug": item.get("slug"),
                                        "name": item.get("displayName", item.get("slug")),
                                        "description": item.get("summary", ""),
                                        "downloads": item.get("stats", {}).get("downloads", 0),
                                        "stars": item.get("stats", {}).get("stars", 0),
                                        "author": item.get("owner", {}).get("handle", "unknown") if isinstance(item.get("owner"), dict) else "unknown",
                                    })
                    except Exception as e:
                        log.warning(f"Souls list fetch failed: {e}")
                        
        except Exception as e:
            log.warning(f"Souls search failed: {e}")
        
        return results[:limit]

    async def install_soul(self, slug: str) -> dict:
        """Download a SOUL.md from ClawHub Souls registry."""
        target_dir = INSTALLED_DIR / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # ClawHub uses Convex backend for downloads
                # Format: https://wry-manatee-359.convex.site/api/v1/download?slug=<slug>
                download_url = f"{CLAWHUB_CONVEX}/api/v1/download"
                
                try:
                    r = await client.get(download_url, params={"slug": slug})
                    if r.status_code == 200:
                        # Response is a zip file
                        zip_data = io.BytesIO(r.content)
                        with zipfile.ZipFile(zip_data, 'r') as zf:
                            zf.extractall(target_dir)
                        
                        # Load the persona
                        soul_path = target_dir / "SOUL.md"
                        if soul_path.exists():
                            self._load_from_file(soul_path, builtin=False)
                            return {"status": "ok", "slug": slug}
                        else:
                            # Try alternate naming
                            for f in target_dir.glob("*.md"):
                                if "soul" in f.name.lower() or f.name == "SOUL.md":
                                    self._load_from_file(f, builtin=False)
                                    return {"status": "ok", "slug": slug}
                except Exception as e:
                    log.warning(f"Download failed: {e}")
                
                # Fallback: try raw SOUL.md endpoints
                urls = [
                    f"{CLAWHUB_CONVEX}/souls/{slug}/raw",
                    f"https://clawhub.ai/souls/{slug}/raw",
                ]
                for url in urls:
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            content = r.text
                            soul_path = target_dir / "SOUL.md"
                            soul_path.write_text(content)
                            self._load_from_file(soul_path, builtin=False)
                            return {"status": "ok", "slug": slug}
                    except Exception:
                        continue

            return {"status": "error", "error": f"Soul '{slug}' not found on ClawHub"}
        except Exception as e:
            return {"status": "error", "error": str(e)}