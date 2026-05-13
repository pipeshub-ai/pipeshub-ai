import asyncio
import json
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.utils.logger import create_logger

MAX_RETRIES = 2
INITIAL_BACKOFF_SECONDS = 2

logger = create_logger(__name__)

class WebSearchArgs(BaseModel):
    """Arguments for web search tool."""
    query: str = Field(
        ...,
        description="Search query to find current information on the web"
    )


def _extract_ddg_url(href: str) -> str:
    """Extract actual URL from DuckDuckGo redirect wrapper."""
    from urllib.parse import parse_qs, unquote, urlparse

    if not href:
        return ""

    # DuckDuckGo wraps URLs: //duckduckgo.com/l/?uddg=ENCODED_URL&rut=...
    if "uddg=" in href:
        full = href if href.startswith("http") else f"https:{href}"
        params = parse_qs(urlparse(full).query)
        uddg = params.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)

    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return f"https:{href}"

    return href


def _search_with_duckduckgo_sync(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using DuckDuckGo HTML endpoint with robust anti-bot evasion (sync I/O)."""
    from urllib.parse import urlencode

    from bs4 import BeautifulSoup

    from app.utils.url_fetcher import FetchError, fetch_url

    search_url = f"https://html.duckduckgo.com/html/?{urlencode({'q': query, 'kl': 'wt-wt'})}"

    try:
        response = fetch_url(search_url, timeout=15)
    except FetchError as e:
        logger.error(f"DuckDuckGo fetch failed: {e}")
        return []
   
    status_code = response.status_code
    if status_code != 200:
        logger.error(f"DuckDuckGo fetch failed: {status_code}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for item in soup.select(".web-result"):
        title_tag = item.select_one("a.result__a")
        snippet_tag = item.select_one(".result__snippet")

        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
        href = title_tag.get("href", "")
        link = _extract_ddg_url(href)

        if title and link:
            results.append({"title": title, "link": link, "snippet": snippet})

        if len(results) >= 10:
            break

    return results


async def _search_with_duckduckgo(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using DuckDuckGo (runs sync fetch/parser in a thread pool)."""
    return await asyncio.to_thread(_search_with_duckduckgo_sync, query, config)


async def _search_with_serper(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using Serper API."""
    api_key = config.get("apiKey")
    if not api_key:
        raise ValueError("Serper API key is required")

    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }
    payload = {"q": query}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()

    # Format results similar to DuckDuckGo output
    results = []
    for item in data.get("organic", [])[:10]:
        results.append({
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", "")
        })
    return results


async def _search_with_tavily(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using Tavily API."""
    api_key = config.get("apiKey")
    if not api_key:
        raise ValueError("Tavily API key is required")

    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": 10,
        "search_depth": "advanced"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()

    # Format results
    results = []
    for item in data.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "link": item.get("url", ""),
            "snippet": item.get("content", "")
        })
    return results


async def _search_with_exa(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using Exa API."""
    api_key = config.get("apiKey")
    if not api_key:
        raise ValueError("Exa API key is required")

    url = "https://api.exa.ai/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "numResults": 10,
        "contents": {
            "text": True,
            "highlights": True,
            "summary": True,
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=30.0)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("results", [])[:10]:
        text = (item.get("text") or "").strip()
        highlights = item.get("highlights") or []
        hl_snippet = " ".join(str(h) for h in highlights if h).strip()
        summary = (item.get("summary") or "").strip()
        snippet = hl_snippet or summary or text

        results.append({
            "title": item.get("title", ""),
            "link": item.get("url", ""),
            "snippet": snippet,
        })
    return results


def create_web_search_tool(
    config: dict[str, Any] | None = None,
) -> BaseTool:
    """
    Factory function to create web search tool.

    Args:
        config: Optional configuration dict with structure:
            {
                "provider": "duckduckgo" | "serper" | "tavily" | "exa",
                "configuration": {
                    # Provider-specific config like apiKey, cx, endpoint, engine
                }
            }
    """
    # Default to DuckDuckGo if no config provided
    if not config:
        config = {"provider": "duckduckgo", "configuration": {}}

    provider = config.get("provider", "duckduckgo")
    provider_config = config.get("configuration", {})

    # Map provider IDs to their search functions
    provider_map = {
        "duckduckgo": _search_with_duckduckgo,
        "serper": _search_with_serper,
        "tavily": _search_with_tavily,
        "exa": _search_with_exa,
    }

    search_func = provider_map.get(provider, _search_with_duckduckgo)

    @tool("web_search", args_schema=WebSearchArgs)
    async def web_search_tool(query: str) -> str:
        """
        This tool searches the web for information.

        RESULT FORMAT:
        Returns search results with titles, URLs/citation_ids and snippets from web pages.
        Treat these as external sources requiring attribution.

        Args:
            query: Clear search query string (e.g., "latest Python 3.12 features")

        Example:
            web_search(query="current AI model benchmarks 2026")
        """
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                results = await search_func(query, provider_config)
                logger.info(f"Got web search results using {provider}: {len(results)} results")
                return json.dumps({
                    "ok": True,
                    "result_type": "web_search",
                    "web_results": results,
                    "query": query,
                })
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    backoff = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"Web search attempt {attempt + 1}/{MAX_RETRIES} failed with {provider}: {e}. "
                        f"Retrying in {backoff}s..."
                    )
                    await asyncio.sleep(backoff)

        logger.error(f"Web search failed after {MAX_RETRIES} attempts with {provider}: {last_error}")
        return json.dumps({
            "ok": False,
            "error": f"Web search failed: {str(last_error)}"
        })

    return web_search_tool
