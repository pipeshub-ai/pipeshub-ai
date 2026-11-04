"""
Confluence Block Parser

Converts Atlassian Document Format (ADF) nodes into Pipeshub Block and BlockGroup objects.
This parser is designed to be extensible - new ADF node types can be easily added.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.connectors.sources.atlassian.core.adf_markdown import (
    adf_to_markdown,
    apply_adf_marks,
    media_reference,
)
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
    # Macros that only arrange or link the page; they carry no content of their own.
    LAYOUT_ONLY_EXTENSIONS = frozenset({"toc", "children", "anchor"})

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize the parser with a logger."""
        self.logger = logger
        self._block_counter = 0
        self._block_group_counter = 0
        # Inline comment marker ref (ADF annotation mark id) -> block holding the marked text
        self._annotation_blocks: dict[str, Block] = {}

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

    @staticmethod
    def _resolve_comment_weburl(
        links: dict[str, Any] | None,
        parent_page_url: str | None = None,
    ) -> str | None:
        """
        Build an absolute comment URL from Confluence v2 _links.

        Per Confluence Cloud REST API v2, comment _links.webui is a site-relative path.
        Join with _links.base, derive from _links.self, or fall back to the parent page URL.
        """
        if not links:
            return None

        web_path = links.get("webui")
        if not web_path or not str(web_path).strip():
            return None

        web_path = str(web_path).strip()
        if web_path.startswith("http"):
            return web_path

        base_url = links.get("base")
        if base_url:
            return f"{base_url}{web_path}"

        self_link = links.get("self")
        if self_link and "https://" in self_link and "/wiki/" in self_link:
            extracted_base_url = self_link.split("/wiki/")[0] + "/wiki"
            return f"{extracted_base_url}{web_path}"

        if parent_page_url and parent_page_url.startswith("http"):
            parsed = urlparse(parent_page_url)
            page_base = f"{parsed.scheme}://{parsed.netloc}/wiki"
            return f"{page_base}{web_path}"

        return None

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
        page_title: str | None = None,
    ) -> tuple[list[Block], list[BlockGroup]]:
        """
        Parse ADF content into blocks and block groups.

        Args:
            adf_content: ADF document (dict with 'content' array)
            media_fetcher: Async callback that fetches media as base64 data URI
            parent_page_url: URL of the parent page
            page_id: ID of the parent page
            page_title: Page title prepended as H1 before body (ADF omits title)

        Returns:
            Tuple of (blocks, block_groups)
        """
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []

        # Reset counters
        self._block_counter = 0
        self._block_group_counter = 0

        # Title must be block 0 before parsing so table row indices and children stay aligned
        if page_title and str(page_title).strip() and page_id:
            blocks.append(
                self.create_title_block(
                    str(page_title).strip(),
                    page_id,
                    parent_page_url,
                )
            )

        if not adf_content or not isinstance(adf_content, dict):
            return blocks, block_groups

        # Parse root content nodes
        content_nodes = adf_content.get("content") or []

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

        first_new_block = len(blocks)
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
            if len(blocks) > first_new_block:
                self._claim_annotations(node, blocks[first_new_block])

            return result if result else []

        except Exception as e:
            self.logger.warning(
                f"Error parsing ADF node type '{node_type}': {e}",
                exc_info=True
            )
            return []

    def _claim_annotations(self, node: dict[str, Any], block: Block) -> None:
        """Map inline comment annotation marks under ``node`` to ``block``.

        Nodes are claimed innermost first, so a mark maps to the block built from
        its own paragraph rather than to an enclosing list, panel or table.
        """
        stack: list[Any] = [node]
        while stack:
            current = stack.pop()
            if not isinstance(current, dict):
                continue
            for mark in current.get("marks") or []:
                if not isinstance(mark, dict) or mark.get("type") != "annotation":
                    continue
                attrs = mark.get("attrs") or {}
                if attrs.get("id") and attrs.get("annotationType", "inlineComment") == "inlineComment":
                    self._annotation_blocks.setdefault(str(attrs["id"]), block)
            stack.extend(current.get("content") or [])

    @staticmethod
    def _mention_text(attrs: dict[str, Any]) -> str:
        """Mention display text; never the account id, which is not useful indexed text."""
        text = str(attrs.get("text") or "").strip()
        if not text:
            return ""
        return text if text.startswith("@") else f"@{text}"

    @staticmethod
    def _card_link(attrs: dict[str, Any]) -> tuple[str, str]:
        """(text, url) of an inline/block/embed card; the text is the card's name when it has one."""
        url = str(attrs.get("url") or "").strip()
        data = attrs.get("data")
        title = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
        return title or url, url

    def _extension_text(self, attrs: dict[str, Any]) -> str:
        """Text for a macro with no body: its title and parameter values, if any."""
        extension_key = str(attrs.get("extensionKey") or "")
        if extension_key.lower() in self.LAYOUT_ONLY_EXTENSIONS:
            return ""
        parameters = attrs.get("parameters") or {}
        if not isinstance(parameters, dict):
            parameters = {}
        metadata = parameters.get("macroMetadata") or {}
        title = str(metadata.get("title") or "").strip() if isinstance(metadata, dict) else ""
        values: list[str] = []
        macro_params = parameters.get("macroParams") or {}
        if isinstance(macro_params, dict):
            for name, param in macro_params.items():
                value = param.get("value") if isinstance(param, dict) else None
                # "_"-prefixed params are Confluence bookkeeping (e.g. _parentId), not content.
                if value in (None, "") or str(name).startswith("_"):
                    continue
                values.append(f"{name}: {value}" if name else str(value))
        label = title or f"Extension: {extension_key or attrs.get('extensionType', '')}"
        return f"[{label}] " + "; ".join(values) if values else f"[{label}]"

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
        block_part_indices: set[int] = set()
        for node in content:
            if not isinstance(node, dict):
                continue
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
                text_parts.append(self._mention_text(node.get("attrs") or {}))
            elif node_type == "emoji":
                attrs = node.get("attrs") or {}
                # shortName already carries its colons (":smile:"); prefer the rendered glyph.
                short_name = str(attrs.get("shortName") or "").strip(":")
                text_parts.append(attrs.get("text") or (f":{short_name}:" if short_name else ""))
            elif node_type in ("inlineCard", "blockCard", "embedCard"):
                link_text, url = self._card_link(node.get("attrs") or {})
                if url:
                    text_parts.append(f"[{link_text}]({url})")
            elif node_type in ("inlineExtension", "extension"):
                text_parts.append(self._extension_text(node.get("attrs") or {}))
            elif node_type in ("media", "mediaInline"):
                text_parts.append(media_reference(node.get("attrs") or {}))
            elif node_type == "status":
                attrs = node.get("attrs") or {}
                status_text = attrs.get("text", "")
                if status_text:
                    text_parts.append(f"[{status_text}]")
            elif node_type == "date":
                attrs = node.get("attrs") or {}
                timestamp = attrs.get("timestamp", "")
                if timestamp:
                    try:
                        dt = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
                        text_parts.append(dt.strftime("%Y-%m-%d"))
                    except (ValueError, TypeError, OverflowError, OSError):
                        text_parts.append(str(timestamp))
            else:
                # Recursively process any nested content
                if "content" in node:
                    nested_text = self._extract_text_from_content(
                        node["content"], strip_marks=strip_marks
                    )
                    block_part_indices.add(len(text_parts))
                    text_parts.append(nested_text)

        # Inline runs join as-is; a block-level child (paragraph, cell, ...) is
        # space-separated from its neighbours so their words are not glued together.
        joined = ""
        prev_is_block = False
        for i, part in enumerate(text_parts):
            if not part:
                continue
            is_block = i in block_part_indices
            if (
                joined
                and (is_block or prev_is_block)
                and not joined[-1].isspace()
                and not part[0].isspace()
            ):
                joined += " "
            joined += part
            prev_is_block = is_block
        return joined

    def _is_inline_only_content(self, content: list[dict[str, Any]]) -> bool:
        """
        Check if content contains only inline ADF nodes (text, mention, emoji, etc.).
        
        Returns False if content contains block-level nodes like tables, lists, panels.
        """
        INLINE_NODE_TYPES = {
            "text", "mention", "emoji", "date", "status", 
            "inlineCard", "hardBreak", "mediaInline"
        }
        
        for node in content:
            if not isinstance(node, dict):
                continue
                
            node_type = node.get("type", "")
            
            # Paragraph is only inline if all its children are inline
            if node_type == "paragraph":
                child_content = node.get("content") or []
                if not self._is_inline_only_content(child_content):
                    return False
            # Any non-inline node type means we need full parsing
            elif node_type not in INLINE_NODE_TYPES:
                return False
        
        return True

    def _extract_text_from_blocks(
        self, 
        blocks: list[Block], 
        indices: list[BlockContainerIndex]
    ) -> str:
        """
        Extract searchable text from a list of block container indices.
        Used for nested cell content where blocks have been created.
        """
        text_parts = []
        for idx in indices:
            if idx.block_index is not None and idx.block_index < len(blocks):
                block = blocks[idx.block_index]
                if isinstance(block.data, str):
                    text_parts.append(block.data)
                elif isinstance(block.data, dict):
                    # Extract text from common data fields
                    text = block.data.get("text") or block.data.get("row_natural_language_text") or ""
                    if text:
                        text_parts.append(text)
        
        return " ".join(text_parts).strip()

    def _apply_marks(self, text: str, marks: list[dict[str, Any]]) -> str:
        """Apply markdown formatting based on ADF text marks (see ``apply_adf_marks``)."""
        return apply_adf_marks(text, marks)

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
        content = node.get("content") or []
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
            source_id=(node.get("attrs") or {}).get("localId"),
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
        content = node.get("content") or []
        text = self._extract_text_from_content(content)

        if not text.strip():
            return []

        attrs = node.get("attrs") or {}
        try:
            level = min(max(int(attrs.get("level") or 1), 1), 6)
        except (TypeError, ValueError, OverflowError):
            level = 1

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
        content = node.get("content") or []

        # Create wrapper BlockGroup for quote first and append before processing children
        group_index = len(block_groups)
        group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.QUOTE,
            source_group_id=(node.get("attrs") or {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(group)

        # Process child nodes recursively
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

        # Update children after processing
        if child_indices:
            group.children = BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            )
            # Update child blocks/groups to point to this group
            for idx in child_indices:
                if idx.block_index is not None and idx.block_index < len(blocks):
                    blocks[idx.block_index].parent_index = group_index
                if idx.block_group_index is not None and idx.block_group_index < len(block_groups):
                    block_groups[idx.block_group_index].parent_index = group_index

        return [BlockContainerIndex(block_group_index=group_index)]

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
        content = node.get("content") or []

        # Extract plain text for code (no formatting)
        code_text_parts = [
            child.get("text", "")
            for child in content
            if child.get("type") == "text"
        ]

        code_text = "".join(code_text_parts)

        if not code_text:
            return []

        attrs = node.get("attrs") or {}
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
            source_id=(node.get("attrs") or {}).get("localId"),
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
        content = node.get("content") or []
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
        content = node.get("content") or []
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
        """Parse listItem node into TEXT block with LIST_ITEM sub_type.

        The item's own block comes first, then its nested lists and media, so readers
        see the parent before its children and annotations claim the item's own block.
        """
        content = node.get("content") or []
        nested_types = ("bulletList", "orderedList", "taskList", "media", "mediaSingle")

        # Extract text from direct children (paragraph nodes)
        text_parts = []
        for child_node in content:
            # TODO: tables/panels in list items still need a wrapping BlockGroup
            # Wrapped in a list so attrs-only nodes (body-less macros, cards) keep their text.
            if child_node.get("type", "") not in nested_types:
                child_text = self._extract_text_from_content([child_node])
                if child_text:
                    text_parts.append(child_text)

        text = " ".join(text_parts).strip()
        result: list[BlockContainerIndex] = []

        if text:
            list_style = parent_list_style or "bullet"
            block_index = len(blocks)
            blocks.append(Block(
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
                source_id=(node.get("attrs") or {}).get("localId"),
                weburl=self._normalize_url(parent_page_url),
            ))
            result.append(BlockContainerIndex(block_index=block_index))

        for child_node in content:
            if child_node.get("type", "") in nested_types:
                result.extend(await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style=parent_list_style,
                ))

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
        content = node.get("content") or []
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
        content = node.get("content") or []
        
        # Separate inline content from nested blocks
        inline_content: list[dict[str, Any]] = []
        nested_list_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_type = child_node.get("type", "")

            # Process nested lists separately (generic like listItem)
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
                    parent_list_style="checkbox",
                )
                nested_list_indices.extend(nested_result)
            elif child_type in ["media", "mediaSingle"]:
                # Process media separately
                media_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style="checkbox",
                )
                nested_list_indices.extend(media_result)
            elif child_type == "paragraph":
                # Paragraph wraps inline content - extract from its content array
                if "content" in child_node:
                    inline_content.extend(child_node["content"])
            else:
                # Direct inline node (text, mention, emoji, etc.) - add to inline content
                inline_content.append(child_node)

        # Extract text from all collected inline content
        text = self._extract_text_from_content(inline_content).strip()

        if not text:
            return nested_list_indices

        attrs = node.get("attrs") or {}
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

        result = [BlockContainerIndex(block_index=block_index)]
        result.extend(nested_list_indices)

        return result

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

        Images that are fetched become IMAGE blocks. Anything else (non-image files,
        failed downloads, external images) becomes a text reference; the files
        themselves are handled as attachments via ChildRecords.
        """
        attrs = node.get("attrs") or {}
        media_id = attrs.get("id", "")
        alt_text = attrs.get("alt", "")
        filename = attrs.get("__fileName", "") or alt_text
        width = attrs.get("width")
        height = attrs.get("height")

        if not media_id and not filename and not media_reference(attrs):
            return []

        # Fetch media as base64 if fetcher provided; an external image with neither has nothing to fetch.
        base64_data_url = None
        if media_fetcher and (media_id or filename):
            try:
                base64_data_url = await media_fetcher(media_id, filename or alt_text)
            except Exception as e:
                self.logger.warning(f"Failed to fetch media {media_id}/{filename}: {e}")

        # The fetcher returns None for non-image files and for failed downloads; keep a
        # text reference so the page still records what was embedded there.
        if not base64_data_url:
            reference = media_reference(attrs)
            if not reference:
                return []
            block_index = len(blocks)
            blocks.append(Block(
                id=str(uuid4()),
                index=block_index,
                parent_index=parent_group_index,
                type=BlockType.TEXT,
                sub_type=BlockSubType.PARAGRAPH,
                format=DataFormat.MARKDOWN,
                data=reference,
                source_id=attrs.get("localId"),
                weburl=self._normalize_url(parent_page_url),
            ))
            return [BlockContainerIndex(block_index=block_index)]

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
        content = node.get("content") or []
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
        content = node.get("content") or []
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
        content = node.get("content") or []

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
            source_group_id=(node.get("attrs") or {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(table_group)

        # Process table rows
        row_indices: list[int] = []
        nested_block_group_indices: list[int] = []  # Track all nested block groups
        num_cols = 0
        table_markdown_lines: list[str] = []
        column_headers: list[str] = []

        has_header = False
        for row_node in content:
            if row_node.get("type") == "tableRow":
                row_block_index, row_nested_groups = await self._parse_table_row_node(
                    node=row_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                )
                if row_block_index is not None:
                    is_first_row = not row_indices
                    self._claim_annotations(row_node, blocks[row_block_index])
                    row_indices.append(row_block_index)
                    nested_block_group_indices.extend(row_nested_groups)  # Collect nested groups

                    # Get the cell texts from the created block
                    row_block = blocks[row_block_index]
                    if isinstance(row_block.data, dict):
                        cells = row_block.data.get("cells", [])
                        
                        # Extract text from cells (handle both dict and string format for backward compatibility)
                        cell_texts = []
                        for cell in cells:
                            if isinstance(cell, dict):
                                cell_texts.append(cell.get("text", ""))
                            else:
                                cell_texts.append(str(cell))

                        escaped_cells = [cell.replace("|", "\\|") for cell in cell_texts]
                        if is_first_row:
                            num_cols = len(cell_texts)
                            details = row_block.data.get("cell_details") or []
                            has_header = bool(details) and all(
                                isinstance(d, dict) and d.get("is_header") for d in details
                            )
                            if has_header:
                                column_headers = cell_texts
                                table_markdown_lines.append("| " + " | ".join(escaped_cells) + " |")
                            else:
                                # Markdown tables need a header line; leave it blank rather than promote data.
                                table_markdown_lines.append("|" + "|".join("   " for _ in cell_texts) + "|")
                            table_markdown_lines.append("|" + "|".join([" --- " for _ in cell_texts]) + "|")
                            if has_header:
                                continue
                        table_markdown_lines.append("| " + " | ".join(escaped_cells) + " |")

        # Generate table markdown
        table_markdown = "\n".join(table_markdown_lines) if table_markdown_lines else ""

        # Update table metadata and data
        if table_group.table_metadata:
            table_group.table_metadata.num_of_rows = len(row_indices)
            table_group.table_metadata.num_of_cols = num_cols
            table_group.table_metadata.num_of_cells = len(row_indices) * num_cols
            table_group.table_metadata.column_names = column_headers
            table_group.table_metadata.has_header = has_header

        if isinstance(table_group.data, dict):
            table_group.data["column_headers"] = column_headers
            table_group.data["table_markdown"] = table_markdown

        # Update table group children with BOTH row blocks AND nested block groups
        if row_indices or nested_block_group_indices:
            table_group.children = BlockGroupChildren.from_indices(
                block_indices=row_indices,
                block_group_indices=nested_block_group_indices,
            )

        # Align nested block groups with the parent table (same as panel/callout parsing)
        for nested_gi in nested_block_group_indices:
            if 0 <= nested_gi < len(block_groups):
                block_groups[nested_gi].parent_index = group_index

        return [BlockContainerIndex(block_group_index=group_index)]

    async def _parse_table_row_node(
        self,
        node: dict[str, Any],
        blocks: list[Block],
        block_groups: list[BlockGroup],
        parent_group_index: int | None,
        media_fetcher: Callable | None,
        parent_page_url: str | None,
        page_id: str | None,
    ) -> tuple[int | None, list[int]]:
        """
        Parse tableRow ADF node into TABLE_ROW block.

        Returns:
            Tuple of (row block index, list of nested block group indices created in this row)
        """
        content = node.get("content") or []

        # Track all nested block groups created within cells of this row
        all_nested_block_group_indices: list[int] = []

        # Extract cell data
        cells_data = []
        cell_texts = []

        for cell_node in content:
            cell_type = cell_node.get("type", "")
            if cell_type in ["tableCell", "tableHeader"]:
                cell_content = cell_node.get("content") or []
                
                # Check if cell contains only inline content (fast path)
                if self._is_inline_only_content(cell_content):
                    # Extract text as string (existing logic)
                    cell_text_parts = []
                    for cell_child in cell_content:
                        cell_text = self._extract_text_from_content(
                            (cell_child.get("content") or []) if "content" in cell_child else [cell_child],
                            strip_marks=False,
                        )
                        if cell_text:
                            cell_text_parts.append(cell_text)
                    
                    cell_text = " ".join(cell_text_parts).strip()
                    cell_texts.append(cell_text)
                    cells_data.append({
                        "type": "text",
                        "text": cell_text,
                        "is_header": cell_type == "tableHeader",
                    })
                else:
                    # Cell contains block-level content - parse recursively
                    first_block, first_group = len(blocks), len(block_groups)
                    child_indices = []
                    for content_node in cell_content:
                        result = await self._process_node_recursive(
                            node=content_node,
                            blocks=blocks,
                            block_groups=block_groups,
                            parent_group_index=parent_group_index,  # Nested content points to TABLE group
                            media_fetcher=media_fetcher,
                            parent_page_url=parent_page_url,
                            page_id=page_id,
                        )
                        child_indices.extend(result)
                    
                    # Collect nested block group indices from this cell
                    nested_groups_in_cell = [idx.block_group_index for idx in child_indices 
                                             if idx.block_group_index is not None]
                    all_nested_block_group_indices.extend(nested_groups_in_cell)
                    
                    # Extract text from created blocks for search
                    cell_text = self._extract_text_from_blocks(blocks, child_indices)
                    cell_texts.append(cell_text)

                    if len(block_groups) == first_group and all(
                        b.type == BlockType.TEXT for b in blocks[first_block:]
                    ):
                        # Text-only cell (lists, headings, code): the row already carries its
                        # text, so separate blocks would index the same text twice.
                        removed = {id(b) for b in blocks[first_block:]}
                        del blocks[first_block:]
                        self._annotation_blocks = {
                            k: v for k, v in self._annotation_blocks.items() if id(v) not in removed
                        }
                        cells_data.append({
                            "type": "text",
                            "text": cell_text,
                            "is_header": cell_type == "tableHeader",
                        })
                        continue
                    cells_data.append({
                        "type": "nested",
                        "text": cell_text,
                        "is_header": cell_type == "tableHeader",
                        "block_indices": [idx.block_index for idx in child_indices if idx.block_index is not None],
                        "block_group_indices": nested_groups_in_cell,
                    })

        if not cells_data:
            return None, []

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
            "cell_details": cells_data,
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
                is_header=all(cell["is_header"] for cell in cells_data),
            ),
            source_id=(node.get("attrs") or {}).get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return block_index, all_nested_block_group_indices

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
        attrs = node.get("attrs") or {}
        panel_type = attrs.get("panelType", "info")
        content = node.get("content") or []

        # Create panel group first and append before processing children
        group_index = len(block_groups)
        panel_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CALLOUT,
            name=panel_type.upper(),
            description=f"{panel_type} panel",
            source_group_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(panel_group)

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

        # Update children after processing
        if child_indices:
            panel_group.children = BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            )
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
        attrs = node.get("attrs") or {}
        title = attrs.get("title", "Details")
        content = node.get("content") or []

        # Create expand group first and append before processing children
        group_index = len(block_groups)
        expand_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.TOGGLE,
            name=title,
            description=f"Expandable section: {title}",
            source_group_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        block_groups.append(expand_group)

        # Process child nodes
        child_indices: list[BlockContainerIndex] = []
        # The title is only embedded if it is a block, so it opens the group as a heading.
        if str(attrs.get("title") or "").strip():
            child_indices.append(BlockContainerIndex(block_index=len(blocks)))
            blocks.append(Block(
                id=str(uuid4()),
                index=len(blocks),
                parent_index=group_index,
                type=BlockType.TEXT,
                sub_type=BlockSubType.HEADING,
                format=DataFormat.MARKDOWN,
                data=str(title).strip(),
                weburl=self._normalize_url(parent_page_url),
            ))
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

        # Update children after processing
        if child_indices:
            expand_group.children = BlockGroupChildren.from_indices(
                block_indices=[idx.block_index for idx in child_indices if idx.block_index is not None],
                block_group_indices=[idx.block_group_index for idx in child_indices if idx.block_group_index is not None],
            )
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
        content = node.get("content") or []

        # Create column list group
        group_index = len(block_groups)
        column_list_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.COLUMN_LIST,
            source_group_id=(node.get("attrs") or {}).get("localId"),
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
        """Parse layoutColumn node into a TEXT_SECTION BlockGroup.

        Not GroupType.COLUMN: answer building only expands hits whose parent group type
        is a text-like label, so column text would be dropped from chat answers.
        """
        content = node.get("content") or []

        # Create column group
        group_index = len(block_groups)
        column_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            source_group_id=(node.get("attrs") or {}).get("localId"),
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
        attrs = node.get("attrs") or {}
        parameters = attrs.get("parameters") or {}

        # Handle nested ADF in extension parameters (e.g. migration nested-table)
        nested_adf_str = parameters.get("adf")
        if nested_adf_str:
            try:
                nested_adf = json.loads(nested_adf_str) if isinstance(nested_adf_str, str) else nested_adf_str
                if not isinstance(nested_adf, dict):
                    raise TypeError("nested ADF must be a dict")

                nested_type = nested_adf.get("type", "")
                if nested_type == "doc" and nested_adf.get("content"):
                    nodes_to_process = nested_adf["content"]
                elif nested_type:
                    # Migration stores the inner table (or other block) as the root node
                    nodes_to_process = [nested_adf]
                else:
                    nodes_to_process = []

                if nodes_to_process:
                    child_indices: list[BlockContainerIndex] = []
                    for nested_node in nodes_to_process:
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
                    if child_indices:
                        return child_indices
            except Exception as e:
                self.logger.debug(f"Failed to parse nested ADF in extension: {e}")

        # Fallback: process content if available
        content = node.get("content") or []
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

        extension_text = self._extension_text(attrs)
        if not extension_text:
            return []
        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.TXT,
            data=extension_text,
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    async def _parse_inlineExtension(
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
        """Parse a standalone inlineExtension like a body-less extension."""
        return await self._parse_extension(
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
        attrs = node.get("attrs") or {}
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
        """Parse inlineCard/blockCard/embedCard node into TEXT block with LINK sub_type."""
        attrs = node.get("attrs") or {}
        link_text, url = self._card_link(attrs)

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
            data=f"[{link_text}]({url})",
            link_metadata=LinkMetadata(
                link_text=link_text,
                link_url=self._normalize_url(url),
                link_type="external",
            ),
            source_id=attrs.get("localId"),
            weburl=self._normalize_url(parent_page_url),
        )
        blocks.append(block)

        return [BlockContainerIndex(block_index=block_index)]

    _parse_blockCard = _parse_inlineCard
    _parse_embedCard = _parse_inlineCard

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
        attrs = node.get("attrs") or {}
        mention_text = self._mention_text(attrs)
        if not mention_text:
            return []

        block_index = len(blocks)
        block = Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.PARAGRAPH,
            format=DataFormat.TXT,
            data=mention_text,
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

    async def _parse_tableRow(
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
        """Parse tableRow - handled by table parser."""
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
        content = node.get("content") or []
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
        attrs = node.get("attrs") or {}
        state = attrs.get("state", "DECIDED")
        content = node.get("content") or []

        # Separate inline content from nested blocks
        inline_content: list[dict[str, Any]] = []
        nested_list_indices: list[BlockContainerIndex] = []

        for child_node in content:
            child_type = child_node.get("type", "")

            # Process nested lists separately (generic like listItem)
            if child_type in ["bulletList", "orderedList", "taskList", "decisionList"]:
                nested_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style="decision",
                )
                nested_list_indices.extend(nested_result)
            elif child_type in ["media", "mediaSingle"]:
                # Process media separately
                media_result = await self._process_node_recursive(
                    node=child_node,
                    blocks=blocks,
                    block_groups=block_groups,
                    parent_group_index=parent_group_index,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    page_id=page_id,
                    list_depth=list_depth,
                    parent_list_style="decision",
                )
                nested_list_indices.extend(media_result)
            elif child_type == "paragraph":
                # Paragraph wraps inline content - extract from its content array
                if "content" in child_node:
                    inline_content.extend(child_node["content"])
            else:
                # Direct inline node (text, mention, emoji, etc.) - add to inline content
                inline_content.append(child_node)

        # Extract text from all collected inline content
        text = self._extract_text_from_content(inline_content).strip()

        if not text:
            return nested_list_indices

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

        result = [BlockContainerIndex(block_index=block_index)]
        result.extend(nested_list_indices)

        return result

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
        content = node.get("content") or []
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
        parent_page_url: str | None = None,
        user_display_names: dict[str, str] | None = None,
    ) -> None:
        """
        Attach inline comment threads to the blocks they were placed on.

        A thread is anchored by its root comment's ``inlineMarkerRef``, which matches the
        id of an annotation mark in the page ADF (recorded by ``parse_adf``). Failing that,
        the quoted selection is matched against block text. A thread that matches nothing
        is attached to the page title block (or the first block) rather than dropped.

        Args:
            blocks: List of blocks to attach comments to
            inline_comments: List of inline comment dictionaries from API
            media_fetcher: Optional media fetcher for comment attachments
            parent_page_url: Absolute page/blog URL for resolving relative comment webui paths
            user_display_names: Optional mapping of author_id to display_name
        """
        if not inline_comments:
            return

        from collections import defaultdict

        comments_by_id = {str(c.get("id")): c for c in inline_comments if c.get("id")}

        def root_id(comment: dict[str, Any]) -> str:
            # Replies to replies point at their immediate parent; walk up to the thread root.
            current = str(comment.get("id", ""))
            seen = {current}
            parent = comment.get("parentCommentId")
            while parent and str(parent) not in seen:
                current = str(parent)
                seen.add(current)
                parent_comment = comments_by_id.get(current)
                if parent_comment is None:
                    break
                parent = parent_comment.get("parentCommentId")
            return current

        normalized_blocks: list[tuple[Block, str]] | None = None
        comments_by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for comment in inline_comments:
            comments_by_thread[root_id(comment)].append(comment)

        for thread_id, thread_comments in comments_by_thread.items():
            thread_comments.sort(
                key=lambda c: (str(c.get("id", "")) != thread_id, self.comment_created_at(c) or ""),
            )
            root_comment = thread_comments[0]
            properties = root_comment.get("properties") or {}
            quoted_text = properties.get("inlineOriginalSelection") or ""
            marker_ref = properties.get("inlineMarkerRef")

            target_block = self._annotation_blocks.get(str(marker_ref)) if marker_ref else None
            if target_block is not None and not any(b is target_block for b in blocks):
                target_block = None
            if target_block is None and quoted_text.strip():
                if normalized_blocks is None:
                    normalized_blocks = self._normalized_blocks_for_comment_match(blocks)
                target_block = self._find_block_by_text(blocks, quoted_text, normalized_blocks)
            if target_block is None:
                if not blocks:
                    self.logger.warning(f"No block to attach inline comment thread {thread_id} to")
                    continue
                self.logger.debug(
                    f"Inline comment thread {thread_id} matched no block; attaching at page level"
                )
                target_block = next((b for b in blocks if self._is_title_block(b)), blocks[0])

            thread_block_comments = []
            for comment in thread_comments:
                block_comment = await self._parse_confluence_comment_to_block_comment(
                    comment,
                    quoted_text=quoted_text if comment is root_comment else None,
                    media_fetcher=media_fetcher,
                    parent_page_url=parent_page_url,
                    user_display_names=user_display_names,
                )
                if block_comment:
                    thread_block_comments.append(block_comment)

            if thread_block_comments:
                target_block.comments.append(thread_block_comments)

    @staticmethod
    def comment_created_at(comment: dict[str, Any]) -> str | None:
        """Creation time of a v2 comment; v2 carries it in ``version.createdAt``."""
        return comment.get("createdAt") or (comment.get("version") or {}).get("createdAt")

    @staticmethod
    def comment_header_text(
        author_name: str | None,
        created_at: str | int | None,
        quoted_text: str | None = None,
    ) -> str:
        """Header line for a comment group, so author, date and quoted text are embedded.

        ``created_at`` is an ISO string (v2 API) or epoch milliseconds. Example:
        ``Comment by Ana Silva on 2024-05-01 on "the quoted text"``.
        """
        header = f"Comment by {(author_name or '').strip() or 'Unknown'}"
        day: str | None = None
        try:
            if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
                day = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            elif isinstance(created_at, str) and created_at.strip():
                day = datetime.fromisoformat(created_at.strip().replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            day = None
        if day:
            header += f" on {day}"
        quote = " ".join((quoted_text or "").split())
        if quote:
            header += f' on "{quote}"'
        return header

    @staticmethod
    def _is_title_block(block: Block) -> bool:
        return bool(block.source_id) and str(block.source_id).endswith("_title")

    @staticmethod
    def _normalize_text_for_comment_match(text: str) -> str:
        """Normalize text for inline comment quoted-text matching (apply to both sides)."""
        clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        clean = re.sub(r"<[^>]+>", "", clean)
        clean = re.sub(r"[*_~`#>\u200b|]", "", clean)
        return re.sub(r"\s+", " ", clean).strip().lower()

    @staticmethod
    def _searchable_text_for_comment_match(block: Block) -> str:
        """Collect searchable plain text from a block for inline comment matching."""
        if block.type == BlockType.TEXT and block.data:
            return str(block.data)

        if block.type == BlockType.TABLE_ROW and isinstance(block.data, dict):
            parts: list[str] = []
            row_text = block.data.get("row_natural_language_text")
            if row_text:
                parts.append(str(row_text))
            for detail in block.data.get("cell_details") or []:
                if isinstance(detail, dict) and detail.get("text"):
                    parts.append(str(detail["text"]))
            cells = block.data.get("cells") or []
            parts.extend(str(cell) for cell in cells if cell)
            return " ".join(parts)

        if isinstance(block.data, str) and block.data.strip():
            return block.data

        return ""

    def _normalized_blocks_for_comment_match(self, blocks: list[Block]) -> list[tuple[Block, str]]:
        """(block, normalized text) for every non-title block, built once per page."""
        return [
            (block, self._normalize_text_for_comment_match(self._searchable_text_for_comment_match(block)))
            for block in blocks
            if not self._is_title_block(block)
        ]

    def _find_block_by_text(
        self,
        blocks: list[Block],
        quoted_text: str,
        normalized_blocks: list[tuple[Block, str]] | None = None,
    ) -> Block | None:
        """
        Find a block that contains the quoted text.

        Args:
            blocks: List of blocks to search
            quoted_text: Text to search for
            normalized_blocks: Output of ``_normalized_blocks_for_comment_match(blocks)``,
                passed in so a page with many comments normalizes its blocks once

        Returns:
            Block containing the text, or None if not found
        """
        if not quoted_text or not quoted_text.strip():
            return None

        # A selection that spans blocks is not inside any one block; its first line is.
        candidates = [quoted_text, quoted_text.strip().splitlines()[0]]
        if normalized_blocks is None:
            normalized_blocks = self._normalized_blocks_for_comment_match(blocks)
        for candidate in candidates:
            needle = self._normalize_text_for_comment_match(candidate)
            if not needle:
                continue
            for block, block_text in normalized_blocks:
                if needle in block_text:
                    return block

        return None

    async def _parse_confluence_comment_to_block_comment(
        self,
        comment: dict[str, Any],
        quoted_text: str | None = None,
        media_fetcher: Callable | None = None,
        parent_page_url: str | None = None,
        user_display_names: dict[str, str] | None = None,
    ) -> BlockComment | None:
        """
        Parse Confluence comment data into BlockComment object.

        Args:
            comment: Raw comment data from Confluence API
            quoted_text: The text that was commented on (for inline comments)
            media_fetcher: Optional media fetcher for attachments
            parent_page_url: Absolute page/blog URL for resolving relative comment webui paths
            user_display_names: Optional mapping of author_id to display_name

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
            created_at = self._parse_confluence_timestamp(self.comment_created_at(comment))

            # Extract weburl (v2 API returns site-relative webui paths)
            links = comment.get("_links", {})
            comment_weburl = self._resolve_comment_weburl(links, parent_page_url)

            # Resolution status
            resolution_status = comment.get("resolutionStatus", "open")

            # Get author display name from cache if available
            author_name = None
            if user_display_names and author_id:
                author_name = user_display_names.get(author_id, "Unknown")

            return BlockComment(
                text=comment_text or "",
                format=DataFormat.MARKDOWN,
                author_id=author_id,
                author_name=author_name,
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
        """Convert ADF to simple markdown text (for comments). See ``adf_to_markdown``."""
        return adf_to_markdown(adf_content)

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

    @staticmethod
    def _shift_group_index_refs(
        index: int,
        insert_at: int,
        delta: int = 1,
    ) -> int:
        """Bump a block_group index reference when a group is inserted at insert_at."""
        if index >= insert_at:
            return index + delta
        return index

    @staticmethod
    def _shift_block_group_ranges(
        ranges: list[Any],
        insert_at: int,
        delta: int = 1,
    ) -> None:
        """Bump block_group index ranges after a block group is inserted at insert_at."""
        for range_obj in ranges:
            if range_obj.start >= insert_at:
                range_obj.start += delta
            if range_obj.end >= insert_at:
                range_obj.end += delta

    @staticmethod
    def shift_parent_indices_after_group_insert(
        blocks: list[Block],
        block_groups: list[BlockGroup],
        insert_at: int = 0,
        delta: int = 1,
    ) -> None:
        """
        Bump parent_index values when a block group is inserted at insert_at.

        Also shifts block_group_ranges in children and block_group_indices stored
        in table cell_details. Block index ranges are unchanged (blocks are not reordered).

        Comment threads keep parent_index=insert_at (content wrapper) unchanged.
        """
        for group in block_groups:
            if group.parent_index is None or group.parent_index < insert_at:
                pass
            elif (
                group.sub_type == GroupSubType.COMMENT_THREAD
                and group.parent_index == insert_at
            ):
                pass
            else:
                group.parent_index += delta

            if group.children and group.children.block_group_ranges:
                ConfluenceBlockParser._shift_block_group_ranges(
                    group.children.block_group_ranges,
                    insert_at,
                    delta,
                )

        for block in blocks:
            if block.parent_index is not None and block.parent_index >= insert_at:
                block.parent_index += delta

            if block.type != BlockType.TABLE_ROW or not isinstance(block.data, dict):
                continue
            for detail in block.data.get("cell_details") or []:
                if not isinstance(detail, dict):
                    continue
                nested_group_indices = detail.get("block_group_indices")
                if not nested_group_indices:
                    continue
                detail["block_group_indices"] = [
                    ConfluenceBlockParser._shift_group_index_refs(
                        gi, insert_at, delta
                    )
                    for gi in nested_group_indices
                ]

    @staticmethod
    def sync_table_row_links(
        blocks: list[Block],
        block_groups: list[BlockGroup],
    ) -> None:
        """
        Align TABLE_ROW parent_index and row_number fields with TABLE group children.

        Uses each table group's explicit children ranges as source of truth so row
        blocks stay correct after block_groups are reordered (e.g. content wrapper insert).
        """
        for group in block_groups:
            if group.type != GroupType.TABLE or not group.children:
                continue

            row_number = 0
            for range_obj in group.children.block_ranges:
                for block_index in range(range_obj.start, range_obj.end + 1):
                    if block_index < 0 or block_index >= len(blocks):
                        continue
                    block = blocks[block_index]
                    if block.type != BlockType.TABLE_ROW:
                        continue

                    block.parent_index = group.index
                    row_number += 1

                    if block.table_row_metadata:
                        block.table_row_metadata.row_number = row_number

                    if isinstance(block.data, dict):
                        block.data["row_number"] = row_number

    @staticmethod
    def sync_nested_table_group_links(
        block_groups: list[BlockGroup],
    ) -> None:
        """Align nested TABLE block_group parent_index with parent table children."""
        for group in block_groups:
            if group.type != GroupType.TABLE or not group.children:
                continue
            for range_obj in group.children.block_group_ranges:
                for group_index in range(range_obj.start, range_obj.end + 1):
                    if group_index < 0 or group_index >= len(block_groups):
                        continue
                    nested_group = block_groups[group_index]
                    nested_group.parent_index = group.index

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

        self.sync_table_row_links(blocks, block_groups)

        # Ensure first row is marked header when table declares has_header
        for group in block_groups:
            if (
                group.type != GroupType.TABLE
                or not group.table_metadata
                or not group.table_metadata.has_header
                or not group.children
                or not group.children.block_ranges
            ):
                continue
            first_index = group.children.block_ranges[0].start
            if 0 <= first_index < len(blocks):
                first_row = blocks[first_index]
                if first_row.table_row_metadata:
                    first_row.table_row_metadata.is_header = True

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

                # Reset counters for any deeper levels
                for level in list(counters.keys()):
                    if level > indent_level:
                        del counters[level]

                if indent_level in counters and previous_indent is not None:
                    counters[indent_level] += 1
                else:
                    counters[indent_level] = 1

                # Replace "1." with correct number
                current_number = counters[indent_level]
                if isinstance(block.data, str):
                    block.data = re.sub(r'^1\.\s*', f'{current_number}. ', block.data, count=1)

                previous_indent = indent_level
            elif not (
                block.list_metadata
                and previous_indent is not None
                and (block.list_metadata.indent_level or 0) > previous_indent
            ):
                # A deeper sub-list (bullets under a numbered item) does not end the numbered list.
                previous_indent = None

    @staticmethod
    def _list_item_lives_in_table(
        block: Block,
        block_groups: list[BlockGroup],
    ) -> bool:
        """True when a list item block belongs to a TABLE group (nested in a cell)."""
        parent_index = block.parent_index
        if parent_index is None or parent_index < 0 or parent_index >= len(block_groups):
            return False
        return block_groups[parent_index].type == GroupType.TABLE

    def _group_list_items(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup]
    ) -> None:
        """
        Group consecutive list items into BlockGroups.

        List items nested inside table cells keep their TABLE parent_index and are
        referenced via cell_details.block_indices only (not wrapped in LIST groups).

        Args:
            blocks: List of blocks (modified in-place)
            block_groups: List of block groups (modified in-place)
        """
        if not blocks:
            return

        # Lists still open, outermost first: (indent, style, parent_index, items, nested list groups).
        open_lists: list[tuple[int, str | None, int | None, list[int], list[int]]] = []

        def close(keep: int) -> None:
            while len(open_lists) > keep:
                _, style, parent_index, items, nested = open_lists.pop()
                group_index = self._create_list_group(blocks, block_groups, items, style, nested)
                # A top-level list is not added to its parent's groups: the parent (panel, expand,
                # comment) already lists the items as its own blocks, and both would repeat them.
                if open_lists:
                    open_lists[-1][4].append(group_index)

        for i, block in enumerate(blocks):
            if not block.list_metadata or self._list_item_lives_in_table(block, block_groups):
                close(0)
                continue

            list_style = block.list_metadata.list_style
            indent_level = block.list_metadata.indent_level or 0

            # Keep the lists this item sits in: shallower ones, or the same list at its level.
            # Items under different parents (another panel, column or comment) are different lists.
            keep = 0
            while keep < len(open_lists):
                indent, style, parent_index, _, _ = open_lists[keep]
                if parent_index != block.parent_index or indent > indent_level or (
                    indent == indent_level and style != list_style
                ):
                    break
                keep += 1
            close(keep)

            if open_lists and open_lists[-1][0] == indent_level:
                open_lists[-1][3].append(i)
            else:
                open_lists.append((indent_level, list_style, block.parent_index, [i], []))

        close(0)

    def _create_list_group(
        self,
        blocks: list[Block],
        block_groups: list[BlockGroup],
        group_block_indices: list[int],
        list_style: str | None,
        nested_group_indices: list[int] | None = None,
    ) -> int | None:
        """Create a BlockGroup for a sequence of list items and the sub-lists nested in them."""
        if len(group_block_indices) < 1:
            return None

        group_index = len(block_groups)
        group_children = BlockGroupChildren.from_indices(
            block_indices=group_block_indices,
            block_group_indices=nested_group_indices or [],
        )

        parent_indices = {
            blocks[idx].parent_index
            for idx in group_block_indices
            if blocks[idx].parent_index is not None
        }
        list_parent_index = parent_indices.pop() if len(parent_indices) == 1 else None

        group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=list_parent_index,
            type=GroupType.ORDERED_LIST if list_style == "numbered" else GroupType.LIST,
            children=group_children,
            list_metadata=blocks[group_block_indices[0]].list_metadata,
        )
        block_groups.append(group)

        # Update blocks to point to the group
        for idx in group_block_indices:
            blocks[idx].parent_index = group_index
        for nested_index in nested_group_indices or []:
            block_groups[nested_index].parent_index = group_index

        return group_index

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
        block_indices: tuple[int, int] | None = None,
    ) -> BlockGroup:
        """
        Create a COMMENT BlockGroup from a BlockComment object.

        Args:
            block_comment: BlockComment object with comment metadata
            group_index: Index for the new BlockGroup
            parent_group_index: Index of parent BlockGroup (COMMENT_THREAD)
            source_id: Confluence comment ID
            children_records: Optional list of ChildRecord for attachments
            block_indices: Optional (start, end) indices of parsed comment blocks

        Returns:
            BlockGroup object with type TEXT_SECTION and sub_type COMMENT
        """
        block_group = BlockGroup(
            id=str(uuid4()),
            index=group_index,
            parent_index=parent_group_index,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT,
            source_group_id=source_id,
            name=block_comment.author_name or "Comment",
            description=f"Comment by {block_comment.author_name or 'Unknown'}",
            children_records=children_records,
            weburl=block_comment.weburl,
        )

        # Add block children if provided
        if block_indices:
            block_group.children = BlockGroupChildren.from_indices(
                block_indices=[*range(block_indices[0], block_indices[1] + 1)],
                block_group_indices=[]
            )

        return block_group

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
            parent_index=None,  # top level; group 0 is whatever the page body starts with
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.COMMENT_THREAD,
            source_group_id=f"thread_{comment_type}_{thread_id}",
            name=f"{comment_type.capitalize()} Comment Thread",
            description=f"{comment_type.capitalize()} comment thread for page {page_title}",
            weburl=self._normalize_url(weburl),
        )
