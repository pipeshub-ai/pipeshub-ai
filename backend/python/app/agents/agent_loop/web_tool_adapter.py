"""`WebToolAdapter`: restores citation formatting for the dynamic
`web_search`/`fetch_url` tools in the agent loop.

The legacy loop built every tool message through `ToolHandlerRegistry.
format_message` (`app/utils/streaming.py`), which is load-bearing for two
reasons: it registers each per-block URL into the shared `CitationRefMapper`
(via `display_url_for_llm`), and it emits the `url/Citation ID:` marker the
model needs in order to cite anything (an engine run's `url:` and `Citation ID:`
fields instead, see `WebCitationStyle`). `PipesHubStructuredToolAdapter.
execute()` hands the tool's raw JSON straight to the model instead, so
neither of those happen — see the "Web citation restore" plan for the full
trace.

`WebToolAdapter` sits between the web `StructuredTool`s and the loop,
re-using the SAME `ToolHandlerRegistry`/handlers the legacy path and the
prefetch context builder (`chat_modes/prefetch.py`) use, so citation-URL
formatting has exactly one implementation. It does not replace `_to_
tool_output`'s transport/error handling — `execute()` delegates to
`super().execute()` first and only reformats a SUCCESSFUL web-shaped
payload.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.tags import (
    TAG_EFFECT_READ,
    TAG_FLOW_OPENS_ADDRESS,
    TAG_FLOW_PLATFORM_WEB,
    TAG_PROVIDER_PLATFORM,
    TAG_SINK_EXTERNAL,
)
from app.agents.actions.util.tool_summaries import parse_json_maybe
from app.agents.agent_loop.image_parts import admitted_image_parts
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from app.utils.citations import WEB_CITATION_STYLE, WebCitationStyle
from app.utils.tool_handlers import ToolHandlerRegistry
from app.web.addresses import web_addresses
from app.web.budget import WEB_BUDGET_KEY, WebBudget
from app.web.tools.views import web_view

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

    from app.agent_loop_lib.core.messages import Part
    from app.agent_loop_lib.core.types import ToolResult
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = ["WebToolAdapter", "accumulate_web_records", "is_web_payload"]

# `result_type` values the tools in `app/web/tools/` stamp onto a
# successful envelope — see `ToolResultType` in `app/utils/tool_handlers.py`.
WEB_RESULT_TYPES: frozenset[str] = frozenset({"web_search", "url_content"})
FETCH_URL = "fetch_url"


def is_web_payload(payload: object) -> bool:
    """True only for a successful web_search/url_content envelope.

    Guards against `ToolHandlerRegistry.get_handler()` falling back to
    `ContentHandler` and `str()`-ing an `{"ok": false, "error": ...}`
    payload (or some unrelated dynamic tool's JSON) into the prompt — a
    failed web call should reach the model as the plain error text
    the tools in `app/web/tools/` already produce, not a
    reformatted "Blocks of content from the URL:" wrapper around it.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("ok") is False:
        return False
    if payload.get("result_type") in WEB_RESULT_TYPES:
        return True
    # Backwards-compatible shape sniffing, mirrors
    # `ToolHandlerRegistry.get_handler`'s own fallback rule.
    return "web_results" in payload or "blocks" in payload


def accumulate_web_records(tool_state: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Append `records` onto `tool_state["web_records"]`, deduped by `url`.

    Records with empty `content` are dropped here rather than stored: 
    `normalize_citations_and_chunks` (`citations.py`) skips them at citation
    time anyway (a citation with no content is useless), so keeping them
    out of `web_records` avoids padding it with entries no citation pass
    will ever use.
    """
    if not records:
        return
    existing: list[dict[str, Any]] = tool_state.setdefault("web_records", [])
    seen_urls = {rec.get("url") for rec in existing if rec.get("url")}
    for rec in records:
        url = rec.get("url")
        if not url or url in seen_urls or not rec.get("content"):
            continue
        existing.append(rec)
        seen_urls.add(url)


_WEB_TOOL_SHORT_DESCRIPTIONS: dict[str, str] = {
    "web_search": "Search the public web for information.",
    "fetch_url": "Fetch and extract text from a public webpage (unauthenticated GET).",
}


ImageLoader = Callable[[list[str]], Awaitable[list[str | None]]]


class WebToolAdapter(PipesHubStructuredToolAdapter):
    """Wraps the dynamic `web_search`/`fetch_url` `StructuredTool`s so their
    output is rendered through `ToolHandlerRegistry.format_message` before
    reaching the model — see module docstring."""

    def __init__(
        self,
        structured_tool: "StructuredTool",
        app_name: str,
        tool_name: str,
        context: "AgentContext",
        *,
        image_loader: "ImageLoader | None" = None,
        max_images: int = 0,
    ) -> None:
        """``image_loader`` (``fetch_url`` only, when an admin has web images turned on) reads
        up to ``max_images`` of a page's images as ``data:`` URIs; each failed one is None."""
        # Search and fetch change nothing anywhere; their requests carry what the model chose (a query, a URL) to a host
        # outside the organization (FG2). The flow guard never holds them: a query goes only to the search provider, and
        # after a private read a fetch opens only an address the run was given (`app.web.addresses`).
        opens = (TAG_FLOW_OPENS_ADDRESS,) if tool_name == FETCH_URL else ()
        super().__init__(
            structured_tool,
            app_name,
            tool_name,
            effect=TAG_EFFECT_READ,
            sink=TAG_SINK_EXTERNAL,
            provider=TAG_PROVIDER_PLATFORM,
            flow=(TAG_FLOW_PLATFORM_WEB, *opens),
        )
        self._context = context
        self._image_loader = image_loader
        self._max_images = max_images
        # The card's view of each call, by the text the call returned: by the time the loop asks
        # for a view, the result holds the text sent to the model, not the tool's payload.
        self._views: dict[str, dict[str, Any]] = {}

    def result_view(self, args: dict[str, Any], result: "ToolResult") -> dict[str, Any] | None:
        content = result.content
        if isinstance(content, list):
            # A page sent with its images: the view is keyed by the page's text.
            content = next((part.text for part in content if isinstance(part, TextPart)), None)
        return self._views.pop(content, None) if isinstance(content, str) else None

    @property
    def short_description(self) -> str:
        """One-liner for the Available Tools index — the full docstring
        (WHEN TO USE / Args / Examples) ships via the function schema and
        must not be duplicated in the system prompt."""
        # Strip the ``dynamic__`` prefix to look up the base tool name.
        base = self.name.split("__", 1)[-1] if "__" in self.name else self.name
        return _WEB_TOOL_SHORT_DESCRIPTIONS.get(base, super().short_description)

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        output = await super().execute(**kwargs)
        if not output.success:
            return output

        payload = parse_json_maybe(output.data)
        if not is_web_payload(payload):
            return output

        tool_state = self._context.tool_state
        web_addresses(tool_state).note_result(payload)
        handler = ToolHandlerRegistry.get_handler(payload)
        handler_context = {
            "ref_mapper": tool_state.get("citation_ref_mapper"),
            # An engine run reads the real link and the Citation ID as two fields (`WebCitationStyle`).
            WEB_CITATION_STYLE: tool_state.get(WEB_CITATION_STYLE, WebCitationStyle.ALIAS),
            "config_service": self._context.config_service,
            "is_multimodal_llm": self._context.is_multimodal_llm,
        }
        try:
            formatted_blocks = await handler.format_message(payload, handler_context)
        except Exception:
            logger.exception("WebToolAdapter: format_message failed for %s", self.name)
            return output

        records = handler.extract_records(payload, org_id=self._context.org_id)
        accumulate_web_records(tool_state, records)

        text_parts = [
            block["text"]
            for block in formatted_blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if not text_parts:
            return output

        sources = [Source(url=rec.get("url"), title=rec.get("title")) for rec in records if rec.get("url")]
        text = "\n".join([*text_parts, *_footer(payload, tool_state)])
        images = await self._page_images(payload)
        if any(isinstance(part, ImagePart) for part in images):
            data: str | list[Part] = [TextPart(text=text), *images]
        else:
            # Every image refused (or none sent): one string, with the reasons.
            data = "\n".join([text, *(part.text for part in images if isinstance(part, TextPart))])
        view = web_view(payload)
        if view is not None:
            # `result_view` finds the card by the returned string, or a list's first text part.
            self._views[data if isinstance(data, str) else text] = view
        return ToolOutput(success=True, data=data, sources=sources)

    async def _page_images(self, payload: dict[str, Any]) -> list["Part"]:
        """The page's first images as parts after the image admission check, for a model that
        reads images. None for a model that does not: the page's text already describes them,
        and a note per image would say nothing the model asked about."""
        if self._image_loader is None or self._max_images <= 0 or not self._context.is_multimodal_llm:
            return []
        urls = [
            image["url"] for image in payload.get("images") or []
            if isinstance(image, dict) and isinstance(image.get("url"), str)
        ][: self._max_images]
        if not urls:
            return []
        try:
            uris = [uri for uri in await self._image_loader(urls) if uri]
        except Exception:
            logger.warning("WebToolAdapter: page images could not be read for %s", self.name, exc_info=True)
            return []
        # Already decoded in full as they were read (`fetch_image_data_uri`).
        return await admitted_image_parts(uris, self._context, source="on the page", verified=True) if uris else []


def _footer(payload: dict[str, Any], tool_state: dict[str, Any]) -> list[str]:
    """What the model must know about the result beyond its blocks (WP-62): that a large page was cut and how to read
    the rest, and, on a long run, the web allowance still left."""
    lines: list[str] = []
    truncated = payload.get("truncated")
    if isinstance(truncated, str) and truncated:
        lines.append(f"[{truncated}]")
    budget = tool_state.get(WEB_BUDGET_KEY)
    allowance = budget.allowance_line() if isinstance(budget, WebBudget) else None
    if allowance is not None:
        lines.append(allowance)
    return lines
