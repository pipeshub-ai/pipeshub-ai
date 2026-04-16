"""
Tool Result Handler Registry - Extensible system for handling different tool result types.

This module provides a plugin pattern for tool result handling, allowing new tools
to be added without modifying the core execute_tool_calls logic in streaming.py.

Usage:
    1. Tools return a 'result_type' key in their output dict
    2. Handlers are registered for each result type
    3. execute_tool_calls uses the registry to dispatch handling
"""

import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from app.utils.chat_helpers import generate_text_fragment_url
from app.utils.citations import (
    display_url_for_llm,
)
from app.utils.image_utils import _fetch_image_as_base64, supported_mime_types
from app.utils.logger import create_logger

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
        return {
            "ok": True,
            "content": tool_result.get("content", str(tool_result)),
        }


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
            "text": f"web_search_query: {query}\nBlocks retrieved from web search:",
        }]

        for result in web_results:
            if not isinstance(result, dict):
                continue
            title = result.get("title", "")
            link = result.get("link", "")
            snippet = result.get("snippet", "")

            display_url = display_url_for_llm(link, ref_mapper)

            formatted_blocks.append({
                "type": "text",
                "text": f"title: {title}\nurl/citation id: {display_url}\ncontent: {snippet}",
            })

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
            records.append({
                "url": link,
                "title": title,
                "content": snippet or title or "Search result",
                "source_type": "web",
                "org_id": org_id,
            })

        return records


def _block_citation_url(base_url: str, block) -> str:
    """Build the citation URL for a single block of a fetched page.

    Text blocks get a text-fragment URL derived from their content so each block
    resolves to its own location. Image blocks use the image's own URL when it is
    a full http(s) URL; otherwise they fall back to the page URL.
    """
    if block.type == "image":
        img_uri = getattr(block, "url", "") or ""
        if img_uri.startswith("http"):
            return img_uri
        return base_url
    return generate_text_fragment_url(base_url, getattr(block, "content", "") or "")


class UrlContentHandler(ToolResultHandler):
    """Handler for fetched URL content with block structure."""

    async def format_message(self, tool_result: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        url = tool_result.get("url", "")
        blocks = tool_result.get("blocks", [])
        ref_mapper = context.get("ref_mapper")
        include_images = context.get("include_images")
        include_images = include_images if isinstance(include_images, bool) else False
        raw_max_images = context.get("max_images", 3)
        try:
            max_images = int(raw_max_images)
        except (TypeError, ValueError):
            max_images = 3
        max_images = max(1, min(max_images, 500))

        # Phase 1: Collect all remote image URLs that need fetching
        images_to_fetch = []
        image_results = {}

        count = 0

        if include_images:
            for index, block in enumerate(blocks):
                if block.type == "image":
                    img_uri = block.url
                    if img_uri and img_uri.startswith("http"):
                        if not img_uri.endswith((".svg", ".gif", ".ico")):
                            images_to_fetch.append((index, img_uri))
                            count += 1
                            if count >= max_images:
                                break


            # Phase 2: Fetch all images in parallel
            if images_to_fetch:
                logger.info("Fetching %d images in parallel", len(images_to_fetch))
                fetch_tasks = [_fetch_image_as_base64(img_url) for (_, img_url) in images_to_fetch]
                fetched_images = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                # Create mapping: index -> fetched_result
                for (index, _), fetched in zip(images_to_fetch, fetched_images):
                    image_results[index] = fetched

        # Phase 3: Build formatted_blocks using pre-fetched results
        count = 0
        formatted_blocks = [{
            "type": "text",
            "text": "Blocks of content from the URL:",
        }]
        for index, block in enumerate(blocks):
            block_citation_url = _block_citation_url(url, block)
            display_url = display_url_for_llm(block_citation_url, ref_mapper)
            if block.type == "image":
                if not include_images or count >= max_images:
                    continue
                img_uri = block.url

                if img_uri:

                    if img_uri.startswith("data:image/"):
                        mime_type = img_uri.split(";")[0].split(":")[1]
                        image_base64 = img_uri.split(",")[1]
                        if mime_type not in supported_mime_types:
                            logger.warning("Not a valid image mime type: %s", mime_type)
                            continue
                        formatted_blocks.append({
                            "type": "text",
                            "text": f"url/citation id: {display_url}\ncontent(image):",
                        })
                        formatted_blocks.append({
                            "type": "image",
                            "base64": image_base64,
                            "mime_type": mime_type,
                        })
                        count += 1
                    elif index in image_results:
                        fetched = image_results[index]
                        if fetched:
                            image_base64, mime_type = fetched
                            if mime_type not in supported_mime_types:
                                logger.warning("Not a valid image mime type: %s", mime_type)
                                continue
                            formatted_blocks.append({
                                "type": "text",
                                "text": f"url/citation id: {display_url}\ncontent(image):",
                            })
                            formatted_blocks.append({
                                "type": "image",
                                "base64": image_base64,
                                "mime_type": mime_type,
                            })
                            count += 1
                    elif not img_uri.startswith("http"):
                        logger.warning("Not a valid image url: %s", img_uri[:100])
            else:
                formatted_blocks.append({
                    "type": "text",
                    "text": f"url/citation id: {display_url}\ncontent: {block.content}",
                })

        return formatted_blocks

    def extract_records(self, tool_result: dict[str, Any], org_id: str | None=None) -> list[dict[str, Any]]:
        """Extract URL blocks as records for citation tracking."""
        url = tool_result.get("url", "")
        blocks = tool_result.get("blocks", [])

        records = []
        for block in blocks:
            block_citation_url = _block_citation_url(url, block)
            if block.type == "text":
                content = getattr(block, "content", "") or ""
            else:
                content = getattr(block, "alt", "") or "Image"
            records.append({
                "url": block_citation_url,
                "content": content,
                "source_type": "web",
                "org_id": org_id,
            })
        return records


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
