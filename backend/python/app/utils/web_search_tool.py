from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.utils.logger import create_logger

logger = create_logger(__name__)

class WebSearchArgs(BaseModel):
    """Arguments for web search tool."""
    query: str = Field(
        ...,
        description="Search query to find current information on the web"
    )


def _search_with_duckduckgo(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Search using DuckDuckGo (default, no API key needed)."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw_results = ddgs.text(
            query,
            max_results=10,
            timelimit=None,
        )
    return [
        {"title": r.get("title", ""), "link": r.get("href", ""), "snippet": r.get("body", "")}
        for r in (raw_results or [])
    ]


def _search_with_serper(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
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

    with httpx.Client() as client:
        response = client.post(url, headers=headers, json=payload, timeout=30.0)
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


def _search_with_tavily(query: str, config: dict[str, Any]) -> list[dict[str, Any]]:
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

    with httpx.Client() as client:
        response = client.post(url, headers=headers, json=payload, timeout=30.0)
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

def create_web_search_tool(
    config: dict[str, Any] | None = None,
) -> BaseTool:
    """
    Factory function to create web search tool.

    Args:
        config: Optional configuration dict with structure:
            {
                "provider": "duckduckgo" | "serper" | "tavily",
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
    }

    search_func = provider_map.get(provider, _search_with_duckduckgo)

    @tool("web_search", args_schema=WebSearchArgs)
    def web_search_tool(query: str) -> dict[str, Any]:
        """
        This tool searches the web for information.

        RESULT FORMAT:
        Returns search results with titles, URLs, snippets and citation_ids from web pages. The content is returned as array of blocks.
        Treat these as external sources requiring attribution.

        Args:
            query: Clear search query string (e.g., "latest Python 3.12 features")

        Example:
            web_search(query="current AI model benchmarks 2026")
        """
        try:
            results = search_func(query, provider_config)
            logger.info(f"Got web search results using {provider}: {len(results)} results")
            return {
                "ok": True,
                "result_type": "web_search",
                "web_results": results,
                "query": query,
            }
        except Exception as e:
            print(f"Web search failed with {provider}: {str(e)}")
            return {
                "ok": False,
                "error": f"Web search failed: {str(e)}"
            }

    return web_search_tool
