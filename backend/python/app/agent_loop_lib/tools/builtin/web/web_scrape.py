from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.tags import TAG_DEDUP_EXACT
from app.web.deadline import Deadline
from app.web.errors import WebError
from app.web.fetch.builtin import BuiltinFetcher

_MAX_CHARS = 3_000
_DEADLINE_S = 20.0


async def _read(url: str, max_chars: int) -> str:
    # The same guarded fetch chat uses: public addresses only, redirects checked, size capped.
    page = await BuiltinFetcher().fetch(url, Deadline.after(_DEADLINE_S))
    text = "\n\n".join(block.content for block in page.blocks)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated — {len(text)} chars total]"
    return text


class WebScrapeTool(Tool):
    """Fetch a URL and return its text content (HTML stripped)."""

    @property
    def name(self) -> str:
        return "web_scrape"

    @property
    def display_name(self) -> str | None:
        return "Read a web page"

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
    def tags(self) -> list[Tag]:
        return [TAG_DEDUP_EXACT]

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

        try:
            text = await _read(url, max_chars)
        except WebError as exc:
            return ToolOutput(success=False, error=f"Error fetching {url}: {exc.message}")
        except Exception as exc:
            return ToolOutput(success=False, error=f"Error fetching {url}: {exc}")
        return ToolOutput(success=True, data=text, sources=[Source(url=url)])
