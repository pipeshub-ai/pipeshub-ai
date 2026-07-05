from __future__ import annotations

import asyncio
import html as html_module
import re
import urllib.request
from typing import Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
_MAX_CHARS = 3_000


def _fetch_and_strip(url: str, max_chars: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        content_type = r.headers.get("Content-Type", "")
        if "text" not in content_type and "html" not in content_type:
            return f"[Non-text response: {content_type}]"
        raw = r.read(max_chars * 6).decode("utf-8", errors="replace")

    # Strip <script> and <style> blocks first
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities
    text = html_module.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated — {len(text)} chars total]"
    return text


class WebScrapeTool(Tool):
    """Fetch a URL and return its text content (HTML stripped)."""

    @property
    def name(self) -> str:
        return "web_scrape"

    @property
    def short_description(self) -> str:
        return "Fetch a URL and return its text content."

    @property
    def description(self) -> str:
        return "Fetch a URL and return its full text content. Use to read the detail behind a search result."

    @property
    def path(self) -> str:
        return "/toolsets/web/web_scrape"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="url",
                type=ParameterType.STRING,
                description="URL to fetch",
                required=True,
            ),
            ToolParameter(
                name="max_chars",
                type=ParameterType.INTEGER,
                description="Maximum characters to return (default 8000)",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        url: str = kwargs["url"]
        max_chars: int = int(kwargs.get("max_chars") or _MAX_CHARS)

        loop = asyncio.get_event_loop()
        try:
            text = await loop.run_in_executor(None, _fetch_and_strip, url, max_chars)
        except Exception as exc:
            return ToolOutput(success=False, error=f"Error fetching {url}: {exc}")
        return ToolOutput(success=True, data=text, sources=[Source(url=url)])
