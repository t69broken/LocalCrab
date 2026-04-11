"""
Web Tools — Search and fetch web content.
"""

import json
import re
from typing import Optional
from .registry import tool, ToolResult

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@tool(
    name="web_search",
    description="Search the web for information. Returns top results with titles and URLs.",
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
    category="web"
)
async def web_search_tool(query: str, limit: int = 5) -> ToolResult:
    """Search the web using DuckDuckGo Instant Answer API."""
    if not HAS_HTTPX:
        return ToolResult(
            success=False,
            output="",
            error="httpx not installed. Run: pip install httpx"
        )
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Use DuckDuckGo HTML search (no API key needed)
            r = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            )
            
            if r.status_code != 200:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Search failed: HTTP {r.status_code}"
                )
            
            # Parse results from HTML
            results = []
            # Simple regex extraction (works for DuckDuckGo HTML)
            pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, r.text)
            
            for url, title in matches[:limit]:
                # Clean up URL (DuckDuckGo uses redirects)
                if url.startswith("//"):
                    url = "https:" + url
                # Extract actual URL from redirect
                if "uddg=" in url:
                    actual_url = url.split("uddg=")[-1].split("&")[0]
                    try:
                        url = __import__("urllib.parse").unquote(actual_url)
                    except Exception:
                        pass
                
                results.append(f"- {title.strip()}\n  {url}")
            
            if not results:
                return ToolResult(
                    success=True,
                    output=f"No results found for '{query}'",
                    data={"query": query, "results": []}
                )
            
            return ToolResult(
                success=True,
                output=f"Search results for '{query}':\n\n" + "\n\n".join(results),
                data={"query": query, "count": len(results)}
            )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))


@tool(
    name="web_fetch",
    description="Fetch and extract text content from a URL.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch"
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters to return (default: 5000)",
                "default": 5000
            }
        },
        "required": ["url"]
    },
    category="web"
)
async def web_fetch_tool(url: str, max_length: int = 5000) -> ToolResult:
    """Fetch and extract content from a URL."""
    if not HAS_HTTPX:
        return ToolResult(
            success=False,
            output="",
            error="httpx not installed"
        )
    
    try:
        # Validate URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
            )
            
            if r.status_code != 200:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"HTTP {r.status_code}"
                )
            
            # Simple text extraction from HTML
            text = r.text
            
            # Remove script/style
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.IGNORECASE)
            
            # Remove tags
            text = re.sub(r'<[^>]+>', ' ', text)
            
            # Clean whitespace
            text = re.sub(r'\s+', ' ', text)
            text = text.strip()
            
            # Truncate
            if len(text) > max_length:
                text = text[:max_length] + "... (truncated)"
            
            return ToolResult(
                success=True,
                output=f"Content from {url}:\n\n{text}",
                data={"url": url, "length": len(text)}
            )
    except Exception as e:
        return ToolResult(success=False, output="", error=str(e))