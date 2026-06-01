"""
Confluence Block Parser

Converts Atlassian Document Format (ADF) nodes into Pipeshub Block and BlockGroup objects.
This parser is designed to be extensible - new ADF node types can be easily added.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.models.blocks import (
    Block,
    BlockComment,
    BlockContainerIndex,
    BlockGroup,
    BlockGroupChildren,
    BlockSubType,
    BlockType,
    ChildRecord,
    CodeMetadata,
    DataFormat,
    GroupSubType,
    GroupType,
    ImageMetadata,
    LinkMetadata,
    ListMetadata,
    TableMetadata,
    TableRowMetadata,
)


class ConfluenceBlockParser:
    """
    Parser for converting ADF (Atlassian Document Format) nodes to Pipeshub Block/BlockGroup objects.

    This class provides a way to handle different ADF node types.
    To add support for a new node type, add a method following the pattern:
    `_parse_{node_type}(self, node: Dict[str, Any], ...) -> Optional[Block|BlockGroup]`
    """

    # Constants
    MAX_TABLE_ROWS_DISPLAY = 100

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the parser with a logger."""
        self.logger = logger
        self._block_counter = 0
        self._block_group_counter = 0

    @staticmethod
    def _normalize_url(url: str | None) -> str | None:
        """
        Normalize URL for Pydantic HttpUrl validation.

        Args:
            url: URL string (can be empty string or None)

        Returns:
            URL string if valid, None if empty or None
        """
        if not url or url.strip() == "":
            return None
        return url

    def _construct_block_url(
        self,
        parent_page_url: str | None,
        node_id: str | None
    ) -> str | None:
        """
        Construct block URL by appending node ID anchor to parent page URL.

        Confluence doesn't have direct block anchors, so we use the parent URL.

        Args:
            parent_page_url: URL of the parent page
            node_id: ADF node localId (if available)

        Returns:
            Parent page URL (Confluence doesn't support block-level URLs)
        """
        return parent_page_url

    async def parse_adf(
        self,
        adf_content: dict[str, Any],
        media_fetcher: Callable[[str, str], Awaitable[str | None]] | None = None,
        parent_page_url: str | None = None,
        page_id: str | None = None,
    ) -> tuple[list[Block], list[BlockGroup]]:
        """
        Parse ADF content into blocks and block groups.

        Args:
            adf_content: ADF document (dict with 'content' array)
            media_fetcher: Async callback that fetches media as base64 data URI
            parent_page_url: URL of the parent page
            page_id: ID of the parent page

        Returns:
            Tuple of (blocks, block_groups)
        """
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []

        if not adf_content or not isinstance(adf_content, dict):
            return blocks, block_groups

        # Reset counters
        self._block_counter = 0
        self._block_group_counter = 0

        # Parse root content nodes
        content_nodes = adf_content.get("content", [])

        for node in content_nodes:
            await self._process_node_recursive(
                node=node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=None,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
            )

        return blocks, block_groups

    def create_title_block(
        self,
        title: str,
        page_id: str,
        weburl: str | None = None,
    ) -> Block:
        """
        Create an H1 heading block for the page/blog post title.

        Confluence ADF body does not include the page title; callers should prepend
        this block so the title appears in the block stream for indexing.
        """
        return Block(
            id=str(uuid4()),
            index=0,
            parent_index=None,
            type=BlockType.TEXT,
            sub_type=BlockSubType.HEADING,
            format=DataFormat.MARKDOWN,
            data=f"# {title}",
            source_id=f"{page_id}_title",
            weburl=self._normalize_url(weburl),
        )

    async def _process_node_recursive(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable[[str, str], Awaitable[str | None]] | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """
        Recursively process an ADF node and its children.

        Args:
            node: ADF node to process
            blocks: List to append blocks to
            block_groups: List to append block groups to
            parent_group_index: Index of parent block group
            media_fetcher: Media fetcher callback
            parent_page_url: Parent page URL
            page_id: Parent page ID
            list_depth: Current list nesting depth
            parent_list_style: Style of parent list (bullet, numbered)

        Returns:
            List of BlockContainerIndex for created blocks/groups
        """
        if not isinstance(node, dict):
            return []

        node_type = node.get("type", "")
        if not node_type:
            return []

        # Dispatch to type-specific parser
        parser_method = getattr(
            self,
            f"_parse_{node_type}",
            self._parse_unknown
        )

        try:
            result = await parser_method(
                node=node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )

            return result if result else []

        except Exception as e:
            self.logger.warning(
                f"Error parsing ADF node type '{node_type}': {e}",
                exc_info=True
            )
            return []

    def _extract_text_from_content(
        self,
        content: list[dict[str, Any]],
        *,
        strip_marks: bool = False,
    ) -> str:
        """
        Extract text from ADF content array (recursively processes text nodes).

        Args:
            content: List of ADF nodes
            strip_marks: If True, ignore text formatting marks

        Returns:
            Extracted text with markdown formatting
        """
        if not content:
            return ""

        text_parts = []
        for node in content:
            node_type = node.get("type", "")

            if node_type == "text":
                text = node.get("text", "")
                if text and not strip_marks:
                    marks = node.get("marks", [])
                    text = self._apply_marks(text, marks)
                text_parts.append(text)
            elif node_type == "hardBreak":
                text_parts.append("\n")
            elif node_type == "mention":
                attrs = node.get("attrs", {})
                mention_text = attrs.get("text", attrs.get("id", "mention"))
                text_parts.append(f"@{mention_text}")
            elif node_type == "emoji":
                attrs = node.get("attrs", {})
                short_name = attrs.get("shortName", "")
                if short_name:
                    text_parts.append(f":{short_name}:")
                else:
                    text_parts.append(attrs.get("text", ""))
            elif node_type == "inlineCard":
                attrs = node.get("attrs", {})
                url = attrs.get("url", "")
                if url:
                    text_parts.append(f"[{url}]({url})")
            elif node_type == "status":
                attrs = node.get("attrs", {})
                status_text = attrs.get("text", "")
                if status_text:
                    text_parts.append(f"[{status_text}]")
            elif node_type == "date":
                attrs = node.get("attrs", {})
                timestamp = attrs.get("timestamp", "")
                if timestamp:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
                        text_parts.append(dt.strftime("%Y-%m-%d"))
                    except (ValueError, TypeError):
                        text_parts.append(timestamp)
            else:
                # Recursively process any nested content
                if "content" in node:
                    nested_text = self._extract_text_from_content(
                        node["content"], strip_marks=strip_marks
                    )
                    text_parts.append(nested_text)

        return "".join(text_parts)

    def _apply_marks(self, text: str, marks: list[dict[str, Any]]) -> str:
        """
        Apply markdown formatting based on ADF text marks.

        Args:
            text: Base text
            marks: List of mark objects (strong, em, code, link, etc.)

        Returns:
            Text with markdown formatting applied
        """
        if not marks:
            return text

        for mark in reversed(marks):
            mark_type = mark.get("type", "")
            attrs = mark.get("attrs", {})

            if mark_type == "strong":
                text = f"**{text}**"
            elif mark_type == "em":
                text = f"*{text}*"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "strike":
                text = f"~~{text}~~"
            elif mark_type == "link":
                href = attrs.get("href", "")
                if href:
                    text = f"[{text}]({href})"
            elif mark_type == "underline":
                text = f"<u>{text}</u>"
            elif mark_type == "textColor":
                color = attrs.get("color", "")
                if color:
                    text = f'<span style="color: {color}">{text}</span>'

        return text

    # ============================================================================
    # Text Block Parsers
    # ============================================================================

    async def _parse_paragraph(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse paragraph node into TEXT block with PARAGRAPH sub_type."""
        content = node.get("content", [])
        text = self._extract_text_from_content(content)

        if not text.strip():
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            data=text,
            source_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_heading(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse heading node into TEXT block with HEADING sub_type."""
        content = node.get("content", [])
        text = self._extract_text_from_content(content)

        if not text.strip():
            return []

        attrs = node.get("attrs", {})
        level = attrs.get("level", 1)

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.HEADING,
            format=DataFormat.MARKDOWN,
            data=f"{'#' * level} {text}",
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_blockquote(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse blockquote node into TEXT block with QUOTE sub_type."""
        content = node.get("content", [])

        # Process child nodes recursively
        child_indices: list[BlockContainerIndex] = []
        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        # Create wrapper BlockGroup for quote
        if child_indices:
            group_index = len(block_groups)
            group = BlockGroup(
                id=str(uuid4()),
                index=group_index,
                parent_index=parent_group_index,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.QUOTE,
                children=BlockGroupChildren.from_indices(
                    block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                    block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
                ),
                source_group_id=node.get("attrs", {}).get("localId"),
                weburl=self._normalize_url(parent_page_url),
            )
            block_groups.append(group)

            # Update child blocks/groups to point to this group
            for idx in child_indices:
                if idx.block_index is not None and idx.block_index < len(blocks):
                    blocks[idx.block_index].parent_index = group_index
                if idx.block_group_index is not None and idx.block_group_index < len(block_groups):
                    block_groups[idx.block_group_index].parent_index = group_index

            return [BlockContainerIndex(block_group_index=group_index)]

        return []

    async def _parse_codeBlock(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse codeBlock node into TEXT block with CODE sub_type."""
        content = node.get("content", [])

        # Extract plain text for code (no formatting)
        code_text_parts = [
            child.get("text", "")
            for child in content
            if child.get("type") == "text"
        ]

        code_text = "".join(code_text_parts)

        if not code_text:
            return []

        attrs = node.get("attrs", {})
        language = attrs.get("language", "")

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.CODE,
            format=DataFormat.CODE,
            data=code_text,
            code_metadata=CodeMetadata(language=language) if language else None,
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_rule(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse rule (horizontal line) node into TEXT block with DIVIDER sub_type."""
        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.DIVIDER,
            format=DataFormat.MARKDOWN,
            data="---",
            source_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_inlineCode(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse inlineCode - usually not standalone, but handle if it appears."""
        text = node.get("text", "")

        if not text:
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.CODE,
            format=DataFormat.CODE,
            data=text,
            source_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    # ============================================================================
    # List Parsers
    # ============================================================================

    async def _parse_bulletList(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse bulletList node - process list items."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth + 1,
                parent_list_style="bullet",
            )
            child_indices.extend(child_result)

        return child_indices

    async def _parse_orderedList(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse orderedList node - process list items."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth + 1,
                parent_list_style="numbered",
            )
            child_indices.extend(child_result)

        return child_indices

    async def _parse_listItem(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse listItem node into TEXT block with LIST_ITEM sub_type."""
        content = node.get("content", [])

        # Extract text from direct children (paragraph nodes)
        text_parts = []
        nested_list_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_type = child_node.get("type", "")

            # Process nested lists separately
            if child_type in ["bulletList", "orderedList", "taskList"]:
                nested_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style=parent_list_style,
                )
                nested_list_indices.extend(nested_result)
            else:
                # Extract text from paragraphs and other content
                if "content" in child_node:
                    child_text = self._extract_text_from_content(child_node["content"])
                    if child_text:
                        text_parts.append(child_text)

        text = " ".join(text_parts).strip()

        if not text:
            return nested_list_indices

        # Determine list style
        list_style = parent_list_style or "bullet"

        # Create list item block
        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.LIST_ITEM,
            format=DataFormat.MARKDOWN,
            data=f"1. {text}" if list_style == "numbered" else f"- {text}",
            list_metadata=ListMetadata(
                list_style=list_style,
                indent_level=list_depth,
            ),
            source_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        result = [BlockContainerIndex(block_index=block_index)]
        result.extend(nested_list_indices)

        return result

    async def _parse_taskList(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse taskList node - process task items."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth + 1,
                parent_list_style="checkbox",
            )
            child_indices.extend(child_result)

        return child_indices

    async def _parse_taskItem(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse taskItem node into TEXT block with LIST_ITEM sub_type and checkbox."""
        content = node.get("content", [])
        text_parts = []

        for child_node in content:
            if "content" in child_node:
                child_text = self._extract_text_from_content(child_node["content"])
                if child_text:
                    text_parts.append(child_text)

        text = " ".join(text_parts).strip()

        if not text:
            return []

        attrs = node.get("attrs", {})
        state = attrs.get("state", "TODO")
        checkbox = "[x]" if state == "DONE" else "[ ]"

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.LIST_ITEM,
            format=DataFormat.MARKDOWN,
            data=f"- {checkbox} {text}",
            list_metadata=ListMetadata(
                list_style="checkbox",
                indent_level=list_depth,
            ),
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    # ============================================================================
    # Media Parsers
    # ============================================================================

    async def _parse_media(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """
        Parse media node into IMAGE block with base64 data.

        Only creates blocks for images that are successfully fetched.
        Non-image media (PDFs, etc.) are handled as attachments via ChildRecords.
        """
        attrs = node.get("attrs", {})
        media_id = attrs.get("id", "")
        alt_text = attrs.get("alt", "")
        filename = attrs.get("__fileName", "") or alt_text
        width = attrs.get("width")
        height = attrs.get("height")

        if not media_id and not filename:
            return []

        # Fetch media as base64 if fetcher provided
        base64_data_url = None
        if media_fetcher:
            try:
                base64_data_url = await media_fetcher(media_id, filename or alt_text)
            except Exception as e:
                self.logger.warning(f"Failed to fetch media {media_id}/{filename}: {e}")

        # Only create IMAGE block if we successfully fetched the media as base64
        # The media_fetcher will return None for non-image types or fetch failures
        if not base64_data_url:
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.IMAGE,
            format=DataFormat.BASE64,
            data={"uri": base64_data_url},
            image_metadata=ImageMetadata(
                alt_text=alt_text,
                image_size={"width": width, "height": height} if width and height else None,
            ),
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_mediaSingle(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse mediaSingle wrapper - extract and process media child."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        return child_indices

    async def _parse_mediaGroup(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse mediaGroup wrapper - extract and process media children."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        return child_indices

    # ============================================================================
    # Table Parsers
    # ============================================================================

    async def _parse_table(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse table node into TABLE BlockGroup with TABLE_ROW blocks."""
        content = node.get("content", [])

        # Create table group first
        group_index = len(block_groups)
        table_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TABLE,
            table_metadata=TableMetadata(
                num_of_rows=0,  # Will be updated after processing rows
                num_of_cols=0,
                has_header=False,  # ADF doesn't indicate headers explicitly
            ),
            format=DataFormat.JSON,
            data={
                "table_summary": "",  # Will be filled during indexing with LLM
                "column_headers": [],  # Will be extracted from first row
                "table_markdown": "",  # Will be generated after processing rows
            },
            source_group_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(table_group)

        # Process table rows
        row_indices: list[int] = []
        num_cols = 0
        table_markdown_lines: list[str] = []
        column_headers: list[str] = []

        for row_idx, row_node in enumerate(content):
            if row_node.get("type") == "tableRow":
                row_block_index = await self._parse_tableRow(
                    node=row_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                )
                if row_block_index is not None:
                    row_indices.append(row_block_index)

                    # Get the cell texts from the created block
                    row_block = blocks[row_block_index]
                    if isinstance(row_block.data, dict):
                        cell_texts = row_block.data.get("cells", [])

                        # Extract column headers from first row
                        if row_idx == 0:
                            column_headers = cell_texts
                            num_cols = len(cell_texts)
                            # Header row
                            escaped_headers = [col.replace("|", "\\|") for col in cell_texts]
                            table_markdown_lines.append("| " + " | ".join(escaped_headers) + " |")
                            table_markdown_lines.append("|" + "|".join([" --- " for _ in cell_texts]) + "|")
                        else:
                            # Data row
                            escaped_cells = [cell.replace("|", "\\|")[:200] for cell in cell_texts]
                            table_markdown_lines.append("| " + " | ".join(escaped_cells) + " |")

        # Generate table markdown
        table_markdown = "\n".join(table_markdown_lines) if table_markdown_lines else ""

        # Update table metadata and data
        if table_group.table_metadata:
            table_group.table_metadata.num_of_rows = len(row_indices)
            table_group.table_metadata.num_of_cols = num_cols
            table_group.table_metadata.num_of_cells = len(row_indices) * num_cols
            table_group.table_metadata.column_names = column_headers

        if isinstance(table_group.data, dict):
            table_group.data["column_headers"] = column_headers
            table_group.data["table_markdown"] = table_markdown

        # Update table group children
        if row_indices:
            table_group.children = BlockGroupChildren.from_indices(
                block_indices=row_indices,
                block_group_indices=[],
            )

        return [BlockContainerIndex(block_group_index=group_index)]

    async def _parse_tableRow(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
    ) -> int | None:
        """
        Parse tableRow node into TABLE_ROW block.

        Returns the block index of the created row block.
        """
        content = node.get("content", [])

        # Extract cell data
        cells_data = []
        cell_texts = []

        for cell_node in content:
            cell_type = cell_node.get("type", "")
            if cell_type in ["tableCell", "tableHeader"]:
                # Extract text from cell content (strip formatting for tables)
                cell_content = cell_node.get("content", [])
                cell_text_parts = []

                for cell_child in cell_content:
                    cell_text = self._extract_text_from_content(
                        cell_child.get("content", []) if "content" in cell_child else [cell_child],
                        strip_marks=True
                    )
                    if cell_text:
                        cell_text_parts.append(cell_text)

                cell_text = " ".join(cell_text_parts).strip()
                cell_texts.append(cell_text)
                cells_data.append({
                    "text": cell_text,
                    "is_header": cell_type == "tableHeader",
                })

        if not cells_data:
            return None

        # Create TABLE_ROW block with proper data format
        block_index = len(blocks)

        # Use zero-width space as delimiter (same as Notion)
        delimiter = "\u200B|\u200B"
        row_natural_language_text = delimiter.join(cell_texts)

        # Create row data dictionary (matching expected format)
        row_data = {
            "row_natural_language_text": row_natural_language_text,
            "row_number": 0,  # Will be set in post-processing
            "row": json.dumps({"cells": cell_texts}),
            "cells": cell_texts,
        }

        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data=row_data,
            table_row_metadata=TableRowMetadata(
                row_number=0,  # Will be set in post-processing
                is_header=any(cell["is_header"] for cell in cells_data),
            ),
            source_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return block_index

    # ============================================================================
    # Structural Block Group Parsers
    # ============================================================================

    async def _parse_panel(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse panel node into BlockGroup with CALLOUT sub_type."""
        attrs = node.get("attrs", {})
        panel_type = attrs.get("panelType", "info")
        content = node.get("content", [])

        # Create panel group
        group_index = len(block_groups)

        # Process child nodes
        child_indices: list[BlockContainerIndex] = []
        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        if not child_indices:
            return []

        panel_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CALLOUT,
            name=panel_type.upper(),
            description=f"{panel_type} panel",
            children=BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            ),
            source_group_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(panel_group)

        # Update children to point to this group
        for idx in child_indices:
            if idx.block_index is not None and idx.block_index < len(blocks):
                blocks[idx.block_index].parent_index = group_index
            if idx.block_group_index is not None and idx.block_group_index < len(block_groups):
                block_groups[idx.block_group_index].parent_index = group_index

        return [BlockContainerIndex(block_group_index=group_index)]

    async def _parse_expand(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse expand (collapsible) node into BlockGroup with TOGGLE sub_type."""
        attrs = node.get("attrs", {})
        title = attrs.get("title", "Details")
        content = node.get("content", [])

        # Create expand group
        group_index = len(block_groups)

        # Process child nodes
        child_indices: list[BlockContainerIndex] = []
        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        if not child_indices:
            return []

        expand_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.TOGGLE,
            name=title,
            description=f"Expandable section: {title}",
            children=BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            ),
            source_group_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(expand_group)

        # Update children to point to this group
        for idx in child_indices:
            if idx.block_index is not None and idx.block_index < len(blocks):
                blocks[idx.block_index].parent_index = group_index
            if idx.block_group_index is not None and idx.block_group_index < len(block_groups):
                block_groups[idx.block_group_index].parent_index = group_index

        return [BlockContainerIndex(block_group_index=group_index)]

    async def _parse_nestedExpand(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse nestedExpand - similar to expand."""
        return await self._parse_expand(
            node, blocks, block_groups, parent_group_index,
            media_fetcher, parent_page_url, page_id, list_depth, parent_list_style
        )

    # ============================================================================
    # Layout Parsers
    # ============================================================================

    async def _parse_layoutSection(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse layoutSection node - process columns."""
        content = node.get("content", [])

        # Create column list group
        group_index = len(block_groups)
        column_list_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.COLUMN_LIST,
            source_group_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(column_list_group)

        # Process each column
        column_group_indices: list[int] = []
        for child_node in content:
            if child_node.get("type") == "layoutColumn":
                column_result = await self._parse_layoutColumn(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style=parent_list_style,
                )
                column_group_indices.extend(
                    idx.block_group_index
                    for idx in column_result
                    if idx.block_group_index is not None
                )

        # Update column list group children
        if column_group_indices:
            column_list_group.children = BlockGroupChildren.from_indices(
                block_indices=[],
                block_group_indices=column_group_indices,
            )

        return [BlockContainerIndex(block_group_index=group_index)]

    async def _parse_layoutColumn(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse layoutColumn node into COLUMN BlockGroup."""
        content = node.get("content", [])

        # Create column group
        group_index = len(block_groups)
        column_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.COLUMN,
            source_group_id=node.get("attrs", {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(column_group)

        # Process child nodes
        child_indices: list[BlockContainerIndex] = []
        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth,
                parent_list_style=parent_list_style,
            )
            child_indices.extend(child_result)

        # Update column group children
        if child_indices:
            column_group.children = BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            )

        return [BlockContainerIndex(block_group_index=group_index)]

    # ============================================================================
    # Extension and Other Parsers
    # ============================================================================

    async def _parse_extension(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse extension node - handle nested ADF or show extension info."""
        attrs = node.get("attrs", {})
        extension_type = attrs.get("extensionType", "")
        extension_key = attrs.get("extensionKey", "")
        parameters = attrs.get("parameters", {})

        # Handle nested ADF in extension parameters
        nested_adf_str = parameters.get("adf")
        if nested_adf_str:
            try:
                nested_adf = json.loads(nested_adf_str) if isinstance(nested_adf_str, str) else nested_adf_str
                # Recursively parse nested ADF
                if "content" in nested_adf:
                    child_indices: list[BlockContainerIndex] = []
                    for nested_node in nested_adf["content"]:
                        nested_result = await self._process_node_recursive(
                            node=nested_node,
                            blocks=blocks,
                            block_groups=block_groups,
                            parent_group_index=parent_group_index,
                            media_fetcher=media_fetcher,
                            parent_page_url=parent_page_url,
                            page_id=page_id,
                            list_depth=list_depth,
                            parent_list_style=parent_list_style,
                        )
                        child_indices.extend(nested_result)
                    return child_indices
            except Exception as e:
                self.logger.debug(f"Failed to parse nested ADF in extension: {e}")

        # Fallback: process content if available
        content = node.get("content", [])
        if content:
            child_indices: list[BlockContainerIndex] = []
            for child_node in content:
                child_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style=parent_list_style,
                )
                child_indices.extend(child_result)
            return child_indices

        # Show extension type as placeholder block
        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.TXT,
            data=f"[Extension: {extension_key or extension_type}]",
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_placeholder(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse placeholder node - show placeholder text."""
        attrs = node.get("attrs", {})
        placeholder_text = attrs.get("text", "")

        if not placeholder_text:
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.TXT,
            data=placeholder_text,
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_inlineCard(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse inlineCard node into TEXT block with LINK sub_type."""
        attrs = node.get("attrs", {})
        url = attrs.get("url", "")

        if not url:
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.LINK,
            format=DataFormat.MARKDOWN,
            data=f"[{url}]({url})",
            link_metadata=LinkMetadata(
                link_text=url,
                link_url=self._normalize_url(url),
                link_type="external",
            ),
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    # ============================================================================
    # Additional Node Type Parsers
    # ============================================================================

    async def _parse_text(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """
        Parse text node - usually not standalone, but handle if it appears at root level.
        """
        text = node.get("text", "")
        marks = node.get("marks", [])

        if not text.strip():
            return []

        formatted_text = self._apply_marks(text, marks)

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            data=formatted_text,
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_hardBreak(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse hardBreak - skip as line breaks are handled within text extraction."""
        return []

    async def _parse_mention(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse mention node - usually not standalone."""
        attrs = node.get("attrs", {})
        mention_text = attrs.get("text", attrs.get("id", "mention"))

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.TXT,
            data=f"@{mention_text}",
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_emoji(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse emoji node - usually not standalone."""
        return []

    async def _parse_status(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse status node - usually not standalone."""
        return []

    async def _parse_date(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse date node - usually not standalone."""
        return []

    async def _parse_tableCell(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse tableCell - handled by tableRow parser."""
        return []

    async def _parse_tableHeader(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse tableHeader - handled by tableRow parser."""
        return []

    async def _parse_decisionList(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse decisionList node - process decision items."""
        content = node.get("content", [])
        child_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_result = await self._process_node_recursive(
                node=child_node,
                blocks=blocks,
                block_groups=block_groups,
                parent_group_index=parent_group_index,
                media_fetcher=media_fetcher,
                parent_page_url=parent_page_url,
                page_id=page_id,
                list_depth=list_depth + 1,
                parent_list_style="decision",
            )
            child_indices.extend(child_result)

        return child_indices

    async def _parse_decisionItem(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """Parse decisionItem node into TEXT block with LIST_ITEM sub_type."""
        attrs = node.get("attrs", {})
        state = attrs.get("state", "DECIDED")
        content = node.get("content", [])

        text_parts = []
        for child_node in content:
            if "content" in child_node:
                child_text = self._extract_text_from_content(child_node["content"])
                if child_text:
                    text_parts.append(child_text)

        text = " ".join(text_parts).strip()

        if not text:
            return []

        marker = "✓" if state == "DECIDED" else "◇"

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.LIST_ITEM,
            format=DataFormat.MARKDOWN,
            data=f"{marker} {text}",
            list_metadata=ListMetadata(
                list_style="dash",
                indent_level=list_depth,
            ),
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    # ============================================================================
    # Fallback Parser
    # ============================================================================

    async def _parse_unknown(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
        list_depth: int = 0,
        parent_list_style: str | None = None,
    ) -> list[BlockContainerIndex]:
        """
        Fallback parser for unknown ADF node types.

        Attempts to extract any text content from the node.
        """
        node_type = node.get("type", "unknown")
        self.logger.debug(f"Unknown ADF node type: {node_type}")

        # Try to process children if available
        content = node.get("content", [])
        if content:
            child_indices: list[BlockContainerIndex] = []
            for child_node in content:
                child_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style=parent_list_style,
                )
                child_indices.extend(child_result)
            return child_indices

        return []

    # ============================================================================
    # Comment Parsing Methods
    # ============================================================================

    async def attach_inline_comments_to_blocks(
        self,
        blocks: list[Block],
        inline_comments: list[dict[str, Any]],
        media_fetcher: Callable[[str, str], Awaitable[str | None]] | None = None,
    ) -> None:
        """
        Attach inline comments to their target blocks based on quoted text.

        Args:
            blocks: List of blocks to attach comments to
            inline_comments: List of inline comment dictionaries from API
            media_fetcher: Optional media fetcher for comment attachments
        """
        if not inline_comments:
            return

        # Group comments by thread
        from collections import defaultdict
        comments_by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for comment in inline_comments:
            comment_id = str(comment.get("id", ""))
            parent_comment_id = comment.get("parentCommentId")
            thread_id = str(parent_comment_id) if parent_comment_id else comment_id
            comments_by_thread[thread_id].append(comment)

        # Sort each thread by creation time
        for thread_id in comments_by_thread:
            comments_by_thread[thread_id].sort(
                key=lambda c: c.get("createdAt", ""),
            )

        # Process each thread
        for thread_id, thread_comments in comments_by_thread.items():
            if not thread_comments:
                continue

            # Get quoted text from first comment (the one that was placed on the content)
            first_comment = thread_comments[0]
            properties = first_comment.get("properties", {})
            quoted_text = properties.get("inlineOriginalSelection", "")

            # Find target block containing the quoted text
            target_block = self._find_block_by_text(blocks, quoted_text)

            if target_block:
                # Parse all comments in thread to BlockComment objects
                thread_block_comments = []
                for comment in thread_comments:
                    block_comment = await self._parse_confluence_comment_to_block_comment(
                        comment,
                        quoted_text=quoted_text if comment == first_comment else None,
                        media_fetcher=media_fetcher,
                    )
                    if block_comment:
                        thread_block_comments.append(block_comment)

                # Add thread to block's comments (2D list structure)
                if thread_block_comments:
                    target_block.comments.append(thread_block_comments)
            else:
                self.logger.debug(f"Could not find target block for inline comment thread {thread_id}")

    def _find_block_by_text(
        self,
        blocks: list[Block],
        quoted_text: str
    ) -> Block | None:
        """
        Find a block that contains the quoted text.

        Args:
            blocks: List of blocks to search
            quoted_text: Text to search for

        Returns:
            Block containing the text, or None if not found
        """
        if not quoted_text or not quoted_text.strip():
            return None

        quoted_text_normalized = quoted_text.strip().lower()

        for block in blocks:
            if block.type == BlockType.TEXT and block.data:
                # Extract plain text from markdown for matching
                block_text = str(block.data)
                # Remove markdown formatting for better matching
                block_text_clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', block_text)  # Remove links
                block_text_clean = re.sub(r'[*_~`#>]', '', block_text_clean)  # Remove formatting
                block_text_clean = block_text_clean.strip().lower()

                if quoted_text_normalized in block_text_clean:
                    return block

        return None

    async def _parse_confluence_comment_to_block_comment(
        self,
        comment: dict[str, Any],
        quoted_text: str | None = None,
        media_fetcher: Callable | None = None,
    ) -> BlockComment | None:
        """
        Parse Confluence comment data into BlockComment object.

        Args:
            comment: Raw comment data from Confluence API
            quoted_text: The text that was commented on (for inline comments)
            media_fetcher: Optional media fetcher for attachments

        Returns:
            BlockComment object or None if parsing fails
        """
        try:
            comment_id = str(comment.get("id", ""))
            if not comment_id:
                return None

            # Extract comment body from ADF
            body = comment.get("body", {})
            atlas_doc = body.get("atlas_doc_format", {})
            adf_value = atlas_doc.get("value")

            if not adf_value:
                return None

            # Parse ADF to text
            if isinstance(adf_value, str):
                adf_dict = json.loads(adf_value)
            else:
                adf_dict = adf_value

            # Convert comment ADF to markdown
            comment_text = self._adf_to_markdown_simple(adf_dict)

            # Extract author info
            version = comment.get("version", {})
            author_id = version.get("authorId", "")

            # Parse timestamps
            created_at = self._parse_confluence_timestamp(comment.get("createdAt"))

            # Extract weburl
            links = comment.get("_links", {})
            comment_weburl = links.get("webui")

            # Resolution status
            resolution_status = comment.get("resolutionStatus", "open")

            return BlockComment(
                text=comment_text or "",
                format=DataFormat.MARKDOWN,
                author_id=author_id,
                thread_id=comment_id,
                resolution_status=resolution_status,
                weburl=self._normalize_url(comment_weburl),
                created_at=created_at,
                quoted_text=quoted_text,
            )

        except Exception as e:
            self.logger.warning(f"Failed to parse Confluence comment: {e}")
            return None

    def _adf_to_markdown_simple(self, adf_content: dict[str, Any]) -> str:
        """
        Convert ADF to simple markdown text (for comments).

        This is a simplified version that extracts text without creating blocks.

        Args:
            adf_content: ADF document

        Returns:
            Markdown text
        """
        if not adf_content or not isinstance(adf_content, dict):
            return ""

        text_parts: list[str] = []

        def extract_text_recursive(node: dict[str, Any]) -> str:
            """Recursively extract text from ADF nodes."""
            if not isinstance(node, dict):
                return ""

            node_type = node.get("type", "")

            if node_type == "text":
                text = node.get("text", "")
                marks = node.get("marks", [])
                return self._apply_marks(text, marks)

            elif node_type == "paragraph":
                content = node.get("content", [])
                para_text = "".join(extract_text_recursive(child) for child in content).strip()
                return f"{para_text}\n\n" if para_text else ""

            elif node_type == "heading":
                level = node.get("attrs", {}).get("level", 1)
                content = node.get("content", [])
                heading_text = "".join(extract_text_recursive(child) for child in content).strip()
                return f"{'#' * level} {heading_text}\n\n" if heading_text else ""

            elif node_type in ["bulletList", "orderedList"]:
                content = node.get("content", [])
                is_numbered = node_type == "orderedList"
                items = []
                for i, child in enumerate(content, 1):
                    item_text = extract_text_recursive(child).strip()
                    if item_text:
                        prefix = f"{i}. " if is_numbered else "- "
                        items.append(f"{prefix}{item_text}")
                return "\n".join(items) + "\n\n" if items else ""

            elif node_type == "listItem":
                content = node.get("content", [])
                return "".join(extract_text_recursive(child) for child in content).strip()

            elif node_type == "codeBlock":
                content = node.get("content", [])
                code_text = "".join(child.get("text", "") for child in content if child.get("type") == "text")
                language = node.get("attrs", {}).get("language", "")
                return f"```{language}\n{code_text}\n```\n\n" if code_text else ""

            elif node_type == "hardBreak":
                return "\n"

            elif node_type == "rule":
                return "---\n\n"

            elif "content" in node:
                content = node.get("content", [])
                return "".join(extract_text_recursive(child) for child in content)

            return ""

        if "content" in adf_content:
            text_parts.extend(
                extract_text_recursive(node) for node in adf_content["content"]
            )

        return "".join(text_parts).strip()

    def _parse_confluence_timestamp(self, timestamp_str: str | None) -> datetime | None:
        """
        Parse Confluence timestamp string to datetime.

        Args:
            timestamp_str: ISO timestamp string from Confluence API

        Returns:
            datetime object or None if parsing fails
        """
        if not timestamp_str:
            return None
        try:
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception:
            return None

    # ============================================================================
    # Post-Processing Methods
    # ============================================================================

    def post_process_blocks(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup]
    ) -> None:
        """
        Post-process blocks and block groups.

        - Finalize indices
        - Update table row metadata
        - Fix numbered list numbering
        - Group consecutive list items into BlockGroups

        Args:
            blocks: List of blocks (modified in-place)
            block_groups: List of block groups (modified in-place)
        """
        # Finalize indices and table metadata
        self._finalize_indices_and_metadata(blocks, block_groups)

        # Fix numbered list numbering
        self._fix_numbered_list_numbering(blocks)

        # Group consecutive list items into BlockGroups
        self._group_list_items(blocks, block_groups)

    def _finalize_indices_and_metadata(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup]
    ) -> None:
        """
        Finalize indices and update table row metadata.

        Args:
            blocks: List of blocks
            block_groups: List of block groups
        """
        # Update final indices
        for i, block in enumerate(blocks):
            block.index = i

        for i, group in enumerate(block_groups):
            group.index = i

        # Update table row metadata
        table_row_counts: dict[int, int] = {}
        table_header_flags: dict[int, bool] = {}

        # First pass: identify tables with headers
        for group in block_groups:
            if (
                group.type == GroupType.TABLE
                and group.table_metadata
                and group.table_metadata.has_header
            ):
                table_header_flags[group.index] = True

        # Second pass: update row numbers
        for block in blocks:
            if block.type == BlockType.TABLE_ROW and block.parent_index is not None:
                table_index = block.parent_index

                if table_index not in table_row_counts:
                    table_row_counts[table_index] = 0

                if block.table_row_metadata:
                    table_row_counts[table_index] += 1
                    block.table_row_metadata.row_number = table_row_counts[table_index]

                    # Set header flag if first row and table has headers
                    if table_row_counts[table_index] == 1 and table_header_flags.get(table_index, False):
                        block.table_row_metadata.is_header = True

    def _fix_numbered_list_numbering(self, blocks: list[Block]) -> None:
        """
        Fix numbered list item numbering.

        Args:
            blocks: List of blocks (modified in-place)
        """
        counters: dict[int, int] = {}
        previous_indent: int | None = None

        for block in blocks:
            if block.list_metadata and block.list_metadata.list_style == "numbered":
                indent_level = block.list_metadata.indent_level or 0

                if indent_level in counters and previous_indent is not None:
                    counters[indent_level] += 1
                else:
                    counters[indent_level] = 1

                # Replace "1." with correct number
                current_number = counters[indent_level]
                if isinstance(block.data, str):
                    block.data = re.sub(r'^1\.\s*', f'{current_number}. ', block.data, count=1)

                previous_indent = indent_level
            else:
                previous_indent = None

    def _group_list_items(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup]
    ) -> None:
        """
        Group consecutive list items into BlockGroups.

        Args:
            blocks: List of blocks (modified in-place)
            block_groups: List of block groups (modified in-place)
        """
        if not blocks:
            return

        current_group_start: int | None = None
        current_indent: int | None = None
        current_list_style: str | None = None
        group_blocks: list[int] = []

        for i, block in enumerate(blocks):
            if block.list_metadata:
                list_style = block.list_metadata.list_style
                indent_level = block.list_metadata.indent_level or 0

                # Check if this continues the current group
                if (current_group_start is not None and
                    current_indent == indent_level and
                    current_list_style == list_style):
                    group_blocks.append(i)
                else:
                    # Finish previous group
                    if current_group_start is not None and group_blocks:
                        self._create_list_group(blocks, block_groups, group_blocks, current_list_style)

                    # Start new group
                    current_group_start = i
                    current_indent = indent_level
                    current_list_style = list_style
                    group_blocks = [i]
            else:
                # Finish current group
                if current_group_start is not None and group_blocks:
                    self._create_list_group(blocks, block_groups, group_blocks, current_list_style)
                    current_group_start = None
                    current_indent = None
                    current_list_style = None
                    group_blocks = []

        # Finish last group
        if current_group_start is not None and group_blocks:
            self._create_list_group(blocks, block_groups, group_blocks, current_list_style)

    def _create_list_group(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup],
        group_block_indices: list[int],
        list_style: str
    ) -> None:
        """Create a BlockGroup for a sequence of list items."""
        if len(group_block_indices) < 1:
            return

        group_index = len(block_groups)
        group_children = BlockGroupChildren()
        for idx in group_block_indices:
            group_children.add_block_index(idx)

        group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            type=GroupType.ORDERED_LIST if list_style == "numbered" else GroupType.LIST,
            children=group_children,
            list_metadata=blocks[group_block_indices[0]].list_metadata,
        )
        block_groups.append(group)

        # Update blocks to point to the group
        for idx in group_block_indices:
            blocks[idx].parent_index = group_index

    # ============================================================================
    # Helper Methods for Footer Comments
    # ============================================================================

    def create_comment_group(
        self,
        block_comment: BlockComment,
        group_index: int,
        parent_group_index: int | None,
        source_id: str,
        children_records: list[ChildRecord] | None = None,
    ) -> BlockGroup:
        """
        Create a COMMENT BlockGroup from a BlockComment object.

        Args:
            block_comment: BlockComment object with comment data
            group_index: Index for the new BlockGroup
            parent_group_index: Index of parent BlockGroup (COMMENT_THREAD)
            source_id: Confluence comment ID
            children_records: Optional list of ChildRecord for attachments

        Returns:
            BlockGroup object with type TEXT_SECTION and sub_type COMMENT
        """
        comment_data = block_comment.text

        return BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT,
            source_group_id=source_id,
            name=block_comment.author_name or "Comment",
            data=comment_data,
            format=block_comment.format,
            description=f"Comment by {block_comment.author_name or 'Unknown'}",
            children_records=children_records,
            weburl=block_comment.weburl,
        )

    def create_comment_thread_group(
        self,
        thread_id: str,
        group_index: int,
        comment_type: str,
        page_title: str,
        weburl: str | None = None,
    ) -> BlockGroup:
        """
        Create a COMMENT_THREAD BlockGroup for footer comments.

        Args:
            thread_id: Thread identifier
            group_index: Index for the new BlockGroup
            comment_type: "footer" or "inline"
            page_title: Title of the parent page
            weburl: Page URL

        Returns:
            BlockGroup object with type TEXT_SECTION and sub_type COMMENT_THREAD
        """
        return BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=0,  # Points to description/content group
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT_THREAD,
            source_group_id=f"thread_{comment_type}_{thread_id}",
            name=f"{comment_type.capitalize()} Comment Thread",
            description=f"{comment_type.capitalize()} comment thread for page {page_title}",
            weburl=self._normalize_url(weburl),
        )
