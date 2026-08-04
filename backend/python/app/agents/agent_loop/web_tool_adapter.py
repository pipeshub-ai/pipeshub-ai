"""`WebToolAdapter`: restores citation formatting for the dynamic
`web_search`/`fetch_url` tools in the agent loop.

The legacy loop built every tool message through `ToolHandlerRegistry.
format_message` (`app/utils/streaming.py`), which is load-bearing for two
reasons: it registers each per-block URL into the shared `CitationRefMapper`
(via `display_url_for_llm`), and it emits the `url/Citation ID:` marker the
model needs in order to cite anything. `PipesHubStructuredToolAdapter.
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
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.actions.util.tool_summaries import parse_json_maybe
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from app.utils.tool_handlers import ToolHandlerRegistry

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = ["WebToolAdapter", "accumulate_web_records", "is_web_payload"]

# `result_type` values `web_search_tool.py`/`fetch_url_tool.py` stamp onto a
# successful envelope — see `ToolResultType` in `app/utils/tool_handlers.py`.
WEB_RESULT_TYPES: frozenset[str] = frozenset({"web_search", "url_content"})


def is_web_payload(payload: object) -> bool:
    """True only for a successful web_search/url_content envelope.

    Guards against `ToolHandlerRegistry.get_handler()` falling back to
    `ContentHandler` and `str()`-ing an `{"ok": false, "error": ...}`
    payload (or some unrelated dynamic tool's JSON) into the prompt — a
    failed web call should reach the model as the plain error text
    `fetch_url_tool.py`/`web_search_tool.py` already produce, not a
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
    ) -> None:
        super().__init__(structured_tool, app_name, tool_name)
        self._context = context

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
        handler = ToolHandlerRegistry.get_handler(payload)
        handler_context = {
            "ref_mapper": tool_state.get("citation_ref_mapper"),
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

        # Images are dropped here (not yet supported): agent_loop_lib's
        # `ToolMessage.content` is str-only, so an image block would need a
        # PRE_MODEL hook to re-inject it as an `ImagePart` — out of scope
        # for restoring text citations (see plan's "Images deferred" note).
        text_parts = [
            block["text"]
            for block in formatted_blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if not text_parts:
            return output

        sources = [Source(url=rec.get("url"), title=rec.get("title")) for rec in records if rec.get("url")]
        return ToolOutput(success=True, data="\n".join(text_parts), sources=sources)
