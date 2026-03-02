"""
Confluence Cloud Connector

This connector syncs Confluence Cloud data including:
- Spaces with permissions
- Pages with content and metadata
- Users and their access

Authentication: OAuth 2.0 (3-legged OAuth)
"""

import base64
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import ConfluenceApp
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.confluence.confluence import (
    ConfluenceClient as ExternalConfluenceClient,
)
from app.sources.external.confluence.confluence import ConfluenceDataSource
from app.utils.streaming import create_stream_record_response

# Confluence Cloud OAuth URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# Time offset (in hours) applied to date filters to handle timezone differences
# between the application and Confluence server, ensuring no data is missed during sync
TIME_OFFSET_HOURS = 24

# Expand parameters for fetching pages and blogposts with required metadata
# Includes: ancestors, history, space, attachments, and comments
CONTENT_EXPAND_PARAMS = (
    "ancestors,"
    "history.lastUpdated,"
    "space,"
    "children.attachment,"
    "children.attachment.history.lastUpdated,"
    "children.attachment.version,"
    "childTypes.comment"
)

# Constant for pseudo-user group prefix
PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"


def extract_media_from_adf(adf_content: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract all media nodes from ADF content.

    Returns list of media info dicts with:
        - id: Media ID/token
        - alt: Alt text (usually filename)
        - type: Media type (file, image, etc.)
        - width: Image width (if available)
        - height: Image height (if available)
        - collection: Media collection (if available)
    """
    if not adf_content or not isinstance(adf_content, dict):
        return []

    media_nodes: List[Dict[str, Any]] = []

    def traverse(node: Dict[str, Any]) -> None:
        """Recursively traverse ADF nodes to find media."""
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "")

        # Check if this is a media node
        if node_type == "media":
            attrs = node.get("attrs", {})
            # Get filename from multiple sources:
            # - __fileName: Used for PDFs and other files
            # - alt: Used for images (usually contains filename)
            alt_text = attrs.get("alt", "")
            internal_filename = attrs.get("__fileName", "")
            # Best filename: prefer __fileName (more reliable for files), fallback to alt
            filename = internal_filename or alt_text

            media_info = {
                "id": attrs.get("id", ""),
                "alt": alt_text,
                "filename": filename,  # Best filename for matching
                "type": attrs.get("type", "file"),
                "width": attrs.get("width"),
                "height": attrs.get("height"),
                "collection": attrs.get("collection", ""),
            }
            if media_info["id"]:  # Only add if we have an ID
                media_nodes.append(media_info)

        # Recurse into content
        if "content" in node:
            for child in node.get("content", []):
                traverse(child)

    # Start traversal from root
    if "content" in adf_content:
        for node in adf_content.get("content", []):
            traverse(node)
    else:
        traverse(adf_content)

    return media_nodes


def adf_to_text(
    adf_content: Dict[str, Any],
    media_cache: Optional[Dict[str, str]] = None,
    logger: Optional[Logger] = None
) -> str:
    """
    Convert Atlassian Document Format (ADF) to Markdown.
    Returns markdown-formatted text with headers, lists, code blocks, tables, etc.

    Args:
        adf_content: The ADF document to convert
        media_cache: Optional dict mapping media_id -> base64 data URI for embedding images
        logger: Optional logger for debug messages
    """
    if not adf_content or not isinstance(adf_content, dict):
        return ""

    text_parts: List[str] = []
    _media_cache = media_cache or {}

    def apply_text_marks(text: str, marks: List[Dict[str, Any]]) -> str:
        """Apply markdown formatting based on text marks (bold, italic, link, etc.)."""
        if not marks:
            return text

        # Process marks in reverse order (innermost first)
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
                # Markdown doesn't have underline, use emphasis
                text = f"*{text}*"
            elif mark_type == "textColor":
                # Markdown doesn't support inline colors, but we can preserve the text
                # Optionally add HTML span with color (if downstream supports HTML in markdown)
                color = attrs.get("color", "")
                if color:
                    # Use HTML span for color (some markdown renderers support this)
                    text = f'<span style="color: {color}">{text}</span>'

        return text

    def extract_list_item_content(list_item: Dict[str, Any], depth: int) -> Dict[str, str]:
        """Extract text content and nested lists from a list item.

        Returns dict with:
            - text: The main text content of the list item
            - nested: Any nested lists formatted with proper indentation
        """
        content = list_item.get("content", [])
        text_parts: List[str] = []
        nested_parts: List[str] = []

        for child in content:
            child_type = child.get("type", "")
            if child_type in ["bulletList", "orderedList", "taskList"]:
                # Nested list - extract with current depth
                nested_text = extract_text(child, depth)
                if nested_text:
                    nested_parts.append(nested_text)
            else:
                # Regular content (paragraph, text, etc.)
                child_text = extract_text(child, depth)
                if child_text:
                    text_parts.append(child_text)

        # Join text parts, clean up excessive whitespace
        main_text = " ".join(text_parts).strip()
        main_text = re.sub(r'\s+', ' ', main_text)  # Normalize whitespace

        # Join nested lists
        nested_text = "\n".join(nested_parts) if nested_parts else ""

        return {"text": main_text, "nested": nested_text}

    def extract_text(node: Dict[str, Any], list_depth: int = 0, strip_marks: bool = False) -> str:
        """Recursively extract text from ADF nodes and convert to markdown.

        Args:
            node: The ADF node to process
            list_depth: Current nesting level for lists (0 = not in list, 1+ = nested depth)
            strip_marks: If True, ignore text formatting marks (for table cells)
        """
        if not isinstance(node, dict):
            return ""

        node_type = node.get("type", "")
        text = ""
        indent = "  " * list_depth  # 2 spaces per nesting level

        if node_type == "text":
            text = node.get("text", "")
            # Skip formatting marks for table cells (they don't render well in markdown tables)
            if not strip_marks:
                marks = node.get("marks", [])
                text = apply_text_marks(text, marks)

        elif node_type == "paragraph":
            content = node.get("content", [])
            para_text = "".join(extract_text(child, list_depth, strip_marks) for child in content).strip()
            if para_text:
                # In lists or tables, paragraphs should contribute text without adding newlines
                if list_depth > 0 or strip_marks:
                    # Just return the text, no newlines - let list item/table cell handle spacing
                    text = para_text
                else:
                    # Check if paragraph contains only a list - if so, don't add extra spacing
                    has_list = any(child.get("type") in ["bulletList", "orderedList", "taskList"] for child in content)
                    if has_list:
                        # Lists already have their own spacing, don't add extra
                        text = para_text
                    else:
                        text = f"{para_text}\n\n"

        elif node_type == "heading":
            level = node.get("attrs", {}).get("level", 1)
            content = node.get("content", [])
            heading_text = "".join(extract_text(child, list_depth, strip_marks) for child in content).strip()
            if heading_text:
                if strip_marks:
                    # In tables, just return heading text without # markers
                    text = heading_text
                else:
                    text = f"{'#' * level} {heading_text}\n\n"

        elif node_type == "blockquote":
            content = node.get("content", [])
            quote_text = "".join(extract_text(child, list_depth, strip_marks) for child in content).strip()
            if quote_text:
                if strip_marks:
                    # In tables, just return the quote text
                    text = quote_text
                else:
                    # Add > to each line for proper markdown blockquote
                    quoted_lines = quote_text.split("\n")
                    quoted_lines = [f"> {line}" if line.strip() else ">" for line in quoted_lines]
                    text = "\n".join(quoted_lines) + "\n\n"

        elif node_type in ["bulletList", "unorderedList"]:
            content = node.get("content", [])
            bullet_lines: List[str] = []

            for child in content:
                child_type = child.get("type", "")

                # Extract the text content from the list item
                if child_type == "listItem":
                    # Standard structure: listItem > paragraph > text
                    item_content = extract_list_item_content(child, list_depth + 1)
                    item_text = item_content.get("text", "").strip()
                    nested_content = item_content.get("nested", "")
                else:
                    # Fallback: directly extract text from whatever node this is
                    item_text = extract_text(child, list_depth + 1, strip_marks).strip()
                    nested_content = ""

                # Add bullet marker if we have text
                if item_text:
                    bullet_line = f"{indent}- {item_text}"
                    bullet_lines.append(bullet_line)
                    if nested_content:
                        bullet_lines.append(nested_content)

            # Join all bullet items with newlines
            if bullet_lines:
                text = "\n".join(bullet_lines)
                if list_depth == 0:
                    text += "\n\n"

        # Handle both "orderedList" and "numberedList" (some variations exist)
        elif node_type in ["orderedList", "numberedList"]:
            content = node.get("content", [])
            numbered_lines: List[str] = []

            for i, child in enumerate(content, start=1):
                child_type = child.get("type", "")

                # Extract the text content from the list item
                if child_type == "listItem":
                    # Standard structure: listItem > paragraph > text
                    item_content = extract_list_item_content(child, list_depth + 1)
                    item_text = item_content.get("text", "").strip()
                    nested_content = item_content.get("nested", "")
                else:
                    # Fallback: directly extract text from whatever node this is
                    item_text = extract_text(child, list_depth + 1, strip_marks).strip()
                    nested_content = ""

                # Add number marker if we have text
                if item_text:
                    numbered_line = f"{indent}{i}. {item_text}"
                    numbered_lines.append(numbered_line)
                    if nested_content:
                        numbered_lines.append(nested_content)

            # Join all numbered items with newlines
            if numbered_lines:
                text = "\n".join(numbered_lines)
                if list_depth == 0:
                    text += "\n\n"

        elif node_type == "listItem":
            # This is handled by extract_list_item_content, but provide fallback
            content = node.get("content", [])
            text = "".join(extract_text(child, list_depth) for child in content).strip()

        elif node_type == "codeBlock":
            content = node.get("content", [])
            code_text = "".join(extract_text(child, list_depth) for child in content)
            language = node.get("attrs", {}).get("language", "")
            # Preserve code formatting - don't strip, but ensure proper code block
            text = f"```{language}\n{code_text}\n```\n\n"

        elif node_type == "inlineCode":
            text = f"`{node.get('text', '')}`"

        elif node_type == "hardBreak":
            text = "\n"

        elif node_type == "rule":
            text = "---\n\n"

        elif node_type == "media":
            attrs = node.get("attrs", {})
            media_id = attrs.get("id", "")
            alt = attrs.get("alt", "")
            title = attrs.get("title", "")

            display_text = alt or title or "attachment"

            # Check if we have base64 data for this media in cache
            if media_id and media_id in _media_cache:
                data_uri = _media_cache[media_id]
                if list_depth > 0:
                    text = f"\n![{display_text}]({data_uri})\n"
                else:
                    text = f"\n![{display_text}]({data_uri})\n\n"
            else:
                # Fallback: just show the image name/alt text
                if list_depth > 0:
                    text = f"\n![{display_text}]\n"
                else:
                    text = f"\n![{display_text}]\n\n"

        elif node_type == "mention":
            attrs = node.get("attrs", {})
            mention_text = attrs.get("text", attrs.get("id", "mention"))
            text = f"@{mention_text}"

        elif node_type == "emoji":
            attrs = node.get("attrs", {})
            short_name = attrs.get("shortName", "")
            if short_name:
                text = f":{short_name}:"
            else:
                text = attrs.get("text", "")

        elif node_type == "table":
            content = node.get("content", [])
            rows: List[str] = []
            is_first_row = True

            for row in content:
                if row.get("type") == "tableRow":
                    cells: List[str] = []
                    for cell in row.get("content", []):
                        cell_type = cell.get("type", "")
                        if cell_type in ["tableCell", "tableHeader"]:
                            # Strip marks (bold, italic, etc.) - they don't render in markdown tables
                            cell_text = extract_text(cell, list_depth, strip_marks=True).strip()
                            # Escape pipe characters in cell content
                            cell_text = cell_text.replace("|", "\\|")
                            # Replace newlines with space for markdown table compatibility
                            cell_text = cell_text.replace("\n", " ")
                            cells.append(cell_text)

                    if cells:
                        rows.append("| " + " | ".join(cells) + " |")

                        # Add header separator after first row
                        if is_first_row:
                            separator = "| " + " | ".join(["---"] * len(cells)) + " |"
                            rows.append(separator)
                            is_first_row = False

            if rows:
                text = "\n".join(rows) + "\n\n"

        elif node_type in ["tableCell", "tableHeader"]:
            content = node.get("content", [])
            # Pass strip_marks through to children
            text = "".join(extract_text(child, list_depth, strip_marks) for child in content)

        elif node_type == "panel":
            attrs = node.get("attrs", {})
            panel_type = attrs.get("panelType", "info")
            content = node.get("content", [])
            panel_text = "".join(extract_text(child, list_depth) for child in content).strip()
            if panel_text:
                # Use blockquote style for panels
                panel_lines = panel_text.split("\n")
                panel_lines = [f"> **{panel_type.upper()}**: {line}" if line.strip() else ">" for line in panel_lines]
                text = "\n".join(panel_lines) + "\n\n"

        # Media wrappers - just extract the media content
        elif node_type in ["mediaSingle", "mediaGroup"]:
            content = node.get("content", [])
            text = "".join(extract_text(child, list_depth) for child in content)

        # Smart links / inline cards
        elif node_type == "inlineCard":
            attrs = node.get("attrs", {})
            url = attrs.get("url", "")
            if url:
                text = f"[{url}]({url})"

        # Task lists (checkboxes)
        elif node_type == "taskList":
            content = node.get("content", [])
            task_items: List[str] = []
            for child in content:
                if child.get("type") == "taskItem":
                    item_text = extract_text(child, list_depth + 1).strip()
                    if item_text:
                        task_items.append(item_text)
            if task_items:
                text = "\n".join(task_items) + "\n\n"

        elif node_type == "taskItem":
            attrs = node.get("attrs", {})
            state = attrs.get("state", "TODO")
            content = node.get("content", [])
            item_text = "".join(extract_text(child, list_depth) for child in content).strip()
            checkbox = "[x]" if state == "DONE" else "[ ]"
            task_indent = "  " * (list_depth - 1) if list_depth > 0 else ""
            text = f"{task_indent}- {checkbox} {item_text}"

        # Decision lists
        elif node_type == "decisionList":
            content = node.get("content", [])
            decision_items: List[str] = []
            for child in content:
                if child.get("type") == "decisionItem":
                    item_text = extract_text(child, list_depth + 1).strip()
                    if item_text:
                        decision_items.append(item_text)
            if decision_items:
                text = "\n".join(decision_items) + "\n\n"

        elif node_type == "decisionItem":
            attrs = node.get("attrs", {})
            state = attrs.get("state", "DECIDED")
            content = node.get("content", [])
            item_text = "".join(extract_text(child, list_depth) for child in content).strip()
            marker = "✓" if state == "DECIDED" else "◇"
            decision_indent = "  " * (list_depth - 1) if list_depth > 0 else ""
            text = f"{decision_indent}{marker} {item_text}"

        # Status badges
        elif node_type == "status":
            attrs = node.get("attrs", {})
            status_text = attrs.get("text", "")
            if status_text:
                text = f"[{status_text}]"

        # Date nodes
        elif node_type == "date":
            attrs = node.get("attrs", {})
            timestamp = attrs.get("timestamp", "")
            if timestamp:
                try:
                    # Convert timestamp to readable date
                    dt = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
                    text = dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    text = timestamp

        # Expand/collapsible sections
        elif node_type in ["expand", "nestedExpand"]:
            attrs = node.get("attrs", {})
            title = attrs.get("title", "Details")
            content = node.get("content", [])
            expand_text = "".join(extract_text(child, list_depth) for child in content).strip()
            if expand_text:
                text = f"**{title}**\n{expand_text}\n\n"

        # Layout containers - just extract content
        elif node_type == "layoutSection":
            content = node.get("content", [])
            column_texts: List[str] = []
            for child in content:
                child_text = extract_text(child, list_depth).strip()
                if child_text:
                    column_texts.append(child_text)
            if column_texts:
                text = "\n\n".join(column_texts) + "\n\n"

        elif node_type == "layoutColumn":
            content = node.get("content", [])
            text = "".join(extract_text(child, list_depth) for child in content)

        # Placeholder nodes - just show placeholder text
        elif node_type == "placeholder":
            attrs = node.get("attrs", {})
            text = attrs.get("text", "")

        # Extension nodes (Confluence-specific) - handle nested ADF
        elif node_type == "extension":
            attrs = node.get("attrs", {})
            extension_type = attrs.get("extensionType", "")
            extension_key = attrs.get("extensionKey", "")
            parameters = attrs.get("parameters", {})

            # Handle nested ADF in extension parameters (e.g., nested tables)
            nested_adf_str = parameters.get("adf")
            if nested_adf_str:
                try:
                    nested_adf = json.loads(nested_adf_str) if isinstance(nested_adf_str, str) else nested_adf_str
                    # Recursively parse nested ADF
                    nested_text = extract_text(nested_adf, list_depth, strip_marks)
                    if nested_text:
                        text = nested_text
                except Exception as e:
                    if logger:
                        logger.debug(f"Failed to parse nested ADF in extension: {e}")
                    # Fallback: try to extract from content
                    content = node.get("content", [])
                    text = "".join(extract_text(child, list_depth, strip_marks) for child in content)
            else:
                # No nested ADF - extract from content or show extension info
                content = node.get("content", [])
                if content:
                    text = "".join(extract_text(child, list_depth, strip_marks) for child in content)
                else:
                    # Show extension type as placeholder
                    text = f"[Extension: {extension_key or extension_type}]"

        # Generic fallback for any node with content
        elif "content" in node:
            content = node.get("content", [])
            text = "".join(extract_text(child, list_depth, strip_marks) for child in content)

        return text

    if "content" in adf_content:
        for node in adf_content.get("content", []):
            text = extract_text(node)
            if text:
                text_parts.append(text)
    else:
        text = extract_text(adf_content)
        if text:
            text_parts.append(text)

    result = "".join(text_parts)
    # Clean up excessive newlines (more than 2 consecutive)
    result = re.sub(r'\n{3,}', '\n\n', result)
    # Remove trailing whitespace from lines
    result = "\n".join(line.rstrip() for line in result.split("\n"))
    # Clean up spacing around lists - remove blank lines before lists
    # This helps when paragraphs contain lists - ensure lists start without extra spacing
    result = re.sub(r'\n\n+(\d+\. )', r'\n\1', result)  # Remove extra newlines before numbered list items
    result = re.sub(r'\n\n+(- )', r'\n\1', result)  # Remove extra newlines before bullet list items
    # Clean up spacing between list items (should be single newline)
    result = re.sub(r'(\n\d+\. .+)\n\n+(\d+\. )', r'\1\n\2', result)  # Between numbered items
    result = re.sub(r'(\n- .+)\n\n+(- )', r'\1\n\2', result)  # Between bullet items

    return result.strip()


async def adf_to_text_with_images(
    adf_content: Dict[str, Any],
    media_fetcher: Callable[[str, str], Awaitable[Optional[str]]],
    logger: Optional[Logger] = None
) -> str:
    """
    Convert Atlassian Document Format (ADF) to Markdown with embedded images.

    This async version fetches media content and embeds it as base64 data URIs.
    Used for streaming content that needs to be indexed by multimodal models.

    Args:
        adf_content: The ADF document to convert
        media_fetcher: Async callback that takes (media_id, alt_text) and returns
                      base64 data URI string or None if fetch fails
        logger: Optional logger for debug messages

    Returns:
        Markdown text with images embedded as base64 data URIs
    """
    if not adf_content or not isinstance(adf_content, dict):
        return ""

    # Extract all media nodes and fetch their content
    media_nodes = extract_media_from_adf(adf_content)
    media_cache: Dict[str, str] = {}

    # Fetch all media (sequentially to avoid rate limits)
    for media_info in media_nodes:
        media_id = media_info.get("id", "")
        alt_text = media_info.get("alt", "")
        if media_id:
            try:
                data_uri = await media_fetcher(media_id, alt_text)
                if data_uri:
                    media_cache[media_id] = data_uri
            except Exception as e:
                if logger:
                    logger.debug(f"Failed to fetch media {media_id} for embedding: {e}")

    # Reuse the main adf_to_text function with the media cache
    return adf_to_text(adf_content, media_cache, logger)


@ConnectorBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_description("Sync pages, spaces, and users from Confluence Cloud")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Confluence",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Confluence",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=AtlassianScope.get_confluence_read_access(),
                agent=AtlassianScope.get_confluence_read_access()
            ),
            fields=[
                CommonFields.client_id("Atlassian OAuth App"),
                CommonFields.client_secret("Atlassian OAuth App")
            ],
            icon_path="/assets/icons/connectors/confluence.svg",
            app_group="Atlassian",
            app_description="OAuth application for accessing Confluence Cloud API and collaboration features",
            app_categories=["Knowledge Management", "Collaboration"]
        ),
        # AuthBuilder.type(AuthType.API_TOKEN).fields([
        #     CommonFields.api_token("Atlassian API Token")
        # ])
    ])\
    .with_info("⚠️ Important: In order for users to get access to Confluence data, each user needs to make their email visible in their Confluence account settings. Users can do this by going to their Confluence profile settings and switching email visibility to Public.")\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/confluence.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Confluence Cloud OAuth Setup",
            "https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/confluence/confluence',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="space_keys",
            display_name="Space Name",
            description="Filter pages and blogposts by space name",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="page_ids",
            display_name="Page Name",
            description="Filter specific pages by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="blogpost_ids",
            display_name="Blogpost Name",
            description="Filter specific blogposts by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter pages and blogposts by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter pages and blogposts by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # Indexing filters - Pages
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_attachments",
            display_name="Index Page Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_comments",
            display_name="Index Page Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page comments",
            default_value=True
        ))
        # Indexing filters - Blogposts
        .add_filter_field(FilterField(
            name="blogposts",
            display_name="Index Blogposts",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogposts",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_attachments",
            display_name="Index Blogpost Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_comments",
            display_name="Index Blogpost Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost comments",
            default_value=True
        ))
    )\
    .build_decorator()
class ConfluenceConnector(BaseConnector):
    """
    Confluence Cloud Connector

    This connector syncs Confluence Cloud data including:
    - Spaces with permissions
    - Pages with content and metadata
    - Users and their access

    Authentication: OAuth 2.0 (3LO - 3-legged OAuth)
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        """Initialize the Confluence connector."""
        super().__init__(
            ConfluenceApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

        # Client instances
        self.external_client: Optional[ExternalConfluenceClient] = None
        self.data_source: Optional[ConfluenceDataSource] = None
        self.connector_id: str = connector_id

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.pages_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.audit_log_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def init(self) -> bool:
        """Initialize the Confluence connector with credentials and client."""
        try:
            self.logger.info("🔧 Initializing Confluence Cloud Connector...")

            # Build client from services (handles config loading, token, base URL internally)
            self.external_client = await ExternalConfluenceClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Initialize data source
            self.data_source = ConfluenceDataSource(self.external_client)

            self.logger.info("✅ Confluence connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Confluence connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ConfluenceDataSource:
        """
        Get ConfluenceDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            ConfluenceDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Confluence client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Confluence configuration not found")

        # Extract fresh OAuth access token
        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return ConfluenceDataSource(self.external_client)

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Confluence API."""
        try:
            if not self.external_client:
                self.logger.error("External client not initialized")
                return False

            # Test by fetching spaces with a limit of 1
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_spaces(
                limit=1
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.error(f"Connection test failed with status: {response.status if response else 'No response'}")
                return False

            self.logger.info("✅ Confluence connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run full synchronization of Confluence Cloud data.

        Sync order:
        1. Users and Groups (global, includes group memberships)
        2. Spaces
            - Permissions
        3. Pages (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        4. Blogposts (per space)
            - Permissions
            - Attachments
            - Comments (inline, footer)
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Confluence Cloud sync for org: {org_id}")

            # Ensure client is initialized
            if not self.external_client or not self.data_source:
                raise Exception("Confluence client not initialized. Call init() first.")

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "confluence", self.connector_id, self.logger
            )

            # Step 1: Sync users
            await self._sync_users()

            # Step 2: Sync groups and memberships
            await self._sync_user_groups()

            # Step 3: Sync spaces
            spaces = await self._sync_spaces()

            # Step 4: Sync pages and blogposts per space
            for space in spaces:
                space_key = space.short_name

                # Sync pages (with attachments, comments, permissions)
                self.logger.info(f"Syncing pages for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_PAGE)

                # Sync blogposts (with attachments, comments, permissions)
                self.logger.info(f"Syncing blogposts for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_BLOGPOST)

            # Step 5: Sync permission changes from audit log
            # This catches permission changes that don't update content's lastModified
            await self._sync_permission_changes_from_audit_log()

            self.logger.info("✅ Confluence sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during Confluence sync: {e}", exc_info=True)
            raise

    async def _sync_users(self) -> None:
        """
        Sync users from Confluence using offset-based pagination.

        Uses CQL search: type=user
        Filters out users without email addresses.
        """
        try:
            self.logger.info("Starting user synchronization...")

            # Pagination variables
            batch_size = 100
            start = 0
            total_synced = 0
            total_skipped = 0

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_users(
                    cql="type=user",
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch users: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                users_data = response_data.get("results", [])

                if not users_data:
                    break

                # Transform users (skip users without email)
                app_users = []
                for user_result in users_data:
                    # Flatten: merge nested 'user' dict with top-level fields
                    user_data = {**user_result.get("user", {}), **{k: v for k, v in user_result.items() if k != "user"}}

                    # Skip if no email
                    email = user_data.get("email", "").strip()
                    if not email:
                        self.logger.warning(f"Skipping user creation with name : {user_data.get('displayName')}, Reason: No email found for the user")
                        total_skipped += 1
                        continue

                    app_user = self._transform_to_app_user(user_data)
                    if app_user:
                        app_users.append(app_user)

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)
                    self.logger.info(f"Synced {len(app_users)} users (batch starting at {start})")

                    # For each user with email, migrate pseudo-group permissions (Confluence-specific)
                    for user in app_users:
                        if user.email and "@" in user.email and user.source_user_id:
                            try:
                                await self.data_entities_processor.migrate_group_to_user_by_external_id(
                                    group_external_id=user.source_user_id,
                                    user_email=user.email,
                                    connector_id=self.connector_id
                                )
                            except Exception as e:
                                # Log error but continue with other users
                                self.logger.warning(
                                    f"Failed to migrate pseudo-group permissions for user {user.email}: {e}",
                                    exc_info=True
                                )
                                continue

                # Move to next page
                start += batch_size

                # Check if we've reached the end
                # FIX: Do not rely on totalSize as it returns incorrect values (e.g. 100) for /search/user
                # Instead, stop if we received fewer results than requested
                if len(users_data) < batch_size:
                    break

            self.logger.info(f"✅ User sync complete. Synced: {total_synced}, Skipped (no email): {total_skipped}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self) -> None:
        """
        Sync user groups and their memberships from Confluence.

        Steps:
        1. Fetch all groups with pagination
        2. For each group, fetch all members with pagination
        3. Create group and membership records
        """
        try:
            self.logger.info("Starting user group synchronization...")

            # Pagination variables for groups
            batch_size = 50
            start = 0
            total_groups_synced = 0
            total_memberships_synced = 0

            # Paginate through all groups
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_groups(
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch groups: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                groups_data = response_data.get("results", [])

                if not groups_data:
                    break

                # Process each group and its members
                for group_data in groups_data:
                    try:
                        group_id = group_data.get("id")
                        group_name = group_data.get("name")

                        if not group_id or not group_name:
                            continue

                        self.logger.debug(f"  Processing group: {group_name} ({group_id})")

                        # Fetch members for this group
                        member_emails = await self._fetch_group_members(group_id, group_name)

                        # Create user group
                        user_group = self._transform_to_user_group(group_data)
                        if not user_group:
                            continue

                        # Get AppUser objects for members
                        app_users = await self._get_app_users_by_emails(member_emails)

                        # Save group with members
                        await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])
                        total_groups_synced += 1
                        total_memberships_synced += len(app_users)
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")

                    except Exception as group_error:
                        self.logger.error(f"❌ Failed to process group {group_data.get('name')}: {group_error}")
                        continue

                # Move to next page
                start += batch_size

                # Check if we have more groups
                size = response_data.get("size", 0)
                if size < batch_size:
                    break

            self.logger.info(f"✅ Group sync complete. Groups: {total_groups_synced}, Memberships: {total_memberships_synced}")

        except Exception as e:
            self.logger.error(f"❌ Group sync failed: {e}", exc_info=True)
            raise

    async def _sync_spaces(self) -> List[RecordGroup]:
        """
        Sync spaces from Confluence with permissions using cursor-based pagination.

        Steps:
        1. Fetch all spaces with cursor pagination
        2. Apply exclusion filters if NOT_IN operator is used
        3. For each space, fetch permissions
        4. Create RecordGroup with Permission objects
        """
        try:
            self.logger.info("Starting space synchronization...")

            # Get sync filter values for API
            space_keys_filter = self.sync_filters.get(SyncFilterKey.SPACE_KEYS)
            included_space_keys = None
            excluded_space_keys = None

            # Determine filter mode
            if space_keys_filter is not None:
                filter_operator = space_keys_filter.get_operator()
                if filter_operator == FilterOperator.IN:
                    included_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to include space keys: {included_space_keys}")
                elif filter_operator == FilterOperator.NOT_IN:
                    excluded_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to exclude space keys: {excluded_space_keys}")

            # Pagination variables
            batch_size = 20
            cursor = None
            total_spaces_synced = 0
            total_permissions_synced = 0
            base_url = None  # Extract from first response
            record_groups = []

            # Paginate through all spaces using cursor
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_spaces(
                    limit=batch_size,
                    cursor=cursor,
                    keys=included_space_keys  # None for NOT_IN (fetch all then filter), list for IN
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch spaces: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                spaces_data = response_data.get("results", [])

                # Extract base URL from first response
                if not base_url and response_data.get("_links", {}).get("base"):
                    base_url = response_data["_links"]["base"]
                    self.logger.debug(f"Base URL extracted: {base_url}")

                if not spaces_data:
                    break

                # Apply client-side exclusion filter if NOT_IN
                if excluded_space_keys:
                    original_count = len(spaces_data)
                    spaces_data = [
                        space for space in spaces_data
                        if space.get("key") not in excluded_space_keys
                    ]
                    filtered_count = original_count - len(spaces_data)
                    if filtered_count > 0:
                        self.logger.debug(f"Filtered out {filtered_count} excluded spaces from batch")

                # Process each space
                record_groups_with_permissions = []
                for space_data in spaces_data:
                    try:
                        space_id = space_data.get("id")
                        space_name = space_data.get("name")

                        if not space_id or not space_name:
                            continue

                        self.logger.debug(f"Processing space: {space_name} ({space_id})")

                        # Fetch permissions for this space
                        permissions = await self._fetch_space_permissions(space_id, space_name)
                        total_permissions_synced += len(permissions)

                        # Create RecordGroup for space
                        record_group = self._transform_to_space_record_group(space_data, base_url)
                        if not record_group:
                            continue

                        # Add to batch
                        record_groups_with_permissions.append((record_group, permissions))
                        record_groups.append(record_group)
                        total_spaces_synced += 1
                        self.logger.debug(f"Space {space_name}: {len(permissions)} permissions")

                    except Exception as space_error:
                        self.logger.error(f"❌ Failed to process space {space_data.get('name')}: {space_error}")
                        continue

                # Save batch to database
                if record_groups_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(record_groups_with_permissions)
                    self.logger.info(f"Synced batch of {len(record_groups_with_permissions)} spaces")

                # Extract next cursor from _links.next
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            self.logger.info(f"✅ Space sync complete. Spaces: {total_spaces_synced}, Permissions: {total_permissions_synced}")

            return record_groups

        except Exception as e:
            self.logger.error(f"❌ Space sync failed: {e}", exc_info=True)
            raise

    async def _sync_content(self, space_key: str, record_type: RecordType) -> None:
        """
        Unified sync for pages and blogposts from Confluence using v1 API.

        Uses cursor-based pagination with modification time filtering for incremental sync.
        Creates WebpageRecord for each content item with attachments, comments, and permissions.

        Args:
            space_key: The space key to sync content from
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
        """
        # Derive content_type from record_type for logging and sync point
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        try:
            self.logger.info(f"Starting {content_type} synchronization for space {space_key}...")
            # Get indexing filter settings based on content type (default=True means index if not configured)
            content_ids_filter = None
            if record_type == RecordType.CONFLUENCE_PAGE:
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES)
                self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS)
            else:  # CONFLUENCE_BLOGPOST
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOSTS)
                self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.BLOGPOST_IDS)

            # Get content IDs filter based on content type
            content_ids = None
            content_ids_operator_str = None
            if content_ids_filter is not None:
                content_ids = content_ids_filter.get_value()
                content_ids_operator = content_ids_filter.get_operator()
                # Extract operator value string for datasource
                content_ids_operator_str = content_ids_operator.value if hasattr(content_ids_operator, 'value') else str(content_ids_operator)
                if content_ids:
                    action = "Excluding" if content_ids_operator_str == "not_in" else "Including"
                    self.logger.info(f"🔍 Filter: {action} {content_type}s by IDs: {content_ids}")

            # Get last sync checkpoint (use content_type as suffix)
            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value, f"confluence_{content_type}s", space_key
            )
            last_sync_data = await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {last_sync_time}")

            # Build date filter parameters from sync filters
            # Get modified filter
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            modified_after = None
            modified_before = None

            if modified_filter:
                modified_after, modified_before = modified_filter.get_datetime_iso()

            # Get created filter
            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            created_after = None
            created_before = None

            if created_filter:
                created_after, created_before = created_filter.get_datetime_iso()

            # Merge modified_after with checkpoint (use the latest)
            if modified_after and last_sync_time:
                modified_after = max(modified_after, last_sync_time)
                self.logger.info(f"🔄 Using latest modified_after: {modified_after} (filter: {modified_after}, checkpoint: {last_sync_time})")
            elif modified_after:
                self.logger.info(f"🔍 Using filter: Fetching {content_type}s modified after {modified_after}")
            elif last_sync_time:
                modified_after = last_sync_time
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {modified_after}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all {content_type}s (first time)")

            # Log other filters if set
            if modified_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s modified before {modified_before}")
            if created_after:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created after {created_after}")
            if created_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created before {created_before}")

            # Pagination variables
            batch_size = 50
            cursor = None
            total_synced = 0
            total_attachments_synced = 0
            total_permissions_synced = 0

            # Paginate through all content items
            while True:
                datasource = await self._get_fresh_datasource()

                if record_type == RecordType.CONFLUENCE_PAGE:
                    response = await datasource.get_pages_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        cursor=cursor,
                        limit=batch_size,
                        space_key=space_key,
                        page_ids=content_ids,
                        page_ids_operator=content_ids_operator_str,
                        include_children=True,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )
                else:  # CONFLUENCE_BLOGPOST
                    response = await datasource.get_blogposts_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        cursor=cursor,
                        limit=batch_size,
                        space_key=space_key,
                        blogpost_ids=content_ids,
                        blogpost_ids_operator=content_ids_operator_str,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch {content_type}s: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                items_data = response_data.get("results", [])

                if not items_data:
                    break


                # Transform items to WebpageRecords with permissions
                records_with_permissions = []
                for item_data in items_data:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")

                        if not item_id or not item_title:
                            continue

                        self.logger.debug(f"Processing {content_type}: {item_title} ({item_id})")

                        # Check if record exists in DB
                        existing_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=item_id
                        )

                        # Fetch page permissions
                        permissions = await self._fetch_page_permissions(item_id)
                        total_permissions_synced += len(permissions)

                        # Transform to WebpageRecord with update tracking
                        webpage_record_update = await self._process_webpage_with_update(
                            item_data, record_type, existing_record, permissions
                        )

                        if not webpage_record_update.record:
                            continue

                        webpage_record = webpage_record_update.record

                        # Set indexing status based on filter
                        if not content_indexing_enabled:
                            webpage_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        # Only set inherit_permissions to False if there are READ restrictions
                        # EDIT-only restrictions should still inherit from space for READ access
                        read_permissions = [p for p in permissions if p.type == PermissionType.READ]
                        if len(read_permissions) > 0:
                            webpage_record.inherit_permissions = False

                        # Add item to batch
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1
                        self.logger.debug(f"{content_type.capitalize()} {item_title}: {len(permissions)} permissions")

                        # Extract space_id for children
                        space_data = item_data.get("space", {})
                        space_id = str(space_data.get("id")) if space_data.get("id") else None

                        # Get parent_node_id for dependent nodes (comments and attachments)
                        parent_node_id = webpage_record.id

                        # Process attachments - skip embedded images
                        children = item_data.get("children", {})
                        attachment_data = children.get("attachment", {})
                        attachments = attachment_data.get("results", [])

                        if attachments:

                            try:
                                embedded_image_ids: Set[str] = set()

                                try:
                                    if content_type == "page":
                                        v2_response = await datasource.get_page_attachments(
                                            id=int(item_id),
                                            status=["current"],  # Only fetch current version attachments
                                            limit=100
                                        )
                                    else:  # blogpost
                                        v2_response = await datasource.get_blogpost_attachments(
                                            id=int(item_id),
                                            status=["current"],  # Only fetch current version attachments
                                            limit=100
                                        )
                                    if v2_response and v2_response.status == HttpStatusCode.SUCCESS.valu:
                                        v2_data = v2_response.json()
                                        attachments_v2 = v2_data.get("results", [])
                                        if attachments_v2:
                                            attachments = attachments_v2
                                except Exception as v2_error:
                                    self.logger.debug(f"Error fetching v2 attachments: {v2_error}")

                                attachment_mime_types: Dict[str, str] = {}
                                for att in attachments:
                                    att_id = att.get("id")
                                    if att_id:
                                        mime_type = att.get("mediaType", "")
                                        if mime_type:
                                            attachment_mime_types[att_id] = mime_type
                                if content_type == "page":
                                    page_adf_response = await datasource.get_page_content_v2(
                                        page_id=item_id,
                                        body_format="atlas_doc_format"
                                    )
                                else:  # blogpost
                                    page_adf_response = await datasource.get_blogpost_content_v2(
                                        blogpost_id=item_id,
                                        body_format="atlas_doc_format"
                                    )

                                if not page_adf_response or page_adf_response.status != HttpStatusCode.SUCCESS.valu:
                                    raise Exception(f"Failed to fetch ADF content: status={page_adf_response.status if page_adf_response else 'None'}")

                                page_adf_data = page_adf_response.json()
                                body = page_adf_data.get("body", {})
                                adf_body = body.get("atlas_doc_format", {})
                                adf_value = adf_body.get("value")

                                if adf_value:
                                    adf_content = json.loads(adf_value) if isinstance(adf_value, str) else adf_value
                                    media_nodes = extract_media_from_adf(adf_content)

                                    for media_info in media_nodes:
                                        attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                        if attachment_id:
                                            mime_type = attachment_mime_types.get(attachment_id, "")
                                            if mime_type.startswith("image/"):
                                                embedded_image_ids.add(attachment_id)

                                try:
                                    if content_type == "page":
                                        footer_comments_response = await datasource.get_page_footer_comments(
                                            id=int(item_id),
                                            body_format="atlas_doc_format",
                                            limit=100
                                        )
                                    else:
                                        footer_comments_response = await datasource.get_blog_post_footer_comments(
                                            id=int(item_id),
                                            body_format="atlas_doc_format",
                                            limit=100
                                        )

                                    if footer_comments_response and footer_comments_response.status == HttpStatusCode.SUCCESS.valu:
                                        footer_comments_data = footer_comments_response.json()
                                        footer_comments = footer_comments_data.get("results", [])

                                        for comment in footer_comments:
                                            comment_body = comment.get("body", {})
                                            comment_adf = comment_body.get("atlas_doc_format", {})
                                            comment_adf_value = comment_adf.get("value")

                                            if comment_adf_value:
                                                comment_content = json.loads(comment_adf_value) if isinstance(comment_adf_value, str) else comment_adf_value
                                                comment_media_nodes = extract_media_from_adf(comment_content)

                                                for media_info in comment_media_nodes:
                                                    attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                                    if attachment_id:
                                                        mime_type = attachment_mime_types.get(attachment_id, "")
                                                        if mime_type.startswith("image/"):
                                                            embedded_image_ids.add(attachment_id)
                                except Exception as comment_error:
                                    self.logger.debug(f"Failed to fetch footer comments for embedded image detection: {comment_error}", exc_info=True)

                                try:
                                    if content_type == "page":
                                        inline_comments_response = await datasource.get_page_inline_comments(
                                            id=int(item_id),
                                            body_format="atlas_doc_format",
                                            limit=100
                                        )
                                    else:
                                        inline_comments_response = await datasource.get_blog_post_inline_comments(
                                            id=int(item_id),
                                            body_format="atlas_doc_format",
                                            limit=100
                                        )

                                    if inline_comments_response and inline_comments_response.status == HttpStatusCode.SUCCESS.valu:
                                        inline_comments_data = inline_comments_response.json()
                                        inline_comments = inline_comments_data.get("results", [])

                                        for comment in inline_comments:
                                            comment_body = comment.get("body", {})
                                            comment_adf = comment_body.get("atlas_doc_format", {})
                                            comment_adf_value = comment_adf.get("value")

                                            if comment_adf_value:
                                                comment_content = json.loads(comment_adf_value) if isinstance(comment_adf_value, str) else comment_adf_value
                                                comment_media_nodes = extract_media_from_adf(comment_content)

                                                for media_info in comment_media_nodes:
                                                    attachment_id = self._resolve_confluence_attachment_id(media_info, attachments)
                                                    if attachment_id:
                                                        mime_type = attachment_mime_types.get(attachment_id, "")
                                                        if mime_type.startswith("image/"):
                                                            embedded_image_ids.add(attachment_id)
                                except Exception as comment_error:
                                    self.logger.debug(f"Failed to fetch inline comments for embedded image detection: {comment_error}", exc_info=True)

                            except Exception as adf_error:
                                self.logger.warning(f"Failed to detect embedded images for {content_type} {item_id}, all attachments will be created as FileRecords: {adf_error}")
                                embedded_image_ids = set()

                            for attachment in attachments:
                                try:
                                    attachment_id = attachment.get("id")
                                    if not attachment_id:
                                        continue

                                    if attachment_id in embedded_image_ids:
                                        continue
                                    existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                                        connector_id=self.connector_id,
                                        external_record_id=attachment_id
                                    )

                                    attachment_record = self._transform_to_attachment_file_record(
                                        attachment,
                                        item_id,
                                        space_id,
                                        existing_record=existing_attachment,
                                        parent_node_id=parent_node_id
                                    )

                                    if attachment_record:
                                        if not content_attachments_indexing_enabled:
                                            attachment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                                        records_with_permissions.append((attachment_record, permissions))
                                        total_attachments_synced += 1

                                except Exception as att_error:
                                    self.logger.error(f"Failed to process attachment: {att_error}")
                                    continue

                    except Exception as item_error:
                        self.logger.error(f"Failed to process {content_type} {item_data.get('title')}: {item_error}")
                        continue

                # Save batch to database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Synced batch of {len(records_with_permissions)} items ({content_type}s + attachments)")

                # Extract next cursor from response
                cursor_url = response_data.get("_links", {}).get("next")
                if not cursor_url:
                    break

                cursor = self._extract_cursor_from_next_link(cursor_url)
                if not cursor:
                    break

            # Update sync checkpoint with current time (only if we synced something)
            # Using current time instead of last item's time avoids re-fetching due to the 24-hour offset
            if total_synced > 0:
                current_sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                await self.pages_sync_point.update_sync_point(sync_point_key, {"last_sync_time": current_sync_time})
                self.logger.info(f"Updated {content_type}s sync checkpoint to {current_sync_time}")

            self.logger.info(f"✅ {content_type.capitalize()} sync complete. {content_type.capitalize()}s: {total_synced}, Attachments: {total_attachments_synced}, Permissions: {total_permissions_synced}")

        except Exception as e:
            self.logger.error(f"❌ {content_type.capitalize()} sync failed: {e}", exc_info=True)
            raise

    async def _sync_permission_changes_from_audit_log(self) -> None:
        """
        Sync permission changes for pages/blogs using Confluence Audit Log API.

        This method tracks permission changes that don't update the content's
        lastModified timestamp, ensuring we capture:
        - Content restriction added (user/group gets access)
        - Content restriction removed (user/group loses access)

        Flow:
        1. Get last audit sync time
        2. If first run (no checkpoint): Initialize with current time and skip (permissions already synced)
        3. If subsequent run: Fetch audit logs since last sync
        4. Extract unique content titles from audit records
        5. Search content by titles and check if exists in DB
        6. For each existing content: Fetch current permissions and update
        7. Update audit log sync point with current timestamp

        Note: First run initializes checkpoint but skips permission sync because
        the initial content sync (_sync_content) already synced all permissions.
        """
        try:
            self.logger.info("🔍 Starting permission sync from audit log...")

            # Sync point key for audit log
            audit_sync_key = generate_record_sync_point_key(RecordType.WEBPAGE.value, "permissions", "audit_log")

            # Get last audit sync timestamp
            last_audit_sync = await self.audit_log_sync_point.read_sync_point(audit_sync_key)
            last_sync_time_ms = last_audit_sync.get("last_sync_time_ms") if last_audit_sync else None

            # Current time as checkpoint
            current_time_ms = int(datetime.now().timestamp() * 1000)

            # First run: Initialize checkpoint and skip (permissions already synced during content sync)
            if last_sync_time_ms is None:
                self.logger.info(
                    "🆕 First audit log sync - initializing checkpoint to current time and skipping. "
                    "Permissions already synced during content sync."
                )

                # Save initial checkpoint
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"🔄 Fetching audit logs from {last_sync_time_ms} to {current_time_ms}")

            # Fetch audit logs and extract content titles that had permission changes
            content_titles = await self._fetch_permission_audit_logs(last_sync_time_ms, current_time_ms)

            if not content_titles:
                self.logger.info("✅ No permission changes found in audit log")
                # Update sync point even if no changes
                await self.audit_log_sync_point.update_sync_point(
                    audit_sync_key,
                    {"last_sync_time_ms": current_time_ms}
                )
                return

            self.logger.info(f"📋 Found {len(content_titles)} content items with permission changes")

            # Search for content by titles and sync their permissions (only if exists in DB)
            await self._sync_content_permissions_by_titles(content_titles)

            # Update audit log sync point with current time
            await self.audit_log_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time_ms": current_time_ms}
            )

            self.logger.info("✅ Permission sync from audit log completed")

        except Exception as e:
            self.logger.error(f"❌ Permission sync from audit log failed: {e}", exc_info=True)
            raise

    async def _fetch_permission_audit_logs(
        self,
        start_date_ms: int,
        end_date_ms: int
    ) -> List[str]:
        """
        Fetch audit logs and extract content titles that had permission changes.

        Filters for:
        - category = "Permissions"
        - scope = content (pages/blogs, not global or space-level)

        Args:
            start_date_ms: Start timestamp in milliseconds
            end_date_ms: End timestamp in milliseconds

        Returns:
            List of unique content titles (pages/blogs) that had permission changes
        """
        content_titles_set: set[str] = set()
        batch_size = 100
        start = 0

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_audit_logs(
                start_date=start_date_ms,
                end_date=end_date_ms,
                start=start,
                limit=batch_size
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch audit logs: {response.status if response else 'No response'}")
                break

            response_data = response.json()
            audit_records = response_data.get("results", [])

            if not audit_records:
                break

            # Process each audit record
            for record in audit_records:
                content_title = self._extract_content_title_from_audit_record(record)
                if content_title:
                    content_titles_set.add(content_title)

            # Check for more pages
            size = response_data.get("size", 0)
            if size < batch_size:
                break

            start += batch_size

        return list(content_titles_set)

    def _extract_content_title_from_audit_record(self, record: Dict[str, Any]) -> Optional[str]:
        """
        Extract content title from an audit record if it's a content permission change.

        Filters for:
        - category = "Permissions"
        - Has a Page or Blog in associatedObjects (content-level permission)
        - Has a Space in associatedObjects (confirms it's content, not global)

        Args:
            record: Raw audit log record

        Returns:
            Content title (page/blog) or None if not a content permission change
        """
        # Must be a permission-related event
        if record.get("category") != "Permissions":
            return None

        associated_objects = record.get("associatedObjects", [])

        # Check for content-level permission (must have both content AND space)
        has_space = any(obj.get("objectType") == "Space" for obj in associated_objects)
        content_obj = next(
            (obj for obj in associated_objects if obj.get("objectType") in ["Page", "Blog"]),
            None
        )

        # Content restriction must have both page/blog AND space
        if not has_space or not content_obj:
            return None

        return content_obj.get("name")


    async def _sync_content_permissions_by_titles(self, titles: List[str]) -> None:
        """
        Search for content by titles and sync their current permissions.

        IMPORTANT: This method ONLY updates permissions for records that already exist in the database.
        It will NOT create new records, ensuring sync filters are respected.

        For each found content item:
        1. Check if record exists in DB (by external_record_id)
        2. If exists: Fetch current permissions and update
        3. If not exists: Skip (record was filtered out during initial sync)

        Args:
            titles: List of content titles to search for
        """
        if not titles:
            return

        # Batch titles to avoid CQL query size limits (process 50 at a time)
        batch_size = 50
        total_synced = 0
        total_skipped = 0
        total_permissions = 0
        has_failures = False

        for i in range(0, len(titles), batch_size):
            batch_titles = titles[i:i + batch_size]

            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_content_by_titles(
                    titles=batch_titles,
                    expand="version,space,history.lastUpdated,ancestors"
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to search content by titles: {response.status if response else 'No response'}")
                    continue

                response_data = response.json()
                content_items = response_data.get("results", [])

                if not content_items:
                    self.logger.debug(f"No content found for titles batch {i // batch_size + 1}")
                    continue

                # Process each content item
                records_with_permissions = []
                for item_data in content_items:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")
                        item_type = item_data.get("type", "").lower()

                        if not item_id or not item_title:
                            continue

                        # Determine record type
                        if item_type == "page":
                            record_type = RecordType.CONFLUENCE_PAGE
                        elif item_type == "blogpost":
                            record_type = RecordType.CONFLUENCE_BLOGPOST
                        else:
                            self.logger.debug(f"Skipping unknown content type: {item_type}")
                            continue

                        # Check if record exists in database (respects sync filters)
                        existing_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=item_id
                        )

                        if not existing_record:
                            # Record doesn't exist - it was filtered out during initial sync
                            self.logger.debug(
                                f"Skipping {item_type} '{item_title}' ({item_id}) - "
                                f"not in database (filtered out during sync)"
                            )
                            total_skipped += 1
                            continue

                        self.logger.debug(f"Updating permissions for {item_type}: {item_title} ({item_id})")

                        # Transform to WebpageRecord
                        webpage_record = self._transform_to_webpage_record(item_data, record_type)
                        if not webpage_record:
                            continue

                        # Fetch current permissions
                        permissions = await self._fetch_page_permissions(item_id)
                        total_permissions += len(permissions)

                        # Only set inherit_permissions to False if there are READ restrictions
                        # EDIT-only restrictions should still inherit from space for READ access
                        read_permissions = [p for p in permissions if p.type == PermissionType.READ]
                        if len(read_permissions) > 0:
                            webpage_record.inherit_permissions = False

                        # Add to batch for update
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1

                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to sync permissions for {item_data.get('title')}: {item_error}")
                        has_failures = True
                        continue

                # Update batch in database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Updated permissions for {len(records_with_permissions)} content items")

            except Exception as batch_error:
                self.logger.error(f"❌ Failed to process titles batch: {batch_error}")
                has_failures = True
                continue

        if has_failures:
            raise ValueError("Failed to sync permissions for some content items")

        if total_skipped > 0:
            self.logger.info(f"🔍 Skipped {total_skipped} items not in database (filtered during sync)")

        self.logger.info(f"✅ Permission sync complete. Items updated: {total_synced}, Permissions: {total_permissions}")

    async def _fetch_space_permissions(self, space_id: str, space_name: str) -> List[Permission]:
        """
        Fetch all permissions for a space with cursor-based pagination.

        Args:
            space_id: The space ID
            space_name: The space name (for logging)

        Returns:
            List of Permission objects
        """
        try:
            permissions = []
            batch_size = 100
            cursor = None

            # Paginate through space permissions
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_space_permissions_assignments(
                    id=space_id,
                    limit=batch_size,
                    cursor=cursor
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch permissions for space {space_name}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                permissions_data = response_data.get("results", [])

                if not permissions_data:
                    break

                # Transform permissions and add to list
                for perm_data in permissions_data:
                    permission = await self._transform_space_permission(perm_data)
                    if permission:
                        permissions.append(permission)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for space {space_name}: {e}")
            return []  # Return empty list on error, space will be created without permissions

    async def _fetch_page_permissions(self, page_id: str) -> List[Permission]:
        """
        Fetch permissions for a Confluence page using v1 API.

        Args:
            page_id: The page ID

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            self.logger.debug(f"Fetching permissions for page: {page_id}")

            # Fetch page restrictions using v1 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_permissions_v1(
                page_id=page_id
            )

            # Check response
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"⚠️ Failed to fetch permissions for page {page_id}: {response.status if response else 'No response'}")
                return []

            response_data = response.json()
            restrictions = response_data.get("results", [])

            # Process each restriction (read and update operations)
            for restriction_data in restrictions:
                operation_permissions = await self._transform_page_restriction_to_permissions(restriction_data)
                permissions.extend(operation_permissions)

            self.logger.debug(f"Found {len(permissions)} permissions for page {page_id}")
            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch permissions for page {page_id}: {e}")
            return []  # Return empty list on error, page will be created without permissions

    async def _fetch_comments_recursive(
        self,
        page_id: str,
        page_title: str,
        comment_type: str,
        page_permissions: List[Permission],
        parent_space_id: Optional[str],
        parent_type: str = "page",
        parent_node_id: Optional[str] = None
    ) -> List[tuple[CommentRecord, List[Permission]]]:
        """
        Recursively fetch all comments (footer or inline) for a page or blogpost.

        Fetches top-level comments and all nested replies in a flat list.
        Each comment inherits permissions from the parent.

        Args:
            page_id: The page/blogpost ID
            page_title: The page/blogpost title (for logging)
            comment_type: "footer" or "inline"
            page_permissions: Permissions inherited from parent
            parent_space_id: Space ID for external_record_group_id
            parent_type: "page" or "blogpost" (determines which API to call)
            parent_node_id: Internal record ID of parent page

        Returns:
            List of tuples (CommentRecord, permissions list)
        """
        try:
            all_comments = []
            batch_size = 100
            cursor = None

            self.logger.debug(f"Fetching {comment_type} comments for {parent_type}: {page_title}")

            # Fetch top-level comments
            while True:
                datasource = await self._get_fresh_datasource()

                # Route to correct API based on parent_type
                if parent_type == "page":
                    if comment_type == "footer":
                        response = await datasource.get_page_footer_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                    else:  # inline
                        response = await datasource.get_page_inline_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                elif parent_type == "blogpost":
                    if comment_type == "footer":
                        response = await datasource.get_blog_post_footer_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                    else:  # inline
                        response = await datasource.get_blog_post_inline_comments(
                            id=int(page_id),
                            cursor=cursor,
                            limit=batch_size,
                            body_format="storage"
                        )
                else:
                    self.logger.error(f"Unknown parent type: {parent_type}")
                    break

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch {comment_type} comments for page {page_title}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                comments_data = response_data.get("results", [])

                if not comments_data:
                    break

                # Extract base URL from response level (v2 API) - available in response_data._links.base
                response_links = response_data.get("_links", {})
                base_url = response_links.get("base")  # v2 format: base URL at response level

                # Process each comment
                for comment_data in comments_data:
                    try:
                        comment_id = comment_data.get("id")

                        if not comment_id:
                            continue

                        # Check if comment exists in DB
                        existing_comment = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=comment_id
                        )

                        # Transform comment to CommentRecord
                        comment_record = self._transform_to_comment_record(
                            comment_data,
                            page_id,
                            parent_space_id,
                            comment_type,
                            None,  # No parent comment for top-level
                            base_url=base_url,  # Pass base URL from response level
                            existing_record=existing_comment,
                            parent_node_id=parent_node_id
                        )

                        if comment_record:
                            all_comments.append((comment_record, page_permissions))

                        # Recursively fetch children
                        children = await self._fetch_comment_children_recursive(
                            comment_id,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions,
                            parent_node_id=parent_node_id
                        )
                        all_comments.extend(children)

                    except Exception as comment_error:
                        self.logger.error(f"❌ Failed to process comment {comment_data.get('id')}: {comment_error}")
                        continue

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            self.logger.debug(f"✓ Fetched {len(all_comments)} {comment_type} comments (including replies) for page {page_title}")
            return all_comments

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch {comment_type} comments for page {page_title}: {e}")
            return []

    async def _fetch_comment_children_recursive(
        self,
        comment_id: str,
        comment_type: str,
        page_id: str,
        parent_space_id: Optional[str],
        page_permissions: List[Permission],
        parent_node_id: Optional[str] = None
    ) -> List[tuple[CommentRecord, List[Permission]]]:
        """
        Recursively fetch all children (replies) of a comment.

        Args:
            comment_id: The parent comment ID
            comment_type: "footer" or "inline"
            page_id: The parent page ID
            parent_space_id: Space ID for external_record_group_id
            page_permissions: Permissions inherited from parent page
            parent_node_id: Internal record ID of parent page

        Returns:
            List of tuples (CommentRecord, permissions list)
        """
        try:
            all_children = []
            batch_size = 100
            cursor = None

            # Fetch children comments
            while True:
                datasource = await self._get_fresh_datasource()
                if comment_type == "footer":
                    response = await datasource.get_footer_comment_children(
                        id=int(comment_id),
                        cursor=cursor,
                        limit=batch_size,
                        body_format="storage"
                    )
                else:  # inline
                    response = await datasource.get_inline_comment_children(
                        id=int(comment_id),
                        cursor=cursor,
                        limit=batch_size,
                        body_format="storage"
                    )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    break

                response_data = response.json()
                children_data = response_data.get("results", [])

                if not children_data:
                    break

                # Extract base URL from response level (v2 API) - available in response_data._links.base
                response_links = response_data.get("_links", {})
                base_url = response_links.get("base")  # v2 format: base URL at response level

                # Process each child comment
                for child_data in children_data:
                    try:
                        child_id = child_data.get("id")

                        if not child_id:
                            continue

                        # Check if child comment exists in DB
                        existing_child = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=child_id
                        )

                        # Transform child to CommentRecord
                        child_record = self._transform_to_comment_record(
                            child_data,
                            page_id,
                            parent_space_id,
                            comment_type,
                            comment_id,  # Parent comment ID
                            base_url=base_url,  # Pass base URL from response level
                            existing_record=existing_child,
                            parent_node_id=parent_node_id
                        )

                        if child_record:
                            all_children.append((child_record, page_permissions))

                        # Recursively fetch grandchildren
                        grandchildren = await self._fetch_comment_children_recursive(
                            child_id,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions,
                            parent_node_id=parent_node_id
                        )
                        all_children.extend(grandchildren)

                    except Exception as child_error:
                        self.logger.error(f"❌ Failed to process child comment {child_data.get('id')}: {child_error}")
                        continue

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            return all_children

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch children for comment {comment_id}: {e}")
            return []

    def _transform_to_comment_record(
        self,
        comment_data: Dict[str, Any],
        page_id: str,
        parent_space_id: Optional[str],
        comment_type: str,
        parent_comment_id: Optional[str],
        base_url: Optional[str] = None,
        existing_record: Optional[Record] = None,
        parent_node_id: Optional[str] = None
    ) -> Optional[CommentRecord]:
        """
        Transform Confluence comment data to CommentRecord entity.

        Args:
            comment_data: Raw comment data from Confluence API
            page_id: Parent page external_record_id
            parent_space_id: Space ID from parent page
            comment_type: "footer" or "inline"
            parent_comment_id: Parent comment ID (None for top-level comments)
            base_url: Base URL from response level (v2 API) - if None, will extract from _links.self (v1 API)
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page

        Returns:
            CommentRecord object or None if transformation fails
        """
        try:
            comment_id = comment_data.get("id")
            title = comment_data.get("title", "")

            if not comment_id:
                return None

            # Extract author accountId
            author = comment_data.get("version", {}).get("authorId")
            if not author:
                self.logger.warning(f"Comment {comment_id} has no author - skipping")
                return None

            # Parse timestamps
            source_created_at = None

            created_at_str = comment_data.get("version", {}).get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Extract resolution status (for inline comments)
            resolution_status = None
            if comment_type == "inline":
                is_resolved = comment_data.get("resolutionStatus", False)
                resolution_status = "resolved" if is_resolved else "open"

            # Extract inline original selection (for inline comments)
            inline_original_selection = None
            if comment_type == "inline":
                inline_properties = comment_data.get("properties", {})
                if inline_properties:
                    inline_original_selection = inline_properties.get("inlineOriginalSelection")

            # Determine parent record ID and type
            parent_external_record_id = parent_comment_id if parent_comment_id else page_id
            parent_record_type = RecordType.COMMENT if parent_comment_id else RecordType.WEBPAGE

            # Determine record ID and version
            is_new = existing_record is None
            comment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            version_number = comment_data.get("version", {}).get("number", 0)

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            # Construct web URL for comment
            links = comment_data.get("_links", {})
            web_url = self._construct_web_url(links, base_url)

            return CommentRecord(
                id=comment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT,
                external_record_id=comment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                external_record_group_id=parent_space_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,
                weburl=web_url,
                author_source_id=author,
                resolution_status=resolution_status,
                inline_original_selection=inline_original_selection,
                is_dependent_node=True,  # Comments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent page
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform comment: {e}")
            return None

    def _construct_web_url(self, links: Dict[str, Any], base_url: Optional[str] = None) -> Optional[str]:
        """
        Construct web URL from _links dictionary.

        Supports both v1 and v2 API response formats:
        - v2 API: base_url is at response level (passed as parameter)
        - v1 API: base_url needs to be extracted from _links.self

        Args:
            links: The _links dictionary from API response
            base_url: Optional base URL from response level (v2 API)

        Returns:
            Constructed web URL or None if not possible
        """
        web_path = links.get("webui")
        if not web_path:
            return None

        # Use base_url from parameter (v2 API - from response level)
        if base_url:
            return f"{base_url}{web_path}"

        # Fall back to v1 format (extract from _links.self)
        self_link = links.get("self")
        if self_link and "https://" in self_link and "/wiki/" in self_link:
            extracted_base_url = self_link.split("/wiki/")[0] + "/wiki"
            return f"{extracted_base_url}{web_path}"

        return None

    def _extract_cursor_from_next_link(self, next_url: str) -> Optional[str]:
        """
        Extract cursor value from _links.next URL.

        Args:
            next_url: The next URL from API response
            Example: "/wiki/api/v2/spaces?limit=20&cursor=eyJ..."

        Returns:
            Cursor string or None if not found
        """
        try:
            if not next_url:
                return None

            parsed = urlparse(next_url)
            query_params = parse_qs(parsed.query)

            # Get cursor value (could be list if multiple, take first)
            cursor_values = query_params.get("cursor", [])
            if cursor_values:
                return cursor_values[0]

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to extract cursor from URL '{next_url}': {e}")
            return None

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        create_pseudo_group_if_missing: bool = False
    ) -> Optional[Permission]:
        """
        Create Permission object from principal data (user or group).

        This is a common function used by both space and page permission processing.

        Args:
            principal_type: "user" or "group"
            principal_id: accountId for users, groupId for groups
            permission_type: Mapped PermissionType enum
            create_pseudo_group_if_missing: If True and user not found, create a
                pseudo-group to preserve the permission. Used for record-level

        Returns:
            Permission object or None if principal not found in DB
        """
        try:
            if principal_type == "user":
                entity_type = EntityType.USER
                # Lookup user by source_user_id (accountId) using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_source_id(
                        source_user_id=principal_id,
                        connector_id=self.connector_id,
                    )
                    if user:
                        return Permission(
                            email=user.email,
                            type=permission_type,
                            entity_type=entity_type
                        )

                    # User not found - check if pseudo-group exists or should be created
                    if create_pseudo_group_if_missing:
                        # Check for existing pseudo-group
                        pseudo_group = await tx_store.get_user_group_by_external_id(
                            connector_id=self.connector_id,
                            external_id=principal_id,
                        )

                        if not pseudo_group:
                            # Create pseudo-group on-the-fly
                            pseudo_group = await self._create_pseudo_group(principal_id)

                        if pseudo_group:
                            self.logger.debug(
                                f"Using pseudo-group for user {principal_id} (no email available)"
                            )
                            return Permission(
                                external_id=pseudo_group.source_user_group_id,
                                type=permission_type,
                                entity_type=EntityType.GROUP
                            )

                    self.logger.debug(f"  ⚠️ User {principal_id} not found in DB, skipping permission")
                    return None

            elif principal_type == "group":
                entity_type = EntityType.GROUP
                # Lookup group by source_user_group_id using transaction store
                async with self.data_store_provider.transaction() as tx_store:
                    group = await tx_store.get_user_group_by_external_id(
                        connector_id=self.connector_id,
                        external_id=principal_id,
                    )
                    if not group:
                        self.logger.debug(f"  ⚠️ Group {principal_id} not found in DB, skipping permission")
                        return None

                    return Permission(
                        external_id=group.source_user_group_id,
                        type=permission_type,
                        entity_type=entity_type
                    )

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to create permission from principal: {e}")
            return None

    async def _create_pseudo_group(self, account_id: str) -> Optional[AppUserGroup]:
        """
        Create a pseudo-group for a user without email.

        This preserves permissions for users who don't have email addresses yet.
        The pseudo-group uses the user's accountId as source_user_group_id.

        Args:
            account_id: Confluence user accountId

        Returns:
            Created AppUserGroup or None if creation fails
        """
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX} {account_id}",
                org_id=self.data_entities_processor.org_id,
            )

            # Save to database (empty members list)
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(f"Created pseudo-group for user without email: {account_id}")

            return pseudo_group

        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    async def _transform_space_permission(self, perm_data: Dict[str, Any]) -> Optional[Permission]:
        """
        Transform Confluence space permission to Permission object.

        Maps Confluence operations to PermissionType:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER

        Args:
            perm_data: Raw permission data from Confluence API

        Returns:
            Permission object or None if invalid or user/group not found in DB
        """
        try:
            principal = perm_data.get("principal", {})
            operation = perm_data.get("operation", {})

            principal_type = principal.get("type")  # "user" or "group"
            principal_id = principal.get("id")  # accountId or groupId
            operation_key = operation.get("key")  # e.g., "read", "administer"
            target_type = operation.get("targetType")  # e.g., "space", "page"

            if not principal_type or not principal_id or not operation_key:
                return None

            # Map Confluence permission to PermissionType
            permission_type = self._map_confluence_permission(operation_key, target_type)

            # Use common function to create permission
            return await self._create_permission_from_principal(
                principal_type,
                principal_id,
                permission_type
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space permission: {e}")
            return None

    def _map_confluence_permission(self, operation_key: str, target_type: str) -> PermissionType:
        """
        Map Confluence operation to PermissionType enum.

        Mapping logic:
        - administer → OWNER
        - read → READ
        - create/delete (comment) → COMMENT
        - create/delete/archive (page/blogpost/attachment) → WRITE
        - restrict_content/export → OTHER
        - delete (space) → OWNER

        Args:
            operation_key: Operation key (e.g., "read", "create", "delete")
            target_type: Target type (e.g., "space", "page", "comment")

        Returns:
            PermissionType enum value
        """
        # Administer = OWNER
        if operation_key == "administer":
            return PermissionType.OWNER

        # Read = READ
        if operation_key == "read":
            return PermissionType.READ

        # Delete space = OWNER
        if operation_key == "delete" and target_type == "space":
            return PermissionType.OWNER

        # Comment operations = COMMENT
        if target_type == "comment" and operation_key in ["create", "delete"]:
            return PermissionType.COMMENT

        # Page/blogpost/attachment operations = WRITE
        if target_type in ["page", "blogpost", "attachment"]:
            if operation_key in ["create", "delete", "archive"]:
                return PermissionType.WRITE

        # Everything else = OTHER
        return PermissionType.OTHER

    def _map_page_permission(self, operation: str) -> PermissionType:
        """
        Map page restriction operation to PermissionType enum.

        Page restrictions only have two operations:
        - read → READ
        - update → WRITE

        Args:
            operation: Operation string ("read" or "update")

        Returns:
            PermissionType enum value
        """
        if operation == "read":
            return PermissionType.READ
        elif operation == "update":
            return PermissionType.WRITE
        else:
            return PermissionType.OTHER

    async def _transform_page_restriction_to_permissions(
        self,
        restriction_data: Dict[str, Any]
    ) -> List[Permission]:
        """
        Transform page restriction data (from v1 API) to Permission objects.
        Creates pseudo-groups for users without email to preserve permissions.

        The v1 API returns restrictions in this format:
        {
            "operation": "read" | "update",
            "restrictions": {
                "user": {
                    "results": [{"type": "known", "accountId": "...", "displayName": "..."}]
                },
                "group": {
                    "results": [{"type": "group", "name": "...", "id": "..."}]
                }
            }
        }

        Args:
            restriction_data: Single restriction object with operation and restrictions

        Returns:
            List of Permission objects
        """
        permissions = []

        try:
            operation = restriction_data.get("operation")
            if not operation:
                return permissions

            # Map operation to PermissionType
            permission_type = self._map_page_permission(operation)

            restrictions = restriction_data.get("restrictions", {})

            # Process user restrictions - create pseudo-group if user not found
            user_restrictions = restrictions.get("user", {})
            user_results = user_restrictions.get("results", [])

            for user_data in user_results:
                # Extract accountId (could be under different keys)
                principal_id = user_data.get("accountId") or user_data.get("id")
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "user",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=True  # Enable pseudo-group creation for record-level permissions
                    )
                    if permission:
                        permissions.append(permission)

            # Process group restrictions
            group_restrictions = restrictions.get("group", {})
            group_results = group_restrictions.get("results", [])

            for group_data in group_results:
                principal_id = group_data.get("id")
                if principal_id:
                    permission = await self._create_permission_from_principal(
                        "group",
                        principal_id,
                        permission_type,
                        create_pseudo_group_if_missing=False  # Groups don't need pseudo-groups
                    )
                    if permission:
                        permissions.append(permission)

        except Exception as e:
            self.logger.error(f"❌ Failed to transform page restriction: {e}")

        return permissions

    def _transform_to_space_record_group(
        self,
        space_data: Dict[str, Any],
        base_url: Optional[str] = None
    ) -> Optional[RecordGroup]:
        """
        Transform Confluence space data to RecordGroup entity.

        Args:
            space_data: Raw space data from Confluence API
            base_url: Base URL from API response (_links.base)

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            space_id = space_data.get("id")
            space_name = space_data.get("name")
            space_description = space_data.get("description", "")
            space_key = space_data.get("key", "")

            if not space_id or not space_name:
                return None

            # Parse timestamps
            source_created_at = None
            created_at_str = space_data.get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Construct web URL: base + webui
            web_url = None
            if base_url:
                webui = space_data.get("_links", {}).get("webui")
                if webui:
                    web_url = f"{base_url}{webui}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=space_name,
                short_name=space_key,
                description=space_description,
                external_group_id=space_id,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                group_type=RecordGroupType.CONFLUENCE_SPACES,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,  # Confluence doesn't provide updated timestamp for spaces
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space: {e}")
            return None

    def _transform_to_webpage_record(
        self,
        data: Dict[str, Any],
        record_type: RecordType,
        existing_record: Optional[Record] = None
    ) -> Optional[WebpageRecord]:
        """
        Unified transform for page/blogpost data to WebpageRecord.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            existing_record: Optional existing record to check for updates

        Returns:
            WebpageRecord object or None if transformation fails
        """
        # Derive content_type for logging
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        try:
            item_id = data.get("id")
            item_title = data.get("title")

            if not item_id or not item_title:
                return None

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at:
                history = data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    updated_when = last_updated.get("when")
                    if updated_when:
                        source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract space ID - v2 has spaceId at top level, v1 has space.id
            external_record_group_id = None
            space_id_v2 = data.get("spaceId")  # v2 format
            if space_id_v2:
                external_record_group_id = str(space_id_v2)
            else:
                # v1 format
                space_data = data.get("space", {})
                space_id = space_data.get("id")
                external_record_group_id = str(space_id) if space_id else None

            if not external_record_group_id:
                self.logger.warning(f"{content_type.capitalize()} {item_id} has no space - skipping")
                return None

            # Extract parent page ID - v2 has parentId at top level, v1 uses ancestors
            parent_external_record_id = None
            parent_id_v2 = data.get("parentId")  # v2 format
            if parent_id_v2:
                parent_external_record_id = str(parent_id_v2)
            else:
                # v1 format - last ancestor is direct parent
                ancestors = data.get("ancestors", [])
                if ancestors and len(ancestors) > 0:
                    direct_parent = ancestors[-1]
                    parent_external_record_id = direct_parent.get("id")

            # Construct web URL - v1 vs v2 have different link structures
            web_url = None
            links = data.get("_links", {})
            webui = links.get("webui")

            if webui:
                # Try v2 format first (_links.base)
                base_url = links.get("base")
                if base_url:
                    web_url = f"{base_url}{webui}"
                else:
                    # Fall back to v1 format (extract from _links.self)
                    self_link = links.get("self")
                    if self_link and "/wiki/" in self_link:
                        base_url = self_link.split("/wiki/")[0] + "/wiki"
                        web_url = f"{base_url}{webui}"

            # Set parent_record_type to match record_type when parent exists
            # This allows placeholder parent creation when parent doesn't exist yet
            parent_record_type = record_type if parent_external_record_id else None

            # Determine record ID and version
            is_new = existing_record is None
            record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            return WebpageRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=item_title,
                record_type=record_type,
                external_record_id=item_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                weburl=web_url,
                mime_type=MimeTypes.BLOCKS.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=False,  # Pages are root nodes
                parent_node_id=None,  # Pages have no parent node
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform {content_type}: {e}")
            return None


    def _transform_to_attachment_file_record(
        self,
        attachment_data: Dict[str, Any],
        parent_external_record_id: str,
        parent_external_record_group_id: Optional[str],
        existing_record: Optional[Record] = None,
        parent_node_id: Optional[str] = None
    ) -> Optional[FileRecord]:
        """
        Transform Confluence attachment to FileRecord entity.
        Supports both v1 and v2 API response formats.

        Args:
            attachment_data: Raw attachment data from v1 (children.attachment.results) or v2 API
            parent_external_record_id: Parent page external_record_id
            parent_external_record_group_id: Space ID from parent page
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page

        Returns:
            FileRecord object or None if transformation fails
        """
        try:
            # Get attachment ID - both v1 and v2 use "id" field with "att" prefix
            attachment_id = attachment_data.get("id")
            if not attachment_id:
                return None
            # Get filename - same field in both v1 and v2
            file_name = attachment_data.get("title")
            if not file_name:
                return None

            # Clean query params from filename if present
            if '?' in file_name:
                file_name = file_name.split('?')[0]

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = attachment_data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = attachment_data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = attachment_data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at or not version_number:
                history = attachment_data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    if not source_updated_at:
                        updated_when = last_updated.get("when")
                        if updated_when:
                            source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract file size - v2 has it at top level, v1 in extensions
            file_size = attachment_data.get("fileSize")  # v2 format
            if file_size is None:
                extensions = attachment_data.get("extensions", {})
                file_size = extensions.get("fileSize")  # v1 format

            # Extract mime type - v2 has it at top level (mediaType), v1 in extensions or metadata
            media_type = attachment_data.get("mediaType")  # v2 format
            if not media_type:
                extensions = attachment_data.get("extensions", {})
                media_type = extensions.get("mediaType")  # v1 format (extensions)
            if not media_type:
                metadata = attachment_data.get("metadata", {})
                media_type = metadata.get("mediaType")  # v1 format (metadata)

            mime_type = None
            if media_type:
                # Try to map to MimeTypes enum
                for mime in MimeTypes:
                    if mime.value == media_type:
                        mime_type = mime
                        break

                # If not found in enum, use the raw value
                if not mime_type:
                    mime_type = media_type

            # Extract extension from filename
            extension = None
            if '.' in file_name:
                extension = file_name.split('.')[-1].lower()

            # Construct web URL using helper method
            links = attachment_data.get("_links", {})
            # For attachments, base_url might be in _links itself (v2 format)
            base_url_from_links = links.get("base")
            web_url = self._construct_web_url(links, base_url_from_links)

            # Determine record ID and version
            is_new = existing_record is None
            attachment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            return FileRecord(
                id=attachment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=attachment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                mime_type=mime_type,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=RecordType.WEBPAGE,
                external_record_group_id=parent_external_record_group_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                extension=extension,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=True,  # Attachments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent page
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform attachment: {e}")
            return None

    async def _process_webpage_with_update(
        self,
        data: Dict[str, Any],
        record_type: RecordType,
        existing_record: Optional[Record],
        permissions: List[Permission]
    ) -> RecordUpdate:
        """Process webpage with change detection.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            existing_record: Existing record from database (if any)
            permissions: Permissions for the record

        Returns:
            RecordUpdate object with change tracking
        """
        # Transform with existing record context
        webpage_record = self._transform_to_webpage_record(
            data, record_type, existing_record
        )

        if not webpage_record:
            return RecordUpdate(
                record=None,
                is_new=False,
                is_updated=False,
                is_deleted=False,
                content_changed=False,
                metadata_changed=False,
                permissions_changed=False,
                new_permissions=None,
                external_record_id=None
            )

        # Detect changes
        is_new = existing_record is None
        content_changed = False
        metadata_changed = False

        if not is_new:
            # Check if version changed (content update)
            current_version = data.get("version", {}).get("number")
            if str(current_version) != existing_record.external_revision_id:
                content_changed = True

            # Check if parent changed (moved between pages)
            current_parent_v2 = data.get("parentId")
            current_parent_v1 = None
            ancestors = data.get("ancestors", [])
            if ancestors and len(ancestors) > 0:
                current_parent_v1 = ancestors[-1].get("id")
            current_parent = current_parent_v2 or current_parent_v1

            if current_parent != existing_record.parent_external_record_id:
                metadata_changed = True

        return RecordUpdate(
            record=webpage_record,
            is_new=is_new,
            is_updated=content_changed or metadata_changed,
            is_deleted=False,
            content_changed=content_changed,
            metadata_changed=metadata_changed,
            permissions_changed=bool(permissions),
            new_permissions=permissions,
            external_record_id=webpage_record.external_record_id
        )

    async def _fetch_group_members(self, group_id: str, group_name: str) -> List[str]:
        """
        Fetch all members of a group with pagination.

        Args:
            group_id: The group ID
            group_name: The group name (for logging)

        Returns:
            List of member email addresses
        """
        try:
            member_emails = []
            batch_size = 100
            start = 0

            # Paginate through group members
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_group_members(
                    group_id=group_id,
                    start=start,
                    limit=batch_size
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                members_data = response_data.get("results", [])

                if not members_data:
                    break

                # Extract emails from members (skip members without email)
                for member_data in members_data:
                    email = member_data.get("email", "").strip()
                    if email:
                        member_emails.append(email)
                    else:
                        self.logger.warning(f"Skipping member creation with name : {member_data.get('displayName')}, Reason: No email found for the member")

                # Move to next page
                start += batch_size

                # Check if we have more members
                size = response_data.get("size", 0)
                if size < batch_size:
                    break

            return member_emails

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch members for group {group_name}: {e}")
            return []

    async def _get_app_users_by_emails(self, emails: List[str]) -> List[AppUser]:
        """
        Get AppUser objects by their email addresses from database.

        Args:
            emails: List of user email addresses

        Returns:
            List of AppUser objects found in database
        """
        if not emails:
            return []

        try:
            # Fetch all users from database
            all_app_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id
            )

            self.logger.debug(f"Fetched {len(all_app_users)} total users from database for email lookup")

            # Create email lookup map
            email_set = set(emails)

            # Filter users by email
            filtered_users = [user for user in all_app_users if user.email in email_set]

            if len(filtered_users) < len(emails):
                missing_count = len(emails) - len(filtered_users)
                self.logger.debug(f"  ⚠️ {missing_count} user(s) not found in database")

            return filtered_users

        except Exception as e:
            self.logger.error(f"❌ Failed to get users by emails: {e}")
            return []

    def _transform_to_app_user(self, user_data: Dict[str, Any]) -> Optional[AppUser]:
        """
        Transform Confluence user data to AppUser entity.

        Args:
            user_data: Raw user data from Confluence API

        Returns:
            AppUser object or None if transformation fails
        """
        try:
            account_id = user_data.get("accountId")
            email = user_data.get("email", "").strip()

            if not account_id or not email:
                return None

            # Parse lastModified timestamp
            source_updated_at = None
            last_modified = user_data.get("lastModified")
            if last_modified:
                source_updated_at = self._parse_confluence_datetime(last_modified)

            return AppUser(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user_data.get("displayName"),
                is_active=False,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform user: {e}")
            return None

    def _transform_to_user_group(
        self,
        group_data: Dict[str, Any]
    ) -> Optional[AppUserGroup]:
        """
        Transform Confluence group data to AppUserGroup entity.

        Args:
            group_data: Raw group data from Confluence API

        Returns:
            AppUserGroup object or None if transformation fails
        """
        try:
            group_id = group_data.get("id")
            group_name = group_data.get("name")

            if not group_id or not group_name:
                return None

            return AppUserGroup(
                app_name=Connectors.CONFLUENCE,
                connector_id=self.connector_id,
                source_user_group_id=group_id,
                name=group_name,
                org_id=self.data_entities_processor.org_id,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform group: {e}")
            return None

    def _parse_confluence_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse Confluence datetime string to epoch timestamp in milliseconds.

        Confluence format: "2025-11-13T07:51:50.526Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Confluence datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-11-13T07:51:50.526Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

    async def get_signed_url(self, record: Record) -> str:
        """Get a signed URL for a record (not implemented for Confluence)."""
        # Confluence uses OAuth, signed URLs are not applicable
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (page BlocksContainer or attachment file) from Confluence.

        For pages/blogposts (WebpageRecord): Fetches ADF content and converts to BlocksContainer with embedded comments
        For attachments (FileRecord): Downloads file from attachment download URL

        Args:
            record: The record to stream (page, blogpost, or attachment)

        Returns:
            StreamingResponse: Streaming response with BlocksContainer JSON or file content
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type in [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST]:
                # Page or blogpost - fetch ADF content and convert to BlocksContainer
                page_data = await self._fetch_page_data_with_adf(record.external_record_id, record.record_type)

                # Process attachments for children_records
                # Fetch attachments directly from v2 API (since page content API doesn't include them)
                attachments_data = []
                try:
                    datasource = await self._get_fresh_datasource()
                    attachments_response = await datasource.get_page_attachments(
                        id=int(record.external_record_id),
                        status=["current"],  # Only fetch current version attachments
                        limit=100
                    )
                    if attachments_response and attachments_response.status == HttpStatusCode.SUCCESS.valu:
                        attachments_result = attachments_response.json()
                        attachments_data = attachments_result.get("results", [])
                        self.logger.debug(f"Fetched {len(attachments_data)} attachment(s) for streaming")
                except Exception as e:
                    self.logger.warning(f"Failed to fetch attachments for streaming: {e}", exc_info=True)
                    attachments_data = []

                attachment_children_map = await self._process_page_attachments_for_children(
                    attachments_data,
                    record.external_record_id,
                    record.id,
                    record.external_record_group_id,
                    record.weburl,
                )

                # Build MIME types map from attachments_data
                attachment_mime_types: Dict[str, str] = {}
                for attachment in attachments_data:
                    attachment_id = attachment.get("id")
                    if attachment_id:
                        # Try different locations for mediaType (v2 API structure)
                        media_type = attachment.get("mediaType") or attachment.get("metadata", {}).get("mediaType")
                        if media_type:
                            attachment_mime_types[str(attachment_id)] = media_type

                # Parse to BlocksContainer
                blocks_container = await self._parse_confluence_page_to_blocks(
                    page_data=page_data,
                    page_id=record.external_record_id,
                    page_title=record.record_name,
                    weburl=record.weburl,
                    attachment_children_map=attachment_children_map,
                    attachment_mime_types=attachment_mime_types,
                    attachments_data=attachments_data,
                    record_type=record.record_type,
                )

                # Serialize and stream
                blocks_json = blocks_container.model_dump_json(indent=2)

                return StreamingResponse(
                    iter([blocks_json.encode('utf-8')]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.json"'}
                )

            elif record.record_type == RecordType.FILE:
                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    self._fetch_attachment_content(record),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
            )

    async def _fetch_page_content(self, page_id: str, record_type: RecordType) -> str:
        """
        Fetch page or blogpost HTML content from Confluence using v2 API.

        Args:
            page_id: The page or blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            str: HTML content of the page/blogpost

        Raises:
            HTTPException: If content not found or fetch fails
        """
        try:
            self.logger.debug(f"Fetching content for {page_id} (type: {record_type})")

            datasource = await self._get_fresh_datasource()

            # Call appropriate API based on record type
            if record_type == RecordType.CONFLUENCE_PAGE:
                response = await datasource.get_page_content_v2(
                    page_id=page_id,
                    body_format="export_view"
                )
            elif record_type == RecordType.CONFLUENCE_BLOGPOST:
                response = await datasource.get_blogpost_content_v2(
                    blogpost_id=page_id,
                    body_format="export_view"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type: {record_type}"
                )

            # Check response
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                raise HTTPException(
                    status_code=404,
                    detail=f"Content not found: {page_id}"
                )

            response_data = response.json()

            # Extract HTML content from body.export_view.value
            body = response_data.get("body", {})
            export_view = body.get("export_view", {})
            html_content = export_view.get("value", "")

            if not html_content:
                self.logger.warning(f"Content {page_id} has no body")
                html_content = "<p>No content available</p>"

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for {page_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch content: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch content: {str(e)}"
            )

    async def _fetch_comment_content(self, record: CommentRecord) -> str:
        """
        Fetch comment HTML content from Confluence based on record type.

        Args:
            record: CommentRecord with external_record_id and record_type

        Returns:
            str: HTML content of the comment

        Raises:
            HTTPException: If comment not found or fetch fails
        """
        try:
            comment_id = record.external_record_id
            self.logger.debug(f"Fetching comment content for {comment_id} (type: {record.record_type})")

            datasource = await self._get_fresh_datasource()

            # Call appropriate API based on record type
            if record.record_type == RecordType.COMMENT:
                # Footer comment
                response = await datasource.get_footer_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            elif record.record_type == RecordType.INLINE_COMMENT:
                # Inline comment
                response = await datasource.get_inline_comment_by_id(
                    comment_id=int(comment_id),
                    body_format="storage"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported comment type: {record.record_type}"
                )

            # Check response
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                raise HTTPException(
                    status_code=404,
                    detail=f"Comment not found: {comment_id}"
                )

            response_data = response.json()

            # Extract HTML content from body.storage.value
            body = response_data.get("body", {})
            storage = body.get("storage", {})
            html_content = storage.get("value", "")

            if not html_content:
                self.logger.warning(f"Comment {comment_id} has no content")
                html_content = "<p>No content available</p>"

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for comment {comment_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch comment content: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch comment content: {str(e)}"
            )

    def _resolve_confluence_attachment_id(
        self,
        media_info: Dict[str, Any],
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
        """
        Resolve Confluence attachment ID from ADF media node.

        ADF media nodes have attrs.id which is a Media Platform UUID.
        Attachment objects have:
        - id: Confluence attachment ID (e.g., "att195952659")
        - fileId: Media Platform UUID that matches attrs.id from ADF

        Args:
            media_info: Media node info from extract_media_from_adf
            attachments: Optional list of attachment objects to match against

        Returns:
            Confluence attachment ID (e.g., "att195952659") or None
        """
        media_id = media_info.get("id")  # Media Platform UUID from ADF
        filename = media_info.get("filename") or media_info.get("alt")

        if media_id and attachments:
            for att in attachments:
                att_file_id = att.get("fileId")
                if att_file_id == media_id:
                    return att.get("id")

        if filename and attachments:
            for att in attachments:
                if att.get("title", "") == filename:
                    return att.get("id")

            filename_lower = filename.lower().strip()
            for att in attachments:
                if att.get("title", "").lower().strip() == filename_lower:
                    return att.get("id")

        return None

    def _create_confluence_media_fetcher(
        self,
        page_id: str,
        content_type: str = "page"
    ) -> Callable[[str, str], Awaitable[Optional[str]]]:
        """
        Create a media fetcher callback bound to a specific Confluence page.

        Args:
            page_id: The page ID to bind to the fetcher
            content_type: "page" or "blogpost" to determine which API to use

        Returns:
            Async function that takes (media_id, alt_text) and returns base64 data URI
        """
        # Capture page_id and content_type in this scope
        captured_page_id = page_id
        captured_content_type = content_type

        async def fetcher(media_id: str, alt_text: str) -> Optional[str]:
            return await self._fetch_confluence_media_as_base64(captured_page_id, media_id, alt_text, captured_content_type)

        return fetcher

    async def _fetch_confluence_media_as_base64(
        self,
        page_id: str,
        media_id: str,
        media_alt: str,
        content_type: str = "page"
    ) -> Optional[str]:
        """
        Fetch Confluence attachment content and return as base64 data URI.

        Args:
            page_id: The page ID containing the attachment
            media_id: The media ID from ADF (UUID token)
            media_alt: The alt text/filename for matching
            content_type: "page" or "blogpost" to determine which API to use

        Returns:
            Base64 data URI string or None
        """
        try:
            # Fetch attachments using v2 API (page or blogpost specific)
            datasource = await self._get_fresh_datasource()
            # Convert page_id to int if it's a string
            page_id_int = int(page_id) if isinstance(page_id, str) else page_id

            # Use correct API based on content type
            if content_type == "blogpost":
                response = await datasource.get_blogpost_attachments(
                    id=page_id_int,
                    status=["current"],  # Only fetch current version attachments
                    limit=100
                )
            else:
                response = await datasource.get_page_attachments(
                    id=page_id_int,
                    status=["current"],  # Only fetch current version attachments
                    limit=100
                )

            if response.status != HttpStatusCode.SUCCESS.value:
                self.logger.debug(f"No attachments found for page {page_id}")
                return None

            attachments_data = response.json()
            attachments = attachments_data.get("results", [])  # v2 API uses "results"

            # Find attachment by filename (alt text)
            target_attachment = None
            if media_alt:
                for attachment in attachments:
                    filename = attachment.get("title") or attachment.get("metadata", {}).get("mediaType", "")
                    if filename == media_alt or filename.lower() == media_alt.lower():
                        target_attachment = attachment
                        break

            if not target_attachment:
                self.logger.debug(f"No attachment found matching '{media_alt}' in page {page_id}")
                return None

            # Download attachment content
            attachment_id = target_attachment.get("id")
            mime_type = target_attachment.get("metadata", {}).get("mediaType", "application/octet-stream")

            # Stream attachment content
            content_bytes = b""
            async for chunk in datasource.download_attachment(
                parent_page_id=page_id,
                attachment_id=attachment_id
            ):
                content_bytes += chunk

            # Convert to base64
            base64_data = base64.b64encode(content_bytes).decode('utf-8')
            data_uri = f"data:{mime_type};base64,{base64_data}"

            return data_uri

        except Exception as e:
            self.logger.warning(f"Error fetching media (id='{media_id}', alt='{media_alt}') for page {page_id}: {e}")
            return None

    async def _fetch_page_comments_recursive(
        self,
        page_id: str,
        record_type: RecordType,
        comment_type: str  # "footer" or "inline"
    ) -> List[Dict[str, Any]]:
        """
        Recursively fetch all comments (footer or inline) for a page or blogpost using v2 API.
        Returns a flat list of all comments including nested replies.

        Args:
            page_id: The page/blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            comment_type: "footer" or "inline"

        Returns:
            List of comment dictionaries with ADF body
        """
        all_comments: List[Dict[str, Any]] = []
        batch_size = 100
        cursor = None

        datasource = await self._get_fresh_datasource()
        page_id_int = int(page_id) if isinstance(page_id, str) else page_id

        # Fetch top-level comments
        while True:
            try:
                if record_type == RecordType.CONFLUENCE_PAGE:
                    if comment_type == "footer":
                        response = await datasource.get_page_footer_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                    else:  # inline
                        response = await datasource.get_page_inline_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                elif record_type == RecordType.CONFLUENCE_BLOGPOST:
                    if comment_type == "footer":
                        response = await datasource.get_blog_post_footer_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                    else:  # inline
                        response = await datasource.get_blog_post_inline_comments(
                            id=page_id_int,
                            body_format="atlas_doc_format",  # Pass as string
                            cursor=cursor,
                            limit=batch_size
                        )
                else:
                    self.logger.error(f"Unsupported record type for comments: {record_type}")
                    break

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.debug(f"Failed to fetch {comment_type} comments for {record_type}: {page_id}")
                    break

                response_data = response.json()
                comments_data = response_data.get("results", [])

                if not comments_data:
                    break

                # Process each comment and recursively fetch children
                for comment in comments_data:
                    all_comments.append(comment)
                    # Recursively fetch children
                    children = await self._fetch_comment_children_recursive(
                        comment_id=comment.get("id"),
                        comment_type=comment_type,
                        record_type=record_type
                    )
                    all_comments.extend(children)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            except Exception as e:
                self.logger.error(f"Error fetching {comment_type} comments for page {page_id}: {e}", exc_info=True)
                break

        return all_comments

    async def _fetch_comment_children_recursive(
        self,
        comment_id: str,
        comment_type: str,  # "footer" or "inline"
        record_type: RecordType
    ) -> List[Dict[str, Any]]:
        """
        Recursively fetch all child comments (replies) for a given comment.

        Args:
            comment_id: The parent comment ID
            comment_type: "footer" or "inline"
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            List of child comment dictionaries
        """
        all_children: List[Dict[str, Any]] = []
        batch_size = 100
        cursor = None

        datasource = await self._get_fresh_datasource()
        comment_id_int = int(comment_id) if isinstance(comment_id, str) else comment_id

        while True:
            try:
                if comment_type == "footer":
                    response = await datasource.get_footer_comment_children(
                        id=comment_id_int,
                        body_format="atlas_doc_format",  # Pass as string
                        cursor=cursor,
                        limit=batch_size
                    )
                else:  # inline
                    response = await datasource.get_inline_comment_children(
                        id=comment_id_int,
                        body_format="atlas_doc_format",  # Pass as string
                        cursor=cursor,
                        limit=batch_size
                    )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    break

                response_data = response.json()
                children_data = response_data.get("results", [])

                if not children_data:
                    break

                # Process each child and recursively fetch their children
                for child in children_data:
                    all_children.append(child)
                    # Recursively fetch grandchildren
                    grandchildren = await self._fetch_comment_children_recursive(
                        comment_id=child.get("id"),
                        comment_type=comment_type,
                        record_type=record_type
                    )
                    all_children.extend(grandchildren)

                # Extract next cursor
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                cursor = self._extract_cursor_from_next_link(next_url)
                if not cursor:
                    break

            except Exception as e:
                self.logger.error(f"Error fetching children for comment {comment_id}: {e}", exc_info=True)
                break

        return all_children

    def _organize_confluence_comments_to_threads(
        self,
        comments_data: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Group Confluence comments by thread (parent comment) and sort by created timestamp.
        Returns list of threads, each thread is a list of comments sorted by created.

        Confluence comments can have parentCommentId for replies.
        - Top-level comments (no parentCommentId) start their own thread
        - Replies grouped under their parent's thread_id
        - Each thread sorted by created timestamp (oldest first)
        - Threads sorted by first comment's created timestamp
        """
        if not comments_data:
            return []

        threads: Dict[str, List[Dict[str, Any]]] = {}

        for comment in comments_data:
            comment_id = str(comment.get("id", ""))
            parent_comment_id = comment.get("parentCommentId")

            # Thread ID is parent's ID if it's a reply, or self ID if top-level
            thread_id = str(parent_comment_id) if parent_comment_id else comment_id
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread by created timestamp (oldest first)
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c: self._parse_confluence_datetime(
                    c.get("version", {}).get("createdAt", "")
                ) or 0
            )

        # Sort threads by first comment's created timestamp (oldest thread first)
        sorted_threads = sorted(
            threads.values(),
            key=lambda t: self._parse_confluence_datetime(
                t[0].get("version", {}).get("createdAt", "")
            ) or 0 if t else 0
        )

        return sorted_threads

    def _create_comment_media_fetcher(
        self,
        page_id: str,
        content_type: str = "page"
    ) -> Callable[[str, str], Awaitable[Optional[str]]]:
        """
        Create a media fetcher callback for comments.
        Comments use collection "comment-container-{pageId}" but attachments
        are still accessible via page attachments API.

        Args:
            page_id: The page ID containing the comment
            content_type: "page" or "blogpost" to determine which API to use

        Returns:
            Async function that takes (media_id, alt_text) and returns base64 data URI
        """
        captured_page_id = page_id
        captured_content_type = content_type

        async def fetcher(media_id: str, alt_text: str) -> Optional[str]:
            # For comments, media might be in comment-container collection
            # but we can still try to fetch from page attachments
            return await self._fetch_confluence_media_as_base64(captured_page_id, media_id, alt_text, captured_content_type)

        return fetcher

    async def _parse_confluence_page_to_blocks(
        self,
        page_data: Dict[str, Any],
        page_id: str,
        page_title: str,
        weburl: Optional[str] = None,
        attachment_children_map: Optional[Dict[str, ChildRecord]] = None,
        attachment_mime_types: Optional[Dict[str, str]] = None,
        attachments_data: Optional[List[Dict[str, Any]]] = None,
        record_type: Optional[RecordType] = None,
    ) -> BlocksContainer:
        """
        Parse Confluence page ADF content into BlocksContainer with comments.

        Structure:
        - Main content BlockGroup (index=0)
        - Comment Thread BlockGroups (index=1,2,...) for footer and inline comments
        - Comment BlockGroups (sub_type=COMMENT) for each comment
        - Attachments assigned to description or comment children

        Args:
            page_data: Page data from API (with body.atlas_doc_format.value)
            page_id: Page external ID
            page_title: Page title
            weburl: Page web URL
            attachment_children_map: Map of attachment_id -> ChildRecord
            attachment_mime_types: Map of attachment_id -> mime_type
            attachments_data: List of attachment objects from API (for fileId matching)
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            BlocksContainer with BlockGroups and Blocks
        """
        block_groups: List[BlockGroup] = []
        blocks: List[Block] = []
        block_group_index = 0

        # Extract ADF content
        body = page_data.get("body", {})
        atlas_doc = body.get("atlas_doc_format", {})
        adf_content = atlas_doc.get("value")

        # Track attachment IDs used in comments (to exclude from description children)
        comment_attachment_ids: Set[str] = set()
        # Track embedded images in description (already in content as base64, exclude from children)
        description_image_ids: Set[str] = set()

        def resolve_attachment_id(media_info: Dict[str, Any]) -> Optional[str]:
            """Resolve ADF media node to attachment ID.

            ADF media nodes have attrs.id which is a Media Platform UUID.
            Attachment objects have:
            - id: Confluence attachment ID (e.g., "att195952659")
            - fileId: Media Platform UUID that matches attrs.id from ADF
            """
            media_id = media_info.get("id")  # Media Platform UUID from ADF

            # Method 1: Match media_id with attachment.fileId (PRIMARY - most reliable)
            if media_id and attachments_data:
                for att in attachments_data:
                    # Compare ADF media_id with attachment fileId (Media Platform UUID)
                    if att.get("fileId") == media_id:
                        attachment_id = att.get("id")  # Confluence attachment ID
                        # Verify it exists in attachment_children_map
                        if attachment_id and str(attachment_id) in attachment_children_map:
                            return str(attachment_id)

            # Method 2: Fall back to filename matching
            media_filename = media_info.get("filename", "") or media_info.get("alt", "")
            if not media_filename:
                return None

            # Build filename -> attachment_id lookup from attachment_children_map
            if attachment_children_map:
                for att_id, child_record in attachment_children_map.items():
                    child_name = child_record.child_name
                    if child_name:
                        # Try exact match
                        if child_name == media_filename:
                            return att_id
                        # Try case-insensitive match
                        if child_name.lower().strip() == media_filename.lower().strip():
                            return att_id

            return None

        def is_image_attachment(attachment_id: str) -> bool:
            """Check if attachment is an image based on MIME type."""
            _attachment_mime_types = attachment_mime_types or {}
            mime_type = _attachment_mime_types.get(attachment_id, "")
            return mime_type.startswith("image/")

        # Extract media from description ADF - identify embedded images (to exclude from children)
        if adf_content:
            try:
                # Parse ADF if it's a string
                if isinstance(adf_content, str):
                    adf_dict = json.loads(adf_content)
                else:
                    adf_dict = adf_content

                for media_info in extract_media_from_adf(adf_dict):
                    attachment_id = resolve_attachment_id(media_info)
                    if attachment_id and is_image_attachment(attachment_id):
                        description_image_ids.add(attachment_id)
            except Exception as e:
                self.logger.debug(f"Error extracting media from description: {e}")

        # 1. Convert description ADF to markdown
        if not adf_content:
            description_content = f"# {page_title}"
        else:
            # Parse ADF if it's a string
            if isinstance(adf_content, str):
                try:
                    adf_dict = json.loads(adf_content)
                except json.JSONDecodeError:
                    adf_dict = {}
            else:
                adf_dict = adf_content

            # Convert ADF to markdown with embedded images
            # Determine content_type for correct API calls
            content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"
            description_content = await adf_to_text_with_images(
                adf_dict,
                self._create_confluence_media_fetcher(page_id, content_type),
                self.logger
            )

        # Check if description content is empty (after stripping whitespace)
        description_content_stripped = description_content.strip() if description_content else ""
        has_description_content = bool(description_content_stripped)

        # Check if we have attachments that might go to description (before comments processing)
        # We'll know for sure after processing comments, but we can check if any exist
        bool(attachment_children_map)

        # 2. Fetch and process comments (footer and inline)
        has_comments = False
        if record_type:
            all_comments: List[Dict[str, Any]] = []

            # Fetch footer comments
            footer_comments = await self._fetch_page_comments_recursive(
                page_id=page_id,
                record_type=record_type,
                comment_type="footer"
            )
            for comment in footer_comments:
                comment["_comment_type"] = "footer"  # Mark comment type
                all_comments.append(comment)

            # Fetch inline comments
            inline_comments = await self._fetch_page_comments_recursive(
                page_id=page_id,
                record_type=record_type,
                comment_type="inline"
            )
            for comment in inline_comments:
                comment["_comment_type"] = "inline"  # Mark comment type
                all_comments.append(comment)

            # Organize comments into threads
            if all_comments:
                has_comments = True
                sorted_threads = self._organize_confluence_comments_to_threads(all_comments)

                for thread_comments in sorted_threads:
                    if not thread_comments:
                        continue

                    # Get thread ID from first comment
                    first_comment = thread_comments[0]
                    parent_comment_id = first_comment.get("parentCommentId")
                    first_comment_id = str(first_comment.get("id", ""))
                    thread_id = str(parent_comment_id) if parent_comment_id else first_comment_id
                    comment_type = first_comment.get("_comment_type", "footer")

                    # Create thread BlockGroup with parent_index=0 (Description BlockGroup)
                    thread_block_group_index = block_group_index
                    thread_block_group = BlockGroup(
                        id=str(uuid.uuid4()),
                        index=thread_block_group_index,
                        parent_index=0,
                        name=f"{comment_type.capitalize()} Comment Thread - {thread_id[:8]}" if thread_id else f"{comment_type.capitalize()} Comment Thread",
                        type=GroupType.TEXT_SECTION,
                        sub_type=GroupSubType.COMMENT_THREAD,
                        description=f"{comment_type.capitalize()} comment thread for page {page_title}",
                        source_group_id=f"{page_id}_thread_{comment_type}_{thread_id}" if thread_id else f"{page_id}_thread_{comment_type}_{thread_block_group_index}",
                        weburl=weburl,
                        requires_processing=False,
                    )
                    block_groups.append(thread_block_group)
                    block_group_index += 1

                    # Create BlockGroup objects for each comment in the thread
                    for comment in thread_comments:
                        comment_id = str(comment.get("id", ""))
                        comment_body_data = comment.get("body", {})
                        atlas_doc_format = comment_body_data.get("atlas_doc_format", {})
                        adf_value = atlas_doc_format.get("value")

                        if not adf_value:
                            continue

                        # Parse ADF (it's a JSON string)
                        try:
                            if isinstance(adf_value, str):
                                comment_body_adf = json.loads(adf_value)
                            else:
                                comment_body_adf = adf_value
                        except json.JSONDecodeError:
                            self.logger.warning(f"Failed to parse ADF for comment {comment_id}")
                            continue

                        # Convert ADF comment body to markdown with base64 images
                        # Use same content_type as parent page/blogpost for API calls
                        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"
                        comment_body = await adf_to_text_with_images(
                            comment_body_adf,
                            self._create_comment_media_fetcher(page_id, content_type),
                            self.logger
                        )

                        if not comment_body:
                            continue

                        # Build comment weburl
                        links = comment.get("_links", {})
                        comment_weburl_raw = links.get("webui", weburl)
                        comment_weburl = comment_weburl_raw
                        if comment_weburl and not comment_weburl.startswith("http"):
                            # Construct full URL if relative
                            # Extract base URL from page weburl (e.g., https://domain.atlassian.net/wiki)
                            if weburl and weburl.startswith("http"):
                                # Parse base URL from page weburl
                                parsed = urlparse(weburl)
                                base_url = f"{parsed.scheme}://{parsed.netloc}/wiki"
                                comment_weburl = f"{base_url}{comment_weburl}"

                        # Get author info
                        version = comment.get("version", {})
                        author_id = version.get("authorId", "Unknown")

                        # Get file attachments used in this comment (images excluded - already as base64)
                        comment_children: List[ChildRecord] = []
                        for media_info in extract_media_from_adf(comment_body_adf):
                            attachment_id = resolve_attachment_id(media_info)
                            if attachment_id and attachment_id in attachment_children_map:
                                comment_attachment_ids.add(attachment_id)
                                # Only include non-image files (images already embedded as base64)
                                if not is_image_attachment(attachment_id):
                                    comment_children.append(attachment_children_map[attachment_id])

                        # Create BlockGroup with sub_type=COMMENT
                        comment_block_group = BlockGroup(
                            id=str(uuid.uuid4()),
                            index=block_group_index,
                            parent_index=thread_block_group_index,  # Points to thread BlockGroup
                            type=GroupType.TEXT_SECTION,
                            sub_type=GroupSubType.COMMENT,
                            name=f"Comment by {author_id}",
                            description=f"Comment by {author_id}",
                            source_group_id=comment_id,
                            data=comment_body,
                            format=DataFormat.MARKDOWN,
                            weburl=comment_weburl or weburl,
                            requires_processing=True,
                            children_records=comment_children if comment_children else None,
                        )
                        block_groups.append(comment_block_group)
                        block_group_index += 1

        # Build description children: all attachments NOT used in comments and NOT embedded images
        description_children: List[ChildRecord] = []
        if attachment_children_map:
            for attachment_id, child_record in attachment_children_map.items():
                if attachment_id in comment_attachment_ids:
                    continue  # Used in comment - belongs to that comment's BlockGroup
                if attachment_id in description_image_ids:
                    continue  # Embedded image in description - already in content as base64
                description_children.append(child_record)

        # Create description BlockGroup only if there's content, attachments, or comments
        # (Comments need parent_index=0, so we must create it if there are comments)
        if has_description_content or description_children or has_comments:
            # Determine if requires_processing: only true if there's actual content to process
            requires_processing = has_description_content

            description_block_group = BlockGroup(
                id=str(uuid.uuid4()),
                index=0,  # Description is always at index 0
                name=page_title,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Content for page {page_title}",
                source_group_id=f"{page_id}_content",
                data=description_content if has_description_content else "",
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=requires_processing,
                children_records=description_children if description_children else None,
            )
            # Insert at beginning and shift all other indices by 1
            block_groups.insert(0, description_block_group)
            for bg in block_groups[1:]:  # Update indices for all BlockGroups after description
                bg.index += 1

        # Populate children arrays for BlockGroups
        # Build a map of parent_index -> list of child indices
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)
        block_children_map: Dict[int, List[int]] = defaultdict(list)

        # Collect all BlockGroup children (thread groups and comment groups that are children of their parents)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        # Collect all Block children (if any blocks exist with parent_index)
        for b in blocks:
            if b.parent_index is not None:
                block_children_map[b.parent_index].append(b.index)

        # Now populate the children arrays using range-based structure
        for bg in block_groups:
            child_block_indices = []
            child_bg_indices = []

            # Add child BlockGroups
            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])

            # Add child Blocks
            if bg.index in block_children_map:
                child_block_indices = sorted(block_children_map[bg.index])

            # Set children if we have any
            if child_block_indices or child_bg_indices:
                bg.children = BlockGroupChildren.from_indices(
                    block_indices=child_block_indices,
                    block_group_indices=child_bg_indices
                )

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _fetch_page_data_with_adf(self, page_id: str, record_type: RecordType) -> Dict[str, Any]:
        """Fetch page/blogpost with ADF format instead of HTML."""
        datasource = await self._get_fresh_datasource()

        if record_type == RecordType.CONFLUENCE_PAGE:
            response = await datasource.get_page_content_v2(
                page_id=page_id,
                body_format="atlas_doc_format"  # ADF format
            )
        elif record_type == RecordType.CONFLUENCE_BLOGPOST:
            response = await datasource.get_blogpost_content_v2(
                blogpost_id=page_id,
                body_format="atlas_doc_format"  # ADF format
            )
        else:
            raise ValueError(f"Unsupported record type: {record_type}")

        if response.status != HttpStatusCode.SUCCESS.value:
            raise HTTPException(status_code=404, detail=f"Content not found: {page_id}")

        return response.json()

    async def _process_page_attachments_for_children(
        self,
        attachments_data: List[Dict[str, Any]],
        page_id: str,
        page_node_id: str,
        space_id: str,
        page_weburl: Optional[str],
    ) -> Dict[str, ChildRecord]:
        """
        Process page attachments and create ChildRecords.
        Creates FileRecords if they don't exist (for new attachments added after sync).
        """
        attachment_children_map: Dict[str, ChildRecord] = {}
        new_file_records: List[Tuple[FileRecord, List[Permission]]] = []

        async with self.data_store_provider.transaction() as tx_store:
            for attachment in attachments_data:
                attachment_id = attachment.get("id")
                if not attachment_id:
                    continue

                # Look up existing FileRecord (without "attachment_" prefix - matches how records are stored)
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=str(attachment_id)  # No prefix - matches external_record_id in FileRecord
                )

                # Create FileRecord if it doesn't exist (new attachment added after sync)
                if not existing_record:
                    # Transform to FileRecord
                    file_record = self._transform_to_attachment_file_record(
                        attachment_data=attachment,
                        parent_external_record_id=page_id,
                        parent_external_record_group_id=space_id,
                        existing_record=None,
                        parent_node_id=page_node_id
                    )

                    if file_record:
                        new_file_records.append((file_record, []))
                        existing_record = file_record

                if existing_record:
                    attachment_children_map[str(attachment_id)] = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    )

        # Save new FileRecords if any were created
        if new_file_records:
            await self.data_entities_processor.on_new_records(new_file_records)
            self.logger.info(f"📎 Created {len(new_file_records)} new FileRecords for attachments added after sync")

        return attachment_children_map

    async def _fetch_attachment_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream attachment file content from Confluence Cloud.

        Args:
            record: Record with external_record_id and parent_external_record_id

        Yields:
            bytes: File content in 8KB chunks

        Raises:
            HTTPException: If attachment not found or download fails
        """
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not attachment_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No attachment ID available for record {record.id}"
                )

            if not parent_page_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"No parent page ID available for attachment {attachment_id}"
                )

            # Use datasource to stream attachment content
            datasource = await self._get_fresh_datasource()
            async for chunk in datasource.download_attachment(
                parent_page_id=parent_page_id,
                attachment_id=attachment_id
            ):
                yield chunk

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to download attachment {record.external_record_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download attachment: {str(e)}"
            )

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex a list of Confluence records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances (WebpageRecord, FileRecord, CommentRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Confluence records")

            # Ensure external clients are initialized
            if not self.external_client or not self.data_source:
                self.logger.error("External API clients not initialized. Call init() first.")
                raise Exception("External API clients not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB and publish updateRecord events for records that changed at source
            if updated_records:
                for updated_record, permissions in updated_records:
                    # Update record content and publish updateRecord event
                    await self.data_entities_processor.on_record_content_update(updated_record)

                    # Update permissions if they exist
                    if permissions:
                        await self.data_entities_processor.on_updated_record_permissions(updated_record, permissions)

                self.logger.info(f"Published update events for {len(updated_records)} records that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")
        except Exception as e:
            self.logger.error(f"Error during Confluence reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Args:
            org_id: Organization ID
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            if record.record_type == RecordType.CONFLUENCE_PAGE:
                return await self._check_and_fetch_updated_page(org_id, record)
            elif record.record_type == RecordType.CONFLUENCE_BLOGPOST:
                return await self._check_and_fetch_updated_blogpost(org_id, record)
            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                # Comments are no longer synced as separate records (embedded in page blocks)
                self.logger.debug("Skipping comment record reindex - comments are embedded in page blocks")
                return None
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_page(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch page from source for reindexing."""
        try:
            page_id = record.external_record_id

            # Fetch page from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_page_content_v2(
                page_id=page_id,
                body_format="storage"
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Page {page_id} not found at source, may have been deleted")
                return None

            page_data = response.json()

            # Check if version changed
            current_version = page_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Page {page_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Page {page_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Page {page_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform page to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                page_data,
                RecordType.CONFLUENCE_PAGE,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(page_id)
            # Only set inherit_permissions to False if there are READ restrictions
            # EDIT-only restrictions should still inherit from space for READ access
            read_permissions = [p for p in permissions if p.type == PermissionType.READ]
            if len(read_permissions) > 0:
                webpage_record.inherit_permissions = False

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching page {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_blogpost(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch blogpost from source for reindexing."""
        try:
            blogpost_id = record.external_record_id

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_blog_post_by_id(
                id=int(blogpost_id),
                body_format="storage"
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Blogpost {blogpost_id} not found at source, may have been deleted")
                return None

            blogpost_data = response.json()

            # Check if version changed
            current_version = blogpost_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Blogpost {blogpost_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Blogpost {blogpost_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Blogpost {blogpost_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform blogpost to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                blogpost_data,
                RecordType.CONFLUENCE_BLOGPOST,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Fetch fresh permissions
            permissions = await self._fetch_page_permissions(blogpost_id)
            # Only set inherit_permissions to False if there are READ restrictions
            # EDIT-only restrictions should still inherit from space for READ access
            read_permissions = [p for p in permissions if p.type == PermissionType.READ]
            if len(read_permissions) > 0:
                webpage_record.inherit_permissions = False

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching blogpost {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            parent_page_id = record.parent_external_record_id

            if not parent_page_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent page ID")
                return None

            # Get parent page's internal record ID
            parent_node_id = None
            parent_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=parent_page_id
            )
            if parent_record:
                parent_node_id = parent_record.id

            # Fetch attachment metadata from source using v2 API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_attachment_by_id(
                id=attachment_id,
                include_version=True
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Attachment {attachment_id} not found at source, may have been deleted")
                return None

            attachment_data = response.json()

            # Check if version changed
            current_version = attachment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Attachment {attachment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Attachment {attachment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Get space_id from parent page or use existing
            parent_space_id = record.external_record_group_id

            # Transform attachment to FileRecord with existing record context
            attachment_record = self._transform_to_attachment_file_record(
                attachment_data,
                parent_page_id,
                parent_space_id,
                existing_record=record,
                parent_node_id=parent_node_id
            )

            if not attachment_record:
                return None

            # Attachments inherit permissions from parent page - fetch page permissions
            permissions = await self._fetch_page_permissions(parent_page_id)

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info("Cleaning up Confluence connector resources")
        # Add cleanup logic if needed

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for Confluence filters with cursor-based pagination.

        Supports:
        - space_keys: All available Confluence spaces
        - page_ids: Pages (with CQL fuzzy search when search term provided)
        - blogpost_ids: Blogposts (with CQL fuzzy search when search term provided)

        Args:
            filter_key: Filter field name
            page: Page number (for API compatibility, not used with cursor)
            limit: Items per page
            search: Search text to filter options (uses CQL fuzzy matching)
            cursor: Cursor for pagination (Confluence uses cursor-based pagination)

        Returns:
            FilterOptionsResponse with options and pagination metadata
        """
        if filter_key == "space_keys":
            return await self._get_space_options(page, limit, search, cursor)
        elif filter_key == "page_ids":
            return await self._get_page_options(page, limit, search, cursor)
        elif filter_key == "blogpost_ids":
            return await self._get_blogpost_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_space_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch available Confluence spaces with cursor-based pagination.

        Uses CQL search for fuzzy matching when search term is provided,
        otherwise uses v2 API for listing all spaces.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        spaces_list = []
        next_cursor = None

        if search:
            # Use CQL search for fuzzy matching on space name/key
            spaces_response = await datasource.search_spaces_cql(
                search_term=search,
                limit=limit,
                cursor=cursor
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()

            # CQL search returns results with nested 'space' object
            # Note: CQL response space object has key and name but NOT id
            for result in response_data.get("results", []):
                space = result.get("space", {})
                space_key = space.get("key")
                space_name = space.get("name") or space.get("title")

                # CQL response doesn't include space id, so we use key as identifier
                if space_key and space_name:
                    spaces_list.append({
                        "id": space_key,  # Use key as id since CQL doesn't return id
                        "key": space_key,
                        "name": space_name
                    })

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all spaces (no search term)
            spaces_response = await datasource.get_spaces(
                cursor=cursor,
                limit=limit,
                status="current"
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()
            spaces_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        # Use key as id since the filter is "space_keys" and backend expects keys
        options = [
            FilterOption(
                id=space.get("key"),  # Frontend will use this value, backend expects keys
                label=space.get("name")
            )
            for space in spaces_list
            if space.get("key") and space.get("name")
        ]

        # Return success response
        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_page_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch pages with cursor-based pagination.

        Uses CQL search for fuzzy title matching when search term is provided,
        otherwise uses v2 API for listing all pages.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        pages_list = []
        next_cursor = None

        if search:
            # Use CQL search for fuzzy title matching
            pages_response = await datasource.search_pages_cql(
                search_term=search,
                limit=limit,
                cursor=cursor
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "page":
                    pages_list.append(content)

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all pages (no search term)
            pages_response = await datasource.get_pages(
                cursor=cursor,
                limit=limit,
                status=["current"]
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()
            pages_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=p.get("id"),
                label=p.get('title')
            )
            for p in pages_list
            if p.get("id") and p.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_blogpost_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch blogposts with cursor-based pagination.

        Uses CQL search for fuzzy title matching when search term is provided,
        otherwise uses v2 API for listing all blogposts.
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        blogposts_list = []
        next_cursor = None

        if search:
            # Use CQL search for fuzzy title matching
            blogposts_response = await datasource.search_blogposts_cql(
                search_term=search,
                limit=limit,
                cursor=cursor
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "blogpost":
                    blogposts_list.append(content)

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")
        else:
            # Use v2 API to list all blogposts (no search term)
            blogposts_response = await datasource.get_blog_posts(
                cursor=cursor,
                limit=limit,
                status=["current"]
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()
            blogposts_list = response_data.get("results", [])

            # Extract cursor from next link
            next_cursor_link = response_data.get("_links", {}).get("next")
            if next_cursor_link:
                try:
                    parsed = urlparse(next_cursor_link)
                    query_params = parse_qs(parsed.query)
                    next_cursor = query_params.get("cursor", [None])[0]
                except Exception as e:
                    self.logger.warning(f"Failed to extract cursor from next link: {e}")

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=bp.get("id"),
                label=bp.get('title')
            )
            for bp in blogposts_list
            if bp.get("id") and bp.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Confluence")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "ConfluenceConnector":
        """Factory method to create a Confluence connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )

        await data_entities_processor.initialize()

        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
