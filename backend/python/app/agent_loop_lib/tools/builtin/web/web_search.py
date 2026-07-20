from __future__ import annotations

import asyncio
import html as html_module
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.tags import TAG_DEDUP_EXACT

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


# ── duckduckgo-search SDK (pip install duckduckgo-search) ─────────────────
# Preferred backend: handles rate limiting, session management, retries.

def _ddg_sdk_search(query: str, count: int) -> list[dict]:
    from ddgs import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=count):
            results.append({
                "title":   r.get("title", ""),
                "url":     r.get("href",  ""),
                "snippet": r.get("body",  ""),
            })
    return results


# ── Brave Search API (set BRAVE_API_KEY) ───────────────────────────────────

def _brave_search(query: str, count: int) -> list[dict]:
    url = (
        "https://api.search.brave.com/res/v1/web/search"
        f"?q={urllib.parse.quote_plus(query)}&count={count}"
    )
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return [
        {
            "title":   item.get("title", ""),
            "url":     item.get("url",   ""),
            "snippet": item.get("description", ""),
        }
        for item in data.get("web", {}).get("results", [])
    ]


# ── Raw DDG HTML scraping (last resort) ────────────────────────────────────
# Brittle: DDG rate-limits after a few rapid requests. Use only as fallback.

def _ddg_html_search(query: str, count: int) -> list[dict]:
    data = urllib.parse.urlencode({"q": query, "b": "", "kl": "us-en"}).encode()
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=data,
        method="POST",
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode("utf-8", errors="replace")

    link_re = re.compile(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    links    = link_re.findall(raw)
    snippets = snippet_re.findall(raw)

    results: list[dict] = []
    for i, (href, title_html) in enumerate(links[:count]):
        actual_url = href
        m = re.search(r"uddg=([^&\s\"]+)", href)
        if m:
            actual_url = urllib.parse.unquote(m.group(1))
        title   = html_module.unescape(re.sub(r"<[^>]+>", "", title_html).strip())
        snippet = ""
        if i < len(snippets):
            snippet = html_module.unescape(re.sub(r"<[^>]+>", "", snippets[i]).strip())
        if title and actual_url.startswith("http"):
            results.append({"title": title, "url": actual_url, "snippet": snippet})

    return results


# ── Provider selection ─────────────────────────────────────────────────────

def _search(query: str, count: int) -> list[dict]:
    """Try providers in priority order, return first non-empty result."""
    # 1. Brave (most reliable, needs API key)
    if os.environ.get("BRAVE_API_KEY"):
        try:
            return _brave_search(query, count)
        except Exception:
            pass

    # 2. ddgs SDK (pip install ddgs — no key, handles rate limits well)
    try:
        import ddgs  # noqa: F401
        return _ddg_sdk_search(query, count)
    except ImportError:
        pass
    except Exception:
        pass

    # 3. Raw DDG HTML (last resort — rate-limited after a few rapid calls)
    try:
        return _ddg_html_search(query, count)
    except Exception:
        pass

    return []


def _format_results(query: str, results: list[dict]) -> str:
    if not results:
        return (
            f"No results found for: {query!r}\n"
            "Tip: run `pip install ddgs` or set BRAVE_API_KEY env var."
        )
    lines = [f"Web search results for: {query!r}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["url"]:
            lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:300]}")
        lines.append("")
    return "\n".join(lines)


class WebSearchTool(Tool):
    """Search the web.

    Backends tried in order:
      1. Brave Search API — set BRAVE_API_KEY env var
      2. ddgs SDK         — pip install ddgs  (recommended, no key needed)
      3. DDG HTML scrape  — built-in fallback, rate-limited after ~3 requests
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def short_description(self) -> str:
        return "Search the web and return a ranked list of results."

    @property
    def description(self) -> str:
        return "Search the web and return a ranked list of results with titles, URLs, and snippets."

    @property
    def path(self) -> str:
        return "/toolsets/web/web_search"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_DEDUP_EXACT]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True,
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Number of results (default 5)",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query:       str = kwargs["query"]
        max_results: int = int(kwargs.get("max_results") or 5)
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _search, query, max_results)
        sources = [
            Source(url=r["url"], title=r.get("title"), query=query)
            for r in results if r.get("url")
        ]
        return ToolOutput(success=True, data=_format_results(query, results), sources=sources)
