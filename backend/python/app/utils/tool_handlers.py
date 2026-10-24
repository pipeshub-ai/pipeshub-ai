"""
Tool Result Handler Registry - Extensible system for handling different tool result types.

This module provides a plugin pattern for tool result handling, allowing new tools
to be added without modifying the core execute_tool_calls logic in streaming.py.

Usage:
    1. Tools return a 'result_type' key in their output dict
    2. Handlers are registered for each result type
    3. execute_tool_calls uses the registry to dispatch handling
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from app.utils.chat_helpers import generate_text_fragment_url
from app.utils.citations import (
    display_url_for_llm,
)
from app.utils.logger import create_logger
from app.utils.text_fragments import SourceFormat
from app.web.untrusted import FENCE_CLOSE, defuse, fence_open
from app.web.url import display_domain

logger = create_logger(__name__)


class ToolResultType(str, Enum):
    """Standard tool result types. New tools can add entries here."""
    RECORDS = "records"        # Document records (fetch_full_record)
    WEB_SEARCH = "web_search"  # Web search results
    URL_CONTENT = "url_content"  # Fetched URL content with blocks
    CONTENT = "content"        # Generic text/structured content (default)


class ToolResultHandler(ABC):
    """
    Base class for tool result handlers.

    Each handler defines how to:
    1. format_message: Convert tool result to LLM-consumable format
    2. post_process: Optional processing (e.g., token counting, retrieval)
    3. extract_records: Extract any records for citation tracking
    """

    @abstractmethod
    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """
        Format tool result for ToolMessage content.

        Args:
            tool_result: Raw result from tool execution
            context: Execution context (message_contents, etc.)

        Returns:
            Dict to be JSON-serialized as ToolMessage content
        """
        pass

    def extract_records(self, tool_result: dict[str, Any], org_id: str | None=None) -> list[dict[str, Any]]:
        """
        Extract records from tool result for citation tracking.

        Override this if your tool returns records that need citation handling.

        Args:
            tool_result: Raw result from tool execution

        Returns:
            List of record dicts, empty list if no records
        """
        return []

    def needs_token_management(self) -> bool:
        """
        Whether this handler's results need token counting/management.

        Override to return True if results may exceed context limits
        and need retrieval service fallback.
        """
        return False


class ContentHandler(ToolResultHandler):
    """Default handler for generic content results."""

    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        result = tool_result.get("content")
        if result is None:
            return str(tool_result)
        messages = [{"type": "text", "text": "Internal Knowledge Search results:"}]
        if isinstance(result, list):
            messages.extend(result)
        else:
            messages.append(result)
        return messages



class RecordsHandler(ToolResultHandler):
    """Handler for fetch_full_record style results with document records."""

    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        message_contents = context.get("message_contents", [])
        flattened = [msg for message_content in message_contents for msg in message_content]
        not_available = tool_result.get("not_available_ids", {})
        if not_available:
            ids_str = ", ".join(f"'{rid}'" for rid in not_available)
            flattened.append({"type": "text", "text": f"\nNote: The following record(s) are not available: {ids_str}"})
        return flattened


    def extract_records(self, tool_result: dict[str, Any], org_id: str | None = None) -> list[dict[str, Any]]:
        return tool_result.get("records", [])

    def needs_token_management(self) -> bool:
        return True

class WebSearchHandler(ToolResultHandler):
    """Handler for web search results."""

    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        web_results = tool_result.get("web_results", [])
        if not isinstance(web_results, list):
            web_results = []

        query = tool_result.get("query", "")
        ref_mapper = context.get("ref_mapper")

        formatted_blocks = [{
            "type": "text",
            "text": f"web_search_query: {query}\nBlocks retrieved from web search:\n{fence_open('web_search')}",
        }]

        for result in web_results:
            if not isinstance(result, dict):
                continue
            title = result.get("title", "")
            link = result.get("link", "")
            snippet = result.get("snippet", "")

            citation_url = (
                generate_text_fragment_url(link, snippet, SourceFormat.PLAIN) if snippet else link
            )
            display_url = display_url_for_llm(citation_url, ref_mapper)
            lines = [f"title: {defuse(title)}", f"site: {result.get('domain') or display_domain(link)}"]
            if result.get("published"):
                lines.append(f"published: {defuse(str(result['published']))}")
            lines += [f"url/Citation ID: {display_url}", f"content: {defuse(snippet)}"]
            formatted_blocks.append({"type": "text", "text": "\n".join(lines)})

        formatted_blocks.append({"type": "text", "text": FENCE_CLOSE})
        return formatted_blocks

    def extract_records(self, tool_result: dict[str, Any], org_id: str | None = None) -> list[dict[str, Any]]:
        web_results = tool_result.get("web_results", [])
        if not isinstance(web_results, list):
            return []

        records = []
        for result in web_results:
            if not isinstance(result, dict):
                continue
            link = result.get("link", "")
            snippet = result.get("snippet", "")
            title = result.get("title", "")
            citation_url = (
                generate_text_fragment_url(link, snippet, SourceFormat.PLAIN) if snippet else link
            )
            records.append({
                "url": citation_url,
                "title": title,
                "content": snippet or title or "Search result",
                "source_type": "web",
                "org_id": org_id,
            })

        return records


def _block_attr(block, attr: str, default: str = "") -> str:
    """Access a block attribute that may be a dataclass or a plain dict."""
    if isinstance(block, dict):
        return block.get(attr, default)
    return getattr(block, attr, default)


def _block_citation_url(base_url: str, block) -> str:
    """A text-fragment URL for one text block of a fetched page, so each block cites its own place."""
    return generate_text_fragment_url(
        base_url, _block_attr(block, "content", ""), SourceFormat.PLAIN
    )


def _text_blocks(tool_result: dict[str, Any]) -> list[Any]:
    return [b for b in tool_result.get("blocks", []) if _block_attr(b, "type") == "text"]


class UrlContentHandler(ToolResultHandler):
    """Handler for fetched URL content with block structure."""

    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        # Only text reaches the model: a tool message carries text alone, so image blocks
        # (from older stored results) are skipped rather than downloaded and dropped.
        url = tool_result.get("url", "")
        ref_mapper = context.get("ref_mapper")
        formatted_blocks = [{"type": "text", "text": f"Blocks of content from the URL:\n{fence_open(display_domain(url))}"}]
        for block in _text_blocks(tool_result):
            display_url = display_url_for_llm(_block_citation_url(url, block), ref_mapper)
            formatted_blocks.append({
                "type": "text",
                "text": f"url/Citation ID: {display_url}\ncontent: {defuse(_block_attr(block, 'content', ''))}",
            })
        formatted_blocks.append({"type": "text", "text": FENCE_CLOSE})
        return formatted_blocks

    def extract_records(self, tool_result: dict[str, Any], org_id: str | None=None) -> list[dict[str, Any]]:
        """Extract URL blocks as records for citation tracking."""
        url = tool_result.get("url", "")
        return [
            {
                "url": _block_citation_url(url, block),
                "title": tool_result.get("title") or "",
                "content": _block_attr(block, "content", ""),
                "source_type": "web",
                "org_id": org_id,
            }
            for block in _text_blocks(tool_result)
        ]


class ToolHandlerRegistry:
    """
    Registry for tool result handlers.

    Provides dispatch mechanism for handling different tool result types.
    Falls back to ContentHandler for unknown types.
    """

    _handlers: dict[str, ToolResultHandler] = {}
    _default_handler: ToolResultHandler = ContentHandler()

    @classmethod
    def register(cls, result_type: str, handler: ToolResultHandler) -> None:
        """
        Register a handler for a result type.

        Args:
            result_type: The result_type string tools will return
            handler: Handler instance for this type
        """
        cls._handlers[result_type] = handler
        logger.debug(f"Registered tool handler for result_type: {result_type}")

    @classmethod
    def get_handler(cls, tool_result: dict[str, Any]) -> ToolResultHandler:
        """
        Get appropriate handler for a tool result.

        Determines handler by:
        1. Explicit 'result_type' key in tool_result (preferred)
        2. Presence of known keys ('records', 'web_results') for backwards compatibility
        3. Falls back to default ContentHandler

        Args:
            tool_result: The tool's output dict

        Returns:
            Appropriate ToolResultHandler instance
        """
        # Check explicit result_type first
        result_type = tool_result.get("result_type")
        if result_type and result_type in cls._handlers:
            return cls._handlers[result_type]

        # Backwards compatibility: infer from known keys
        if "records" in tool_result:
            return cls._handlers.get(ToolResultType.RECORDS.value, cls._default_handler)
        if "web_results" in tool_result:
            return cls._handlers.get(ToolResultType.WEB_SEARCH.value, cls._default_handler)

        return cls._default_handler

    @classmethod
    def list_handlers(cls) -> list[str]:
        """List all registered handler types."""
        return list(cls._handlers.keys())


def _register_builtin_handlers() -> None:
    """Register handlers for built-in tool types."""
    ToolHandlerRegistry.register(ToolResultType.RECORDS.value, RecordsHandler())
    ToolHandlerRegistry.register(ToolResultType.WEB_SEARCH.value, WebSearchHandler())
    ToolHandlerRegistry.register(ToolResultType.URL_CONTENT.value, UrlContentHandler())
    ToolHandlerRegistry.register(ToolResultType.CONTENT.value, ContentHandler())


# Auto-register built-in handlers on module import
_register_builtin_handlers()
