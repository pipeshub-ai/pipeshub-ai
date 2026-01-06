"""
Notion Block Parser

Converts Notion API block objects into Pipeshub Block and BlockGroup objects.
This parser is designed to be extensible - new block types can be easily added.
"""

import json
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

# Type alias for relations and people tuple
RelationsAndPeople = Tuple[List[str], List[str]]

from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CodeMetadata,
    DataFormat,
    FileMetadata,
    GroupType,
    LinkMetadata,
    ListMetadata,
    TableMetadata,
    TableRowMetadata,
)


class NotionBlockParser:
    """
    Parser for converting Notion blocks to Pipeshub Block/BlockGroup objects.
    
    This class provides a way to handle different Notion block types.
    To add support for a new block type, add a method following the pattern:
    `_parse_{block_type}(self, notion_block: Dict[str, Any], ...) -> Optional[Block|BlockGroup]`
    """

    def __init__(self, logger, config=None) -> None:
        """Initialize the parser with a logger and optional config for LLM calls."""
        self.logger = logger
        self.config = config

    @staticmethod
    def _normalize_url(url: Optional[str]) -> Optional[str]:
        """
        Normalize URL for Pydantic HttpUrl validation.
        
        Pydantic HttpUrl doesn't accept empty strings, convert empty strings to None.
        
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
        parent_page_url: Optional[str], 
        block_id: Optional[str]
    ) -> Optional[str]:
        """
        Construct block URL by appending block ID anchor to parent page URL.
        
        Notion block URLs use format: {page_url}#{block_id_without_hyphens}
        
        Args:
            parent_page_url: URL of the parent page
            block_id: Notion block ID (with hyphens)
            
        Returns:
            Full block URL with anchor, or None if inputs are missing
        """
        if not parent_page_url or not block_id:
            return None
        
        # Remove hyphens from block ID for URL fragment
        block_id_clean = block_id.replace("-", "")
        
        # Append as anchor
        return f"{parent_page_url}#{block_id_clean}"

    def extract_plain_text(self, rich_text_array: List[Dict[str, Any]]) -> str:
        """
        Extract plain text from Notion rich text array (no formatting).
        Use this for code blocks where markdown formatting would corrupt the content.
        
        Args:
            rich_text_array: List of rich text objects from Notion API
            
        Returns:
            Plain text string
        """
        if not rich_text_array:
            return ""
        
        text_parts = []
        for item in rich_text_array:
            # Get plain text content
            text_content = item.get("plain_text", "")
            if not text_content and "text" in item and isinstance(item["text"], dict):
                text_content = item["text"].get("content", "")
            if text_content:
                text_parts.append(text_content)
        
        return "".join(text_parts)

    def extract_rich_text(self, rich_text_array: List[Dict[str, Any]]) -> str:
        """
        Extract markdown-formatted text from Notion rich text array.
        Preserves links, bold, italic, code, and strikethrough formatting.
        
        Handles whitespace properly to avoid breaking markdown syntax when
        formatted text has leading/trailing spaces.
        
        Args:
            rich_text_array: List of rich text objects from Notion API
            
        Returns:
            Markdown-formatted string
        """
        if not rich_text_array:
            return ""
        
        text_parts = []
        for item in rich_text_array:
            item_type = item.get("type", "text")
            
            # Handle non-text rich text types
            if item_type == "equation":
                # Equation: extract expression and format as LaTeX
                equation = item.get("equation", {})
                expression = equation.get("expression", "")
                if expression:
                    text_parts.append(f"$${expression}$$")
                continue
            elif item_type == "mention":
                # Mention: extract plain text representation
                mention = item.get("mention", {})
                mention_type = mention.get("type", "")
                plain_text = item.get("plain_text", "")
                if plain_text:
                    text_parts.append(plain_text)
                continue
            elif item_type != "text":
                # Unknown rich text type - fallback to plain_text
                plain_text = item.get("plain_text", "")
                if plain_text:
                    text_parts.append(plain_text)
                continue
            
            # Handle text type
            # Get base text content
            text_content = item.get("plain_text", "")
            if not text_content and "text" in item and isinstance(item["text"], dict):
                text_content = item["text"].get("content", "")
            if not text_content:
                continue
            
            # Handle inline links - check both href and text.link.url
            href = item.get("href")
            if not href and "text" in item and isinstance(item["text"], dict):
                link_obj = item["text"].get("link")
                if link_obj and isinstance(link_obj, dict):
                    href = link_obj.get("url")
            
            if href:
                # Format as markdown link: [text](url)
                text_content = f"[{text_content}]({href})"
            
            # Handle annotations (formatting) with space awareness
            # Extract leading/trailing whitespace to prevent markdown syntax errors
            # e.g., "** bold**" should be " **bold**" (space outside markers)
            annotations = item.get("annotations", {})
            
            if any(annotations.get(key) for key in ["code", "bold", "italic", "strikethrough", "underline"]):
                # Preserve whitespace outside formatting markers
                original_text = text_content
                l_stripped = text_content.lstrip()
                r_stripped = l_stripped.rstrip()
                
                leading_space = original_text[:len(original_text) - len(l_stripped)]
                trailing_space = l_stripped[len(r_stripped):]
                core_text = r_stripped
                
                # Handle inline code - escape backticks if present
                if annotations.get("code"):
                    # Escape backticks by using longer fence if content contains backticks
                    if "`" in core_text:
                        # Count consecutive backticks to find safe fence
                        max_backticks = max(len(seq) for seq in core_text.split("`") if seq == "" or core_text.startswith("`"))
                        fence = "`" * (max_backticks + 1)
                        core_text = f"{fence}{core_text}{fence}"
                    else:
                        core_text = f"`{core_text}`"
                
                if annotations.get("bold"):
                    core_text = f"**{core_text}**"
                
                if annotations.get("italic"):
                    core_text = f"*{core_text}*"
                
                if annotations.get("strikethrough"):
                    core_text = f"~~{core_text}~~"
                
                # Underline - markdown doesn't have native underline, use HTML
                if annotations.get("underline"):
                    core_text = f"<u>{core_text}</u>"
                
                # Color - store in semantic_metadata or as HTML span
                # Note: Notion colors are like "default", "gray", "brown", etc.
                color = annotations.get("color", "default")
                if color != "default":
                    core_text = f'<span style="color: {color}">{core_text}</span>'
                
                # Reconstruct with whitespace outside formatting markers
                text_content = f"{leading_space}{core_text}{trailing_space}"
            
            text_parts.append(text_content)
        
        return "".join(text_parts)

    async def parse_block(
        self,
        notion_block: Dict[str, Any],
        parent_group_index: Optional[int] = None,
        block_index: int = 0,
        parent_page_url: Optional[str] = None,
    ) -> Tuple[Optional[Block], Optional[BlockGroup], List[Block]]:
        """
        Parse a Notion block into Block/BlockGroup.
        
        This is the main entry point for parsing. It dispatches to type-specific parsers.
        
        Note: The third element (List[Block]) is currently always empty. Hierarchy building
        is handled separately by the connector's recursive traversal logic, which:
        1. Detects has_children=True on notion_block
        2. Fetches children via API
        3. Processes them recursively
        4. Attaches them to BlockGroup.children via BlockContainerIndex
        
        Args:
            notion_block: Raw Notion block object from API
            parent_group_index: Index of parent BlockGroup (if nested)
            block_index: Current block index
            parent_page_url: URL of parent page for constructing block URLs
            
        Returns:
            Tuple of (Block, BlockGroup, List[Block])
            - Block: If the block is a content block (paragraph, heading, etc.)
            - BlockGroup: If the block is a container (table, column_list, toggle, etc.)
            - List[Block]: Currently always empty - hierarchy built separately by connector
            Only one of Block or BlockGroup will be non-None
        """
        block_type = notion_block.get("type", "")
        block_id = notion_block.get("id", "")
        
        # Skip archived/trashed blocks
        if notion_block.get("archived", False) or notion_block.get("in_trash", False):
            return None, None, []
        
        # Skip unsupported block types (and their children will be skipped in connector)
        if block_type == "unsupported":
            self.logger.debug(f"Skipping unsupported block type (id: {block_id})")
            return None, None, []
        
        # Get the type-specific data
        type_data = notion_block.get(block_type, {})
        
        # Dispatch to type-specific parser
        parser_method = getattr(
            self,
            f"_parse_{block_type}",
            self._parse_unknown  # Fallback for unknown types
        )
        
        try:
            result = await parser_method(
                notion_block,
                type_data,
                parent_group_index,
                block_index,
                parent_page_url
            )
            
            # Handle different return types
            if isinstance(result, tuple) and len(result) == 3:
                return result
            elif isinstance(result, Block):
                return result, None, []
            elif isinstance(result, BlockGroup):
                return None, result, []
            else:
                return None, None, []
            # TODO: remove list of blocks
                
        except Exception as e:
            self.logger.warning(
                f"Error parsing block {block_id} of type {block_type}: {e}",
                exc_info=True
            )
            return None, None, []

    # ============================================================================
    # Text Block Parsers
    # ============================================================================

    async def _parse_paragraph(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse paragraph block."""
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            data=text,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_heading_1(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse heading_1 block."""
        return await self._parse_heading(notion_block, type_data, parent_group_index, block_index, parent_page_url, level=1)

    async def _parse_heading_2(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse heading_2 block."""
        return await self._parse_heading(notion_block, type_data, parent_group_index, block_index, parent_page_url, level=2)

    async def _parse_heading_3(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse heading_3 block."""
        return await self._parse_heading(notion_block, type_data, parent_group_index, block_index, parent_page_url, level=3)

    async def _parse_heading(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
        level: int = 1,
    ) -> Block:
        """
        Parse heading block (internal helper).
        
        Note: If heading is toggleable (has_children=True), the connector will
        handle it as a container. This parser only handles the heading text itself.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)

        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.HEADING,
            format=DataFormat.MARKDOWN,
            data=text,
            name=f"H{level}",
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_quote(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse quote block."""
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        
        # Format as markdown quote
        formatted_text = f"> {text}" if text else ""
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.QUOTE,
            format=DataFormat.MARKDOWN,
            data=formatted_text,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_callout(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse callout block.
        
        If callout has children (has_children=True), the connector will handle it
        as a container. This parser handles the callout text and icon.
        For callouts with children, they should be represented as BlockGroup,
        but that logic is handled in the connector based on has_children flag.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        icon = type_data.get("icon", {})
        
        # Handle icon (emoji, external URL, or file)
        icon_text = ""
        icon_url = None
        
        if icon and isinstance(icon, dict):
            icon_type = icon.get("type", "")
            if icon_type == "emoji":
                icon_text = icon.get("emoji", "")
            elif icon_type == "external":
                icon_url = icon.get("external", {}).get("url", "")
                icon_text = "📌"  # Placeholder emoji for external icons
            elif icon_type == "file":
                icon_url = icon.get("file", {}).get("url", "")
                icon_text = "📎"  # Placeholder emoji for file icons
        
        # Format as callout with icon
        formatted_text = f"{icon_text} {text}".strip() if icon_text else text
        
        # Store icon URL in link_metadata if available
        # Normalize URL (empty string -> None for Pydantic HttpUrl validation)
        normalized_icon_url = self._normalize_url(icon_url)
        link_metadata = None
        if normalized_icon_url:
            link_metadata = LinkMetadata(
                link_url=normalized_icon_url,
                link_type="external"
            )
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.PARAGRAPH,
            format=DataFormat.MARKDOWN,
            data=formatted_text,
            link_metadata=link_metadata,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # List Block Parsers
    # ============================================================================

    async def _parse_bulleted_list_item(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse bulleted list item.
        
        Note: indent_level is currently set to 0. Actual nesting structure is preserved
        via parent_index relationships in the Block hierarchy. To compute visual indent_level,
        the connector's recursive traversal would need to track depth and pass it as a parameter.
        This is a future enhancement that requires changes to both parser and connector.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        
        # Format as markdown bullet list item
        formatted_text = f"- {text}" if text else "-"
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data=formatted_text,
            list_metadata=ListMetadata(
                list_style="bullet",
                indent_level=0,  # TODO: Calculate from traversal depth when connector supports it
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_numbered_list_item(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse numbered list item.
        
        Note: indent_level is currently set to 0. Actual nesting structure is preserved
        via parent_index relationships in the Block hierarchy. To compute visual indent_level,
        the connector's recursive traversal would need to track depth and pass it as a parameter.
        This is a future enhancement that requires changes to both parser and connector.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        
        # Format as markdown numbered list item (use 1. as placeholder, actual numbering handled by markdown renderer)
        formatted_text = f"1. {text}" if text else "1."
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data=formatted_text,
            list_metadata=ListMetadata(
                list_style="numbered",
                indent_level=0,  # TODO: Calculate from traversal depth when connector supports it
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_to_do(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse to-do block.
        
        Note: indent_level is currently set to 0. Actual nesting structure is preserved
        via parent_index relationships in the Block hierarchy. To compute visual indent_level,
        the connector's recursive traversal would need to track depth and pass it as a parameter.
        This is a future enhancement that requires changes to both parser and connector.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        checked = type_data.get("checked", False)
        
        # Format as markdown checkbox
        checkbox_text = f"- [{'x' if checked else ' '}] {text}" if text else f"- [{'x' if checked else ' '}]"
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            format=DataFormat.MARKDOWN,
            data=checkbox_text,
            list_metadata=ListMetadata(
                list_style="checkbox",
                indent_level=0,  # TODO: Calculate from traversal depth when connector supports it
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # Code Block Parser
    # ============================================================================

    async def _parse_code(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse code block.
        
        Uses extract_plain_text() instead of extract_rich_text() to avoid
        applying markdown formatting to code content, which would corrupt it.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_plain_text(rich_text)
        language = type_data.get("language", "plain text")
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.CODE,
            format=DataFormat.TXT,
            data=text,
            name=f"{language} code" if language else "Code",
            code_metadata=CodeMetadata(
                language=language,
                is_executable=False,
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # Divider Block Parser
    # ============================================================================

    async def _parse_divider(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse divider block."""
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.DIVIDER,
            format=DataFormat.TXT,
            data="---",
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # Link Block Parsers
    # ============================================================================

    async def _parse_child_page(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse child_page block (create a child record reference block)."""
        title = type_data.get("title", "Untitled Page")
        page_id = notion_block.get("id", "")
        
        # Structure reference data as a dictionary (for display/context)
        reference_data = {
            "child_external_id": page_id,
            "child_record_type": "NOTION_PAGE",
            "child_record_name": title,
            "reference_type": "child_page",
            "display_text": title
        }
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.CHILD_RECORD,
            format=DataFormat.JSON,
            data=reference_data,
            source_name=title,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
            # Table row metadata will be populated by _resolve_child_reference_blocks
            table_row_metadata=None,
        )

    async def _parse_child_database(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse child_database block (create a child record reference block)."""
        title = type_data.get("title", "Untitled Database")
        database_id = notion_block.get("id", "")
        
        # Structure reference data as a dictionary (for display/context)
        reference_data = {
            "child_external_id": database_id,
            "child_record_type": "NOTION_DATA_SOURCE",
            "child_record_name": title,
            "reference_type": "child_database",
            "display_text": title
        }
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.CHILD_RECORD,
            format=DataFormat.JSON,
            data=reference_data,
            source_name=title,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
            # Table row metadata will be populated by _resolve_child_reference_blocks
            table_row_metadata=None,
        )

    async def _parse_bookmark(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse bookmark block."""
        url = type_data.get("url", "")
        caption = type_data.get("caption", [])
        caption_text = self.extract_rich_text(caption) if caption else ""
        
        # Normalize URL (empty string -> None for Pydantic HttpUrl validation)
        normalized_url = self._normalize_url(url)
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.LINK,
            format=DataFormat.TXT,
            data=caption_text or url,
            link_metadata=LinkMetadata(
                link_text=caption_text or url,
                link_url=normalized_url,
                link_type="external",
            ) if normalized_url else None,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ) or normalized_url,
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_link_preview(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse link_preview block."""
        url = type_data.get("url", "")
        
        # Normalize URL (empty string -> None for Pydantic HttpUrl validation)
        normalized_url = self._normalize_url(url)
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.LINK,
            format=DataFormat.TXT,
            data=url,
            link_metadata=LinkMetadata(
                link_text=url,
                link_url=normalized_url,
                link_type="external",
            ) if normalized_url else None,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ) or normalized_url,
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_embed(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse embed block."""
        url = type_data.get("url", "")
        caption = type_data.get("caption", [])
        caption_text = self.extract_rich_text(caption) if caption else ""
        
        # Normalize URL (empty string -> None for Pydantic HttpUrl validation)
        normalized_url = self._normalize_url(url)
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.LINK,
            format=DataFormat.TXT,
            data=caption_text or url,
            link_metadata=LinkMetadata(
                link_text=caption_text or url,
                link_url=normalized_url,
                link_type="external",
            ) if normalized_url else None,
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ) or normalized_url,
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # Structural Block Parsers (BlockGroups)
    # ============================================================================

    async def _parse_column_list(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> BlockGroup:
        """Parse column_list block (creates a BlockGroup, children handled separately)."""
        return BlockGroup(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=GroupType.INLINE,
            source_group_id=notion_block.get("id"),
            description="Column Layout Container",
        )

    async def _parse_column(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> BlockGroup:
        """Parse column block (creates a BlockGroup, children handled separately)."""
        width_ratio = type_data.get("width_ratio")
        
        return BlockGroup(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=GroupType.INLINE,
            source_group_id=notion_block.get("id"),
            description=f"Column (width: {width_ratio:.1%})" if width_ratio else "Column",
            data={"width_ratio": width_ratio, "has_children": notion_block.get("has_children", False)} if width_ratio else None,
        )

    async def _parse_toggle(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> BlockGroup:
        """
        Parse toggle block as BlockGroup.
        
        Toggles in Notion are containers with children. The toggle text is stored in the group's data.
        
        Note: Toggle children are automatically fetched and processed by the connector's
        recursive traversal logic when it detects has_children=True on the notion_block.
        The children will be attached to this BlockGroup via the children list.
        """
        rich_text = type_data.get("rich_text", [])
        text = self.extract_rich_text(rich_text)
        
        return BlockGroup(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=GroupType.INLINE,
            source_group_id=notion_block.get("id"),
            data=text,  # Store toggle text in data field
            name=text[:50] if text else "Toggle",
            description="Toggle Block",  # Indicate this is a toggle
            format=DataFormat.MARKDOWN,
        )

    # ============================================================================
    # Table Block Parsers
    # ============================================================================

    async def _parse_table(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> BlockGroup:
        """
        Parse table block (creates BlockGroup, rows handled as children).
        
        Note: LLM-based table summary generation is not possible here because
        Notion returns table and table_row blocks separately. The connector would
        need to collect all rows first, then call a post-processing method to
        generate the summary. For now, basic structure is created.
        """
        table_width = type_data.get("table_width", 0)
        has_column_header = type_data.get("has_column_header", False)
        has_row_header = type_data.get("has_row_header", False)
        
        table_metadata = TableMetadata(
            num_of_cols=table_width,
            has_header=has_column_header,
        )
        
        # Store row header info in description
        description = None
        if has_row_header:
            description = "Table with row headers"
        
        return BlockGroup(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=GroupType.TABLE,
            source_group_id=notion_block.get("id"),
            table_metadata=table_metadata,
            description=description,
            data={"has_row_header": has_row_header} if has_row_header else None,
            format=DataFormat.JSON,
        )

    async def _parse_table_row(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse table_row block.
        
        Stores both structured cell data and a natural language representation.
        Matches Docling converter format with additional Notion-specific metadata.
        """
        cells = type_data.get("cells", [])
        cell_texts = []
        
        # Extract rich text from each cell
        for cell in cells:
            cell_text = self.extract_rich_text(cell) if isinstance(cell, list) else str(cell)
            cell_texts.append(cell_text)
        
        # Use zero-width space as delimiter to avoid collision with user content
        delimiter = "\u200B|\u200B"  # Zero-width space around pipe
        row_natural_language_text = delimiter.join(cell_texts)
        
        # Create row data matching Docling format while keeping Notion extras
        row_data = {
            "row_natural_language_text": row_natural_language_text,  # Docling format
            "row_number": block_index,  # Docling format - will be updated during hierarchy building
            "row": json.dumps({"cells": cell_texts}),  # Docling format - JSON string of row structure
            # Extra Notion-specific fields (good practice to keep):
            "cells": cell_texts,  # Keep for easier access without JSON parsing
        }
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.TABLE_ROW,
            format=DataFormat.JSON,
            data=row_data,
            table_row_metadata=TableRowMetadata(
                row_number=block_index,  # Will be updated during hierarchy building
                is_header=False,  # Will be updated if parent table has header
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    # ============================================================================
    # Data Source Parsers (for Notion databases/tables)
    # ============================================================================

    async def parse_data_source_to_blocks(
        self,
        data_source_metadata: Dict[str, Any],
        data_source_rows: List[Dict[str, Any]],
        data_source_id: str,
        get_record_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
        get_user_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
    ) -> Tuple[List[Block], List[BlockGroup]]:
        """
        Parse Notion data source metadata and rows into TABLE blocks with LLM-enhanced summaries.
        
        This method converts a data source (database view with rows) into a 
        BlocksContainer-compatible structure with a TABLE BlockGroup and 
        TABLE_ROW blocks. Uses LLM to generate table summary and row descriptions.
        
        Args:
            data_source_metadata: Response from retrieve_data_source_by_id API
            data_source_rows: Combined results from query_data_source_by_id API (all pages)
            data_source_id: The data source ID for reference
            get_record_child_callback: Optional async callback to get ChildRecord for a record.
                                     Takes external_id (str) and returns Optional[ChildRecord]
            get_user_child_callback: Optional async callback to get ChildRecord for a user.
                                   Takes user_id (str) and returns Optional[ChildRecord]
        
        Returns:
            Tuple of (List[Block], List[BlockGroup])
        """
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []
        
        # Extract column definitions and description
        properties = data_source_metadata.get("properties", {})
        column_names = list(properties.keys())
        
        description_raw = data_source_metadata.get("description") or []
        description = self.extract_rich_text(description_raw)
        self.logger.debug(f"Parsing data source {data_source_id} with {len(column_names)} columns")
        
        # Generate table markdown and get LLM-enhanced summary
        table_markdown = self._generate_data_source_markdown(column_names, data_source_rows)
        table_summary, column_headers = await self._get_llm_enhanced_table_info(
            table_markdown, description, column_names
        )
        
        # Create TABLE BlockGroup
        table_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            type=GroupType.TABLE,
            source_group_id=data_source_id,
            description=table_summary,
            table_metadata=TableMetadata(
                num_of_cols=len(column_names),
                num_of_rows=len(data_source_rows) + 1,  # +1 for header
                has_header=True,
                column_names=column_headers,
            ),
            format=DataFormat.JSON,
            data={
                "table_summary": table_summary,
                "column_headers": column_headers,
                "table_markdown": table_markdown,
            },
            children=[],
        )
        block_groups.append(table_group)
        
        # Create header row block
        delimiter = "\u200B|\u200B"
        header_block = Block(
            id=str(uuid4()),
            index=0,
            parent_index=0,
            type=BlockType.TABLE_ROW,
            data={
                "row_natural_language_text": delimiter.join(column_names),
                "row_number": 1,
                "row": json.dumps({"cells": column_names}),
                "cells": column_names,
            },
            format=DataFormat.JSON,
            table_row_metadata=TableRowMetadata(row_number=1, is_header=True),
        )
        blocks.append(header_block)
        table_group.children.append(BlockContainerIndex(block_index=0))
        
        # Extract cell values and generate LLM row descriptions
        row_cell_values, row_dicts, relations_and_people_list = await self._extract_row_cell_values(
            data_source_rows, column_names, column_headers,
            get_record_child_callback, get_user_child_callback
        )
        row_descriptions = await self._generate_llm_row_descriptions(
            row_dicts, table_summary, column_headers
        )
        
        # Create data row blocks
        await self._create_data_row_blocks(
            blocks, table_group, data_source_rows, row_cell_values, 
            row_descriptions, delimiter, get_record_child_callback, get_user_child_callback,
            relations_and_people_list
        )
        
        self.logger.info(f"Parsed data source {data_source_id}: {len(column_names)} columns, {len(blocks)} rows")
        
        return blocks, block_groups


    async def _get_llm_enhanced_table_info(
        self, 
        table_markdown: str, 
        fallback_description: str, 
        fallback_columns: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Use LLM to generate enhanced table summary and column headers.
        Falls back to provided values if LLM is unavailable or fails.
        """
        table_summary = fallback_description
        column_headers = fallback_columns
        
        if self.config and table_markdown:
            from app.utils.indexing_helpers import get_table_summary_n_headers
            try:
                response = await get_table_summary_n_headers(self.config, table_markdown)
                if response:
                    table_summary = response.summary or fallback_description
                    column_headers = response.headers or fallback_columns
                    self.logger.debug(f"Generated table summary: {table_summary[:100]}...")
            except Exception as e:
                self.logger.warning(f"Failed to generate table summary: {e}")
        
        return table_summary, column_headers


    def _extract_relations_and_people(
        self,
        properties: Dict[str, Any],
    ) -> Tuple[List[str], List[str]]:
        """
        Extract relation page IDs and people user IDs from properties.
        
        Args:
            properties: Dictionary of property name -> property object
            
        Returns:
            Tuple of (relation_page_ids, people_user_ids)
        """
        relation_page_ids = []
        people_user_ids = []
        
        for prop in properties.values():
            if not isinstance(prop, dict):
                continue
                
            prop_type = prop.get("type", "")
            
            if prop_type == "relation":
                relations = prop.get("relation", [])
                for rel in relations:
                    page_id = rel.get("id")
                    if page_id:
                        relation_page_ids.append(page_id)
            
            elif prop_type == "people":
                people = prop.get("people", [])
                for person in people:
                    user_id = person.get("id")
                    if user_id:
                        people_user_ids.append(user_id)
            
            elif prop_type == "rollup":
                rollup = prop.get("rollup", {})
                rollup_type = rollup.get("type", "")
                if rollup_type == "array":
                    arr = rollup.get("array", [])
                    for item in arr:
                        if isinstance(item, dict):
                            item_type = item.get("type", "")
                            if item_type == "people":
                                people_list = item.get("people", [])
                                for person in people_list:
                                    user_id = person.get("id")
                                    if user_id:
                                        people_user_ids.append(user_id)
                            elif item_type == "relation":
                                # Rollup can contain relations too
                                rel_id = item.get("id")
                                if rel_id:
                                    relation_page_ids.append(rel_id)
        
        return relation_page_ids, people_user_ids

    async def _extract_row_cell_values(
        self,
        data_source_rows: List[Dict[str, Any]],
        column_names: List[str],
        column_headers: List[str],
        get_record_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
        get_user_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
    ) -> Tuple[List[List[str]], List[Dict[str, str]], List[RelationsAndPeople]]:
        """
        Extract cell values from all rows and prepare data for LLM processing.
        Also extracts relation and people IDs for later resolution.
        
        Returns:
            Tuple of (cell_values_list, row_dicts_for_llm, relations_and_people_list)
            relations_and_people_list contains (relation_page_ids, people_user_ids) for each row
        """
        cell_values_list = []
        row_dicts = []
        relations_and_people_list = []
        
        for page in data_source_rows:
            page_properties = page.get("properties", {})
            
            # Extract relations and people IDs
            relation_page_ids, people_user_ids = self._extract_relations_and_people(page_properties)
            relations_and_people_list.append((relation_page_ids, people_user_ids))
            
            # Extract cell values for this row (with resolved names if callbacks provided)
            cell_values = []
            for col_name in column_names:
                prop = page_properties.get(col_name, {})
                cell_value = await self._extract_property_value_with_resolution(
                    prop, get_record_child_callback, get_user_child_callback
                )
                cell_values.append(cell_value)
            cell_values_list.append(cell_values)
            
            # Create dictionary with column headers as keys for LLM
            row_dict = {
                column_headers[i] if i < len(column_headers) else f"Column_{i+1}": cell_values[i]
                for i in range(len(cell_values))
            }
            row_dicts.append(row_dict)
        
        return cell_values_list, row_dicts, relations_and_people_list


    async def _generate_llm_row_descriptions(
        self,
        row_dicts: List[Dict[str, str]],
        table_summary: str,
        column_headers: List[str],
    ) -> List[str]:
        """
        Generate natural language descriptions for all rows using LLM.
        Returns empty list if LLM is unavailable or fails.
        """
        if not (self.config and row_dicts and table_summary):
            return []
        
        from app.utils.indexing_helpers import get_rows_text
        
        try:
            table_data = {"grid": [[row] for row in row_dicts]}
            row_descriptions, _ = await get_rows_text(
                self.config, table_data, table_summary, column_headers
            )
            self.logger.debug(f"Generated {len(row_descriptions)} row descriptions")
            return row_descriptions
        except Exception as e:
            self.logger.warning(f"Failed to generate row descriptions: {e}")
            return []


    async def _create_data_row_blocks(
        self,
        blocks: List[Block],
        table_group: BlockGroup,
        data_source_rows: List[Dict[str, Any]],
        row_cell_values: List[List[str]],
        row_descriptions: List[str],
        delimiter: str,
        get_record_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
        get_user_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
        relations_and_people_list: Optional[List[Tuple[List[str], List[str]]]] = None,
    ) -> None:
        """
        Create TABLE_ROW blocks for all data rows and add them to blocks list.
        Mutates blocks and table_group.children in place.
        
        Args:
            blocks: List to append blocks to
            table_group: Table BlockGroup to add children to
            data_source_rows: Raw Notion page data for each row
            row_cell_values: Extracted cell values for each row
            row_descriptions: LLM-generated descriptions for each row
            delimiter: Delimiter for joining cell values
            get_record_child_callback: Optional callback to get ChildRecord for a record
            get_user_child_callback: Optional callback to get ChildRecord for a user
            relations_and_people_list: List of (relation_page_ids, people_user_ids) tuples for each row
        """
        for row_num, (page, cell_values) in enumerate(
            zip(data_source_rows, row_cell_values), start=2
        ):
            page_properties = page.get("properties", {})
            
            # Extract title property as source_name
            source_name = None
            for prop in page_properties.values():
                if prop.get("type") == "title":
                    title_arr = prop.get("title", [])
                    source_name = "".join([t.get("plain_text", "") for t in title_arr])
                    break
            
            # Use LLM description if available, otherwise delimiter-joined cells
            if row_descriptions and (row_num - 2) < len(row_descriptions):
                row_natural_language_text = row_descriptions[row_num - 2]
            else:
                row_natural_language_text = delimiter.join(cell_values)
            
            # Create row block
            row_block = Block(
                id=str(uuid4()),
                index=len(blocks),
                parent_index=0,
                type=BlockType.TABLE_ROW,
                data={
                    "row_natural_language_text": row_natural_language_text,
                    "row_number": row_num,
                    "row": json.dumps({
                        "cells": cell_values,
                        "source_name": source_name,
                        "page_id": page.get("id"),
                    }),
                    "cells": cell_values,
                },
                format=DataFormat.JSON,
                source_id=page.get("id"),
                source_creation_date=self._parse_timestamp(page.get("created_time")),
                source_update_date=self._parse_timestamp(page.get("last_edited_time")),
                source_name=source_name,
                weburl=page.get("url"),
                table_row_metadata=TableRowMetadata(
                    row_number=row_num,
                    is_header=False,
                ),
            )
            
            # Collect all child records (row page + relations + people)
            all_children_records = []
            
            # 1. Fetch the row page's own record
            if get_record_child_callback and page.get("id"):
                try:
                    row_page_record = await get_record_child_callback(page.get("id"))
                    if row_page_record:
                        all_children_records.append(row_page_record)
                except Exception as e:
                    self.logger.warning(f"Failed to fetch record info for row {page.get('id')}: {e}")
            
            # 2. Resolve relation pages and users
            if relations_and_people_list and (row_num - 2) < len(relations_and_people_list):
                relation_page_ids, people_user_ids = relations_and_people_list[row_num - 2]
                
                # Resolve relation pages
                if relation_page_ids and get_record_child_callback:
                    for page_id in relation_page_ids:
                        try:
                            child_record = await get_record_child_callback(page_id)
                            if child_record:
                                all_children_records.append(child_record)
                        except Exception as e:
                            self.logger.warning(f"Failed to resolve relation page {page_id}: {e}")
                
                # Resolve people users
                if people_user_ids and get_user_child_callback:
                    for user_id in people_user_ids:
                        try:
                            child_record = await get_user_child_callback(user_id)
                            if child_record:
                                all_children_records.append(child_record)
                        except Exception as e:
                            self.logger.warning(f"Failed to resolve user {user_id}: {e}")
            
            # Populate children_records if we have any
            if all_children_records:
                if not row_block.table_row_metadata:
                    row_block.table_row_metadata = TableRowMetadata()
                row_block.table_row_metadata.children_records = all_children_records
                self.logger.debug(
                    f"📎 Row {row_num} has {len(all_children_records)} child record(s): "
                    f"{len([c for c in all_children_records if c.child_type == ChildType.RECORD])} records, "
                    f"{len([c for c in all_children_records if c.child_type == ChildType.USER])} users"
                )
            
            blocks.append(row_block)
            table_group.children.append(BlockContainerIndex(block_index=row_block.index))

    async def _extract_property_value_with_resolution(
        self,
        prop: Dict[str, Any],
        get_record_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
        get_user_child_callback: Optional[
            Callable[[str], Awaitable[Optional[ChildRecord]]]
        ] = None,
    ) -> str:
        """
        Extract property value with resolution of relation pages and people to names.
        
        Args:
            prop: Notion property object
            get_record_child_callback: Optional callback to get ChildRecord for a record
            get_user_child_callback: Optional callback to get ChildRecord for a user
            
        Returns:
            String representation with resolved names
        """
        prop_type = prop.get("type", "")
        
        if prop_type == "relation":
            relations = prop.get("relation", [])
            if not relations:
                return ""
            
            if get_record_child_callback:
                # Resolve page titles from ChildRecord
                titles = []
                for rel in relations:
                    page_id = rel.get("id")
                    if page_id:
                        child_record = await get_record_child_callback(page_id)
                        title = child_record.record_name if child_record else page_id
                        titles.append(title)
                return ", ".join(titles)
            else:
                # Fallback to IDs
                return ", ".join([r.get("id", "") for r in relations if r.get("id")])
        
        elif prop_type == "people":
            people = prop.get("people", [])
            if not people:
                return ""
            
            if get_user_child_callback:
                # Resolve user names from ChildRecord
                names = []
                for person in people:
                    user_id = person.get("id")
                    if user_id:
                        child_record = await get_user_child_callback(user_id)
                        name = child_record.user_name if child_record else user_id
                        names.append(name)
                return ", ".join(names)
            else:
                # Fallback to IDs
                return ", ".join([p.get("id", "") for p in people if p.get("id")])
        
        elif prop_type == "rollup":
            rollup = prop.get("rollup", {})
            rollup_type = rollup.get("type", "")
            if rollup_type == "array":
                arr = rollup.get("array", [])
                values = []
                for item in arr:
                    if isinstance(item, dict):
                        item_type = item.get("type", "")
                        if item_type == "people" and get_user_child_callback:
                            # Handle people in rollup
                            people_list = item.get("people", [])
                            people_names = []
                            for person in people_list:
                                user_id = person.get("id")
                                if user_id:
                                    child_record = await get_user_child_callback(user_id)
                                    name = child_record.user_name if child_record else user_id
                                    people_names.append(name)
                            if people_names:
                                values.append(", ".join(people_names))
                        elif item_type == "relation" and get_record_child_callback:
                            # Handle relations in rollup
                            rel_id = item.get("id")
                            if rel_id:
                                child_record = await get_record_child_callback(rel_id)
                                title = child_record.record_name if child_record else rel_id
                                values.append(title)
                        else:
                            # Fallback to recursive extraction
                            value = self._extract_property_value(item)
                            if value:
                                values.append(value)
                return ", ".join(filter(None, values))
            else:
                # For non-array rollups, use regular extraction
                return self._extract_property_value(prop)
        else:
            # For all other types, use regular extraction
            return self._extract_property_value(prop)

    def _extract_property_value(self, prop: Dict[str, Any]) -> str:
        """
        Extract a displayable value from a Notion property.

        Handles all standard Notion property types and converts them to 
        markdown-friendly text strings.

        Args:
            prop: Notion property object with type and value

        Returns:
            String representation of the property value
        """
        prop_type = prop.get("type", "")
        
        try:
            if prop_type == "title":
                title_arr = prop.get("title", [])
                return "".join([t.get("plain_text", "") for t in title_arr])
            
            elif prop_type == "rich_text":
                rich_text_arr = prop.get("rich_text", [])
                return "".join([t.get("plain_text", "") for t in rich_text_arr])
            
            elif prop_type == "number":
                num = prop.get("number")
                return str(num) if num is not None else ""
            
            elif prop_type == "select":
                select = prop.get("select")
                return select.get("name", "") if select else ""
            
            elif prop_type == "multi_select":
                options = prop.get("multi_select", [])
                return ", ".join([opt.get("name", "") for opt in options])
            
            elif prop_type == "status":
                status = prop.get("status")
                return status.get("name", "") if status else ""
            
            elif prop_type == "date":
                date_obj = prop.get("date")
                if date_obj:
                    start = date_obj.get("start", "")
                    end = date_obj.get("end", "")
                    return f"{start} - {end}" if end else start
                return ""
            
            elif prop_type == "people":
                people = prop.get("people", [])
                # Return user IDs since we may not have names
                return ", ".join([p.get("id", "") for p in people])
            
            elif prop_type == "relation":
                relations = prop.get("relation", [])
                return ", ".join([r.get("id", "") for r in relations])
            
            elif prop_type == "checkbox":
                return "✓" if prop.get("checkbox") else "✗"
            
            elif prop_type == "url":
                return prop.get("url", "") or ""
            
            elif prop_type == "email":
                return prop.get("email", "") or ""
            
            elif prop_type == "phone_number":
                return prop.get("phone_number", "") or ""
            
            elif prop_type == "formula":
                formula = prop.get("formula", {})
                formula_type = formula.get("type", "")
                if formula_type == "string":
                    return formula.get("string", "") or ""
                elif formula_type == "number":
                    num = formula.get("number")
                    return str(num) if num is not None else ""
                elif formula_type == "boolean":
                    return "✓" if formula.get("boolean") else "✗"
                elif formula_type == "date":
                    date_obj = formula.get("date")
                    return date_obj.get("start", "") if date_obj else ""
                return ""
            
            elif prop_type == "rollup":
                rollup = prop.get("rollup", {})
                rollup_type = rollup.get("type", "")
                if rollup_type == "number":
                    num = rollup.get("number")
                    return str(num) if num is not None else ""
                elif rollup_type == "array":
                    # For arrays, recursively extract values
                    arr = rollup.get("array", [])
                    values = [self._extract_property_value(item) for item in arr]
                    return ", ".join(filter(None, values))
                return ""
            
            elif prop_type == "created_time":
                return prop.get("created_time", "") or ""
            
            elif prop_type == "created_by":
                user = prop.get("created_by", {})
                return user.get("id", "")
            
            elif prop_type == "last_edited_time":
                return prop.get("last_edited_time", "") or ""
            
            elif prop_type == "last_edited_by":
                user = prop.get("last_edited_by", {})
                return user.get("id", "")
            
            elif prop_type == "files":
                files = prop.get("files", [])
                file_names = []
                for f in files:
                    file_names.append(f.get("name", "") or f.get("external", {}).get("url", ""))
                return ", ".join(filter(None, file_names))
            
            else:
                # Unknown type - try to stringify
                return str(prop.get(prop_type, ""))
                
        except Exception as e:
            self.logger.debug(f"Error extracting property value for type {prop_type}: {e}")
            return ""

    def _generate_data_source_markdown(
        self,
        column_names: List[str],
        data_source_rows: List[Dict[str, Any]]
    ) -> str:
        """
        Generate markdown table from data source rows.
        
        Args:
            column_names: List of column names
            data_source_rows: List of row data from Notion API
            
        Returns:
            Markdown table string
        """
        if not column_names:
            return ""
        
        lines = []
        
        # Header row
        escaped_headers = [col.replace("|", "\\|") for col in column_names]
        lines.append("| " + " | ".join(escaped_headers) + " |")
        lines.append("|" + "|".join([" --- " for _ in column_names]) + "|")
        
        # Data rows (limit to first 100 for performance)
        for page in data_source_rows[:100]:
            page_properties = page.get("properties", {})
            cell_values = []
            for col_name in column_names:
                prop = page_properties.get(col_name, {})
                cell_value = self._extract_property_value(prop)
                # Escape pipes and limit cell length
                cell_value = cell_value.replace("|", "\\|")[:200]
                cell_values.append(cell_value)
            lines.append("| " + " | ".join(cell_values) + " |")
        
        if len(data_source_rows) > 100:
            lines.append(f"| ... ({len(data_source_rows) - 100} more rows) |")
        
        return "\n".join(lines)

    # ============================================================================
    # Special Block Parsers
    # ============================================================================

    async def _parse_equation(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """Parse equation block."""
        expression = type_data.get("expression", "")
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.CODE,
            format=DataFormat.TXT,
            data=f"$${expression}$$",
            code_metadata=CodeMetadata(
                language="latex",
                is_executable=False,
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_breadcrumb(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Optional[Block]:
        """
        Parse breadcrumb block (skip - navigation only).
        
        Breadcrumbs are navigation elements and don't contain meaningful content
        for indexing purposes.
        """
        return None

    async def _parse_table_of_contents(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Optional[Block]:
        """
        Parse table_of_contents block (skip - generated content).
        
        Table of contents is auto-generated and doesn't need to be indexed
        as it would be redundant with the actual headings.
        """
        return None

    async def _parse_link_to_page(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Block:
        """
        Parse link_to_page block.
        
        Notion's link_to_page can reference either a page or a database.
        The type discriminator determines which ID field to read.
        """
        link_type = type_data.get("type", "page_id")
        target_id = ""
        
        if link_type == "page_id":
            target_id = type_data.get("page_id", "")
        elif link_type == "database_id":
            target_id = type_data.get("database_id", "")
        else:
            # Fallback: try both
            target_id = type_data.get("page_id") or type_data.get("database_id", "")
        
        return Block(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=BlockType.LINK,
            format=DataFormat.TXT,
            data=target_id,
            link_metadata=LinkMetadata(
                link_text=target_id,
                link_type="internal",
                link_target=target_id,
            ),
            source_id=notion_block.get("id"),
            weburl=self._normalize_url(
                self._construct_block_url(parent_page_url, notion_block.get("id"))
            ),
            source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
            source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
        )

    async def _parse_synced_block(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> BlockGroup:
        """
        Parse synced_block as BlockGroup.
        
        Synced blocks in Notion are containers that reference content from another block.
        They must be BlockGroups to preserve hierarchy and allow children to be attached.
        
        Tracks the synced_from reference to avoid duplicate indexing of the same content.
        """
        # Extract synced_from reference if available
        synced_from = type_data.get("synced_from", None)
        synced_from_id = None
        if synced_from and isinstance(synced_from, dict):
            synced_from_type = synced_from.get("type", "")
            if synced_from_type == "block_id":
                synced_from_id = synced_from.get("block_id", "")
        
        description = "Synced Block Container"
        if synced_from_id:
            description = f"Synced Block (from: {synced_from_id})"
        
        # Store synced_from in data for downstream deduplication
        data = {"synced_from": synced_from_id} if synced_from_id else None
        
        return BlockGroup(
            id=str(uuid4()),
            index=block_index,
            parent_index=parent_group_index,
            type=GroupType.INLINE,
            source_group_id=notion_block.get("id"),
            description=description,
            data=data,
        )

    # ============================================================================
    # Fallback Parser
    # ============================================================================

    async def _parse_unknown(
        self,
        notion_block: Dict[str, Any],
        type_data: Dict[str, Any],
        parent_group_index: Optional[int],
        block_index: int,
        parent_page_url: Optional[str] = None,
    ) -> Optional[Block]:
        """
        Fallback parser for unknown block types.
        Attempts to extract any text content gracefully.
        
        Logs the full type_data for debugging when new block types are encountered.
        """
        import json
        block_type = notion_block.get("type", "unknown")
        block_id = notion_block.get("id", "")
        
        # Log with full type_data for debugging future block types
        type_data_json = json.dumps(type_data, default=str, ensure_ascii=False)
        self.logger.warning(
            f"Unknown Notion block type: {block_type} (id: {block_id}), "
            f"attempting graceful parsing. Type data: {type_data_json}"
        )
        
        # Try to find any rich_text fields
        if isinstance(type_data, dict):
            for key in ["rich_text", "text", "content"]:
                if key in type_data:
                    rich_text = type_data[key]
                    if isinstance(rich_text, list):
                        text = self.extract_rich_text(rich_text)
                        if text:
                            return Block(
                                id=str(uuid4()),
                                index=block_index,
                                parent_index=parent_group_index,
                                type=BlockType.TEXT,
                                format=DataFormat.MARKDOWN,
                                data=text,
                                source_id=block_id,
                                weburl=self._normalize_url(
                                    self._construct_block_url(parent_page_url, block_id)
                                ),
                                source_creation_date=self._parse_timestamp(notion_block.get("created_time")),
                                source_update_date=self._parse_timestamp(notion_block.get("last_edited_time")),
                            )
        
        # If no text found, return None (skip block)
        self.logger.debug(f"Skipping unknown block {block_id} - no extractable text content")
        return None

    # ============================================================================
    # Helper Methods
    # ============================================================================

    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse Notion timestamp string to datetime object."""
        if not timestamp_str:
            return None
        
        try:
            # Notion timestamps are ISO 8601 format: "2025-12-15T09:52:00.000Z"
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except Exception:
            return None

