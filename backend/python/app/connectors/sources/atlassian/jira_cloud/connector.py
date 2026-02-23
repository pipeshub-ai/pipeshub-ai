"""Jira Cloud Connector Implementation"""
import base64
import re
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
from urllib.parse import quote
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    RecordRelations,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterField,
    FilterOperatorType,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import JiraApp
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_JIRA_CONFIG_PATH,
    AtlassianScope,
)
from app.connectors.utils.value_mapper import ValueMapper, map_relationship_type
from app.models.blocks import (
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
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    IndexingStatus,
    MimeTypes,
    OriginTypes,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# API URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# Pagination/constants
DEFAULT_MAX_RESULTS: int = 50
BATCH_PROCESSING_SIZE: int = 100
USER_PAGE_SIZE: int = 50
GROUP_PAGE_SIZE: int = 50
GROUP_MEMBER_PAGE_SIZE: int = 50
AUDIT_PAGE_SIZE: int = 500

PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"

# JQL query constants
ISSUE_SEARCH_FIELDS: List[str] = [
    "summary", "description", "status", "priority",
    "creator", "reporter", "assignee", "created", "updated",
    "issuetype", "project", "parent", "attachment", "security",
    "issuelinks"
]



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
    media_cache: Optional[Dict[str, str]] = None
) -> str:
    """
    Convert Atlassian Document Format (ADF) to Markdown.
    Returns markdown-formatted text with headers, lists, code blocks, tables, etc.

    Args:
        adf_content: The ADF document to convert
        media_cache: Optional dict mapping media_id -> base64 data URI for embedding images
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

        # Handle both "bulletList" and "unorderedList" (some Jira versions use different names)
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
    media_fetcher: Callable[[str, str], Awaitable[Optional[str]]]
) -> str:
    """
    Convert Atlassian Document Format (ADF) to Markdown with embedded images.

    This async version fetches media content and embeds it as base64 data URIs.
    Used for streaming content that needs to be indexed by multimodal models.

    Args:
        adf_content: The ADF document to convert
        media_fetcher: Async callback that takes (media_id, alt_text) and returns
                      base64 data URI string or None if fetch fails

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
            except Exception:
                # If fetch fails, we'll just use the alt text
                pass

    # Reuse the main adf_to_text function with the media cache
    return adf_to_text(adf_content, media_cache)


@ConnectorBuilder("Jira")\
    .in_group(AppGroups.ATLASSIAN.value)\
    .with_description("Sync issues from Jira Cloud")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Jira",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Jira",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=AtlassianScope.get_jira_read_access(),
                agent=AtlassianScope.get_jira_read_access()
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Application (Client) ID",
                    placeholder="Enter your Atlassian Cloud Application ID",
                    description="The Application (Client) ID from Atlassian Developer Console"
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="Enter your Atlassian Cloud Client Secret",
                    description="The Client Secret from Atlassian Developer Console",
                    field_type="PASSWORD",
                    is_secret=True
                )
            ],
            icon_path="/assets/icons/connectors/jira.svg",
            app_group="Atlassian",
            app_description="OAuth application for accessing Jira Cloud API and issue tracking services",
            app_categories=["Storage"]
        )
    ])\
    .with_info("⚠️ Important: In order for users to get access to Jira data, each user needs to make their email visible in their Jira account settings. Users can do this by going to their Jira profile settings and switching email visibility to Public.")\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/jira.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Jira Cloud API Setup",
            "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/jira/jira',
            'pipeshub'
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(
            name="project_keys",
            display_name="Project Keys",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by project keys (e.g., PROJ1, PROJ2)",
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter issues by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter issues by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="issues",
            display_name="Index Issues",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issues",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_attachments",
            display_name="Index Issue and comment Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue attachments",
            default_value=True
        ))
    )\
    .build_decorator()
class JiraConnector(BaseConnector):
    """
    Jira connector for syncing projects, issues, groups, roles and users from Jira
    """
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            JiraApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.external_client: Optional[JiraClient] = None
        self.data_source: Optional[JiraDataSource] = None
        self.cloud_id: Optional[str] = None
        self.site_url: Optional[str] = None
        self.connector_id = connector_id
        self.connector_name = Connectors.JIRA

        # Initialize sync points
        org_id = self.data_entities_processor.org_id

        self.issues_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider
        )

        self.sync_filters = None
        self.indexing_filters = None

        # Initialize value mapper for standardizing status/priority/type values
        self.value_mapper = ValueMapper()

        # Per-issue attachments cache to avoid repeated API calls
        self._issue_attachments_cache: Dict[str, List[Dict[str, Any]]] = {}

    # ============================================================================
    # Initialization & Configuration
    # ============================================================================

    async def init(self) -> bool:
        """
        Initialize Jira client using proper Client + DataSource architecture
        """
        try:
            # Use JiraClient.build_from_services() to create client with proper auth
            client = await JiraClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Store client for token updates
            self.external_client = client

            # Create DataSource from client
            self.data_source = JiraDataSource(client)

            # Get cloud ID and site URL from accessible resources
            access_token = await self._get_access_token()
            resources = await JiraClient.get_accessible_resources(access_token)
            if not resources:
                raise Exception("No accessible Jira resources found")

            self.cloud_id = resources[0].id
            self.site_url = resources[0].url

            self.logger.info("✅ Jira client initialized successfully using Client + DataSource architecture")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Jira client: {e}")
            return False

    # ============================================================================
    # Authentication & Token Management
    # ============================================================================

    async def _get_access_token(self) -> str:
        """
        Get access token from config
        """
        config_path = OAUTH_JIRA_CONFIG_PATH.format(connector_id=self.connector_id)
        config = await self.config_service.get_config(config_path)
        access_token = config.get("credentials", {}).get("access_token") if config else None
        if not access_token:
            raise ValueError("Jira access token not found in configuration")
        return access_token

    async def _get_fresh_datasource(self) -> JiraDataSource:
        """
        Get JiraDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            JiraDataSource with current valid token
        """
        if not self.external_client:
            raise Exception("Jira client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config_path = OAUTH_JIRA_CONFIG_PATH.format(connector_id=self.connector_id)
        config = await self.config_service.get_config(config_path)

        if not config:
            raise Exception("Jira configuration not found")

        # Extract fresh OAuth access token
        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client using get_token() method
        internal_client = self.external_client.get_client()
        current_token = internal_client.get_token()

        # Update client's token if it changed (mutation) - set_token() is atomic
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.set_token(fresh_token)

        # Return datasource with updated client
        return JiraDataSource(self.external_client)

    # ============================================================================
    # Filter Options
    # ============================================================================

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for Jira filters with pagination.

        Supports:
        - project_keys: All available Jira projects

        Args:
            filter_key: Filter field name
            page: Page number (1-indexed)
            limit: Items per page
            search: Search text to filter project names/keys
            cursor: Not used (Jira uses startAt-based pagination)

        Returns:
            FilterOptionsResponse with options and pagination metadata
        """
        if filter_key == "project_keys":
            return await self._get_project_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_project_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch available Jira projects with pagination.

        Uses search_projects API with optional search term filtering.
        Jira uses startAt/maxResults pagination (not cursor-based).
        """
        # Get fresh datasource with refreshed OAuth token
        datasource = await self._get_fresh_datasource()

        # Calculate startAt for pagination (Jira uses 0-based startAt)
        start_at = (page - 1) * limit

        try:
            # Jira search_projects supports a query parameter for filtering by name or key.
            # Passing None is handled by the API, so we can directly assign search.
            query = search

            # Fetch projects using the search_projects API.
            # No expand parameter needed - we only use 'key' and 'name' which are in default response.
            response = await datasource.search_projects(
                maxResults=limit,
                startAt=start_at,
                query=query
            )

            if not response or response.status != HttpStatusCode.OK.value:
                raise RuntimeError(
                    f"Failed to fetch projects: HTTP {response.status if response else 'No response'}"
                )

            response_data = self._safe_json_parse(response, "project search")
            if response_data is None:
                raise RuntimeError("Failed to parse project search response")
            projects_list = response_data.get("values", [])

            # Use Jira's isLast flag as the source of truth for pagination.
            is_last = response_data.get("isLast", False)
            has_more = not is_last

            # Convert to FilterOption objects.
            options = [
                FilterOption(
                    id=project.get("key"),  # Use key as id since filter expects keys.
                    label=f"{project.get('name', '')} ({project.get('key', '')})"
                )
                for project in projects_list
                if project.get("key") and project.get("name")
            ]

            return FilterOptionsResponse(
                success=True,
                options=options,
                page=page,
                limit=limit,
                has_more=has_more,
                cursor=None  # Jira doesn't use cursor-based pagination.
            )
        except Exception as e:
            self.logger.error(f"❌ Error fetching projects: {e}")
            raise RuntimeError(f"Failed to fetch project options: {str(e)}")

    # ============================================================================
    # Sync Orchestration
    # ============================================================================

    async def run_sync(self) -> None:
        """
        Run sync of Jira projects and issues - only new/updated tickets
        """
        try:
            if not self.cloud_id:
                await self.init()

            # Load sync and indexing filters (loaded in run_sync to ensure latest values)
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.connector_id,
                self.logger
            )

            users = await self.data_entities_processor.get_all_active_users()

            if not users:
                self.logger.info("ℹ️ No users found")
                return

            # Fetch and sync users (pseudo-group migration)
            jira_users = await self._fetch_users()

            # Fetch and sync user groups (returns mapping for role resolution)
            groups_members_map = await self._sync_user_groups(jira_users)

            # Get project_keys filter if configured (to fetch only those projects)
            allowed_keys = None
            project_keys_operator = None
            if self.sync_filters:
                project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
                if project_keys_filter:
                    allowed_keys = project_keys_filter.get_value(default=[])
                    project_keys_operator = project_keys_filter.get_operator()
                    if allowed_keys:
                        # Extract operator value string (handles both enum and string)
                        operator_value = project_keys_operator.value if hasattr(project_keys_operator, 'value') else str(project_keys_operator) if project_keys_operator else "in"
                        action = "Excluding" if operator_value == "not_in" else "Including"
                        self.logger.info(f"🔍 Project keys filter: {action} projects: {allowed_keys}")
                    else:
                        self.logger.info("🔍 Project keys filter is empty, will fetch no projects")
            # Fetch projects
            projects, raw_projects = await self._fetch_projects(allowed_keys, project_keys_operator)

            # Sync project roles BEFORE RecordGroups
            project_keys_for_roles = [proj.short_name for proj, _ in projects]
            await self._sync_project_roles(project_keys_for_roles, jira_users, groups_members_map)

            # Sync project lead roles
            await self._sync_project_lead_roles(raw_projects, jira_users)

            # Create RecordGroups and its permissions
            await self.data_entities_processor.on_new_record_groups(projects)

            # Sync issues for all projects
            last_sync_time = await self._get_issues_sync_checkpoint()
            sync_stats = await self._sync_all_project_issues(projects, jira_users, last_sync_time)

            # Update sync checkpoint and handle deletions
            await self._update_issues_sync_checkpoint(sync_stats, len(projects))
            await self._handle_issue_deletions(last_sync_time)

            self.logger.info(
                f"✅ Jira sync completed. Total: {sync_stats['total_synced']} issues "
                f"(New: {sync_stats['new_count']}, Updated: {sync_stats['updated_count']})"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during Jira sync: {e}", exc_info=True)
            raise

    # ============================================================================
    # Sync Points & Checkpoints
    # ============================================================================

    async def _get_issues_sync_checkpoint(self) -> Optional[int]:
        """
        Get global sync checkpoint.
        """
        try:
            sync_point_data = await self.issues_sync_point.read_sync_point("issues_global")
            last_sync_time = sync_point_data.get("last_sync_time") if sync_point_data else None
            return last_sync_time
        except Exception:
            return None

    async def _update_issues_sync_checkpoint(self, stats: Dict[str, int], project_count: int) -> None:
        """
        Update global sync checkpoint.
        """
        if stats["total_synced"] > 0 or project_count > 0:
            current_time = get_epoch_timestamp_in_ms()
            sync_point_data = {
                "last_sync_time": current_time
            }
            await self.issues_sync_point.update_sync_point("issues_global", sync_point_data)

    async def _get_project_sync_checkpoint(self, project_key: str) -> Dict[str, Any]:
        """
        Get project-specific sync checkpoint.

        Returns:
            Dict with last_sync_time and last_issue_updated
        """
        sync_point_key = f"project_{project_key}"
        return await self.issues_sync_point.read_sync_point(sync_point_key)

    async def _update_project_sync_checkpoint(
        self,
        project_key: str,
        last_sync_time: Optional[int] = None,
        last_issue_updated: Optional[int] = None
    ) -> None:
        """
        Update project-specific sync checkpoint.

        Args:
            project_key: Project key (e.g., "PROJ")
            last_sync_time: Timestamp when checkpoint was updated (metadata only)
            last_issue_updated: Updated timestamp of last processed issue (used for resume AND next incremental sync)
        """
        sync_point_key = f"project_{project_key}"

        # Read existing to preserve values not being updated
        existing = await self._get_project_sync_checkpoint(project_key)

        sync_point_data = {
            "last_sync_time": last_sync_time if last_sync_time is not None else existing.get("last_sync_time"),
            "last_issue_updated": last_issue_updated if last_issue_updated is not None else existing.get("last_issue_updated")
        }

        await self.issues_sync_point.update_sync_point(sync_point_key, sync_point_data)

    async def _handle_issue_deletions(self, global_last_sync_time: Optional[int]) -> None:
        """
        Detect and handle issue deletions via Audit API.
        """
        audit_sync_key = "issues_audit_deletions"

        try:
            audit_sync_point_data = await self.issues_sync_point.read_sync_point(audit_sync_key)
            audit_last_sync_time = audit_sync_point_data.get("last_sync_time") if audit_sync_point_data else None
        except Exception:
            audit_last_sync_time = None

        deletion_check_time = audit_last_sync_time or global_last_sync_time

        if deletion_check_time:
            await self._detect_and_handle_deletions(deletion_check_time)

            # Update audit sync checkpoint
            await self.issues_sync_point.update_sync_point(
                audit_sync_key,
                {"last_sync_time": get_epoch_timestamp_in_ms()}
            )

    # ============================================================================
    # Deletion Handling
    # ============================================================================

    async def _detect_and_handle_deletions(self, last_sync_time: int) -> int:
        """
        Detect and handle deleted issues using Jira Audit API.
        """
        try:
            self.logger.info("🔍 Checking for deleted issues via Audit API...")

            # Convert timestamp to ISO format
            from_date = datetime.fromtimestamp(
                last_sync_time / 1000,
                tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            to_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            # Fetch audit records for issue deletions
            deleted_issue_keys = await self._fetch_deleted_issues_from_audit(from_date, to_date)

            if not deleted_issue_keys:
                self.logger.info("ℹ️ No deleted issues found in audit log")
                return 0

            # Handle each deletion
            deleted_count = 0
            for issue_key in deleted_issue_keys:
                try:
                    await self._handle_deleted_issue(issue_key)
                    deleted_count += 1
                except Exception as e:
                    self.logger.error(f"❌ Error handling deleted issue {issue_key}: {e}")
                    continue

            return deleted_count

        except Exception as e:
            self.logger.error(f"❌ Error detecting deletions: {e}", exc_info=True)
            return 0

    async def _fetch_deleted_issues_from_audit(
        self,
        from_date: str,
        to_date: str
    ) -> List[str]:
        """
        Fetch deleted issue keys from Jira Audit API.
        """
        deleted_issue_keys = []
        offset = 0
        limit = AUDIT_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_audit_records(
                    offset=offset,
                    limit=limit,
                    from_=from_date,
                    to=to_date
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch audit records: {response.text()}")
                    break

                audit_data = response.json()
                records = audit_data.get("records", [])

                if not records:
                    break

                # Filter for issue deletion events
                for record in records:
                    object_item = record.get("objectItem", {})
                    type_name = object_item.get("typeName")

                    # Check if this is an issue deletion
                    if type_name == "ISSUE_DELETE":
                        issue_key = object_item.get("name")
                        if issue_key:
                            deleted_issue_keys.append(issue_key)
                            self.logger.debug(f"Audit: Issue {issue_key} deleted at {record.get('created')}")

                # Check pagination
                total = audit_data.get("total", 0)
                if offset + len(records) >= total:
                    break

                offset += limit

            except Exception as e:
                self.logger.error(f"❌ Error fetching audit records at offset {offset}: {e}")
                break

        return deleted_issue_keys

    async def _handle_deleted_issue(self, issue_key: str) -> None:
        """
        Handle deletion of an issue following Jira's cascade behavior.

        Jira's cascade deletion behavior:
        - Epic deletion: Tasks/Stories under the Epic are NOT deleted (they just lose their epic link)
        - Task/Story deletion: Jira deletes all subtasks automatically
        - Subtask deletion: Only the subtask is deleted

        Our cascade deletion mirrors this:
        - Epic deletion: Only delete the Epic and its direct attachments
        - Task/Story deletion: Delete subtasks (and their attachments) + direct attachments
        - Subtask deletion: Only delete attachments

        Comments are stored as blocks within issue BlocksContainer, so they're deleted with the issue.
        """
        try:
            self.logger.info(f"🗑️ Handling deletion of issue {issue_key}")

            issue_id = None
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_issue(issueIdOrKey=issue_key)

                if response.status == HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Issue {issue_key} still exists in Jira (not deleted, maybe moved?)")
                    return

            except Exception:
                pass

            async with self.data_store_provider.transaction() as tx_store:

                # Get issue record by issue key using direct query
                issue_record = await tx_store.get_record_by_issue_key(
                    connector_id=self.connector_id,
                    issue_key=issue_key
                )

                if not issue_record:
                    self.logger.warning(f"⚠️ Issue {issue_key} not found in database (already deleted or never synced?)")
                    return

                issue_id = issue_record.external_record_id
                record_internal_id = issue_record.id

                # Check if this is an Epic - Epics don't cascade delete their child tasks
                issue_type = getattr(issue_record, 'type', None)
                # Handle both enum (Type.EPIC) and string ("EPIC") cases
                if issue_type:
                    # If it's an enum, get the value; otherwise use as string
                    type_value = issue_type.value if hasattr(issue_type, 'value') else str(issue_type)
                    is_epic = type_value.upper() == 'EPIC'
                else:
                    is_epic = False

                self.logger.info(
                    f"✅ Found issue {issue_key} (type: {issue_type}, is_epic: {is_epic}) with internal ID {record_internal_id}, external ID {issue_id}"
                )

                child_issue_count = 0

                # Only delete child tickets if this is NOT an Epic
                # Jira doesn't delete tasks when Epic is deleted - they just lose the epic link
                if not is_epic:
                    # Delete child subtasks - recursively deletes their subtasks and attachments
                    child_issue_count = await self._delete_issue_children(
                        issue_id,
                        RecordType.TICKET,
                        tx_store
                    )
                else:
                    self.logger.info(f"📋 Epic {issue_key} deleted - child tasks/stories will remain (Jira behavior)")

                # Delete direct child attachments of this issue (applies to all issue types)
                attachment_count = await self._delete_issue_children(
                    issue_id,
                    RecordType.FILE,
                    tx_store
                )

                # Delete the issue itself and all its edges
                await tx_store.delete_records_and_relations(
                    record_key=record_internal_id,
                    hard_delete=True
                )

                if is_epic:
                    self.logger.info(
                        f"🗑️ Deleted Epic {issue_key} ({attachment_count} direct attachments)"
                    )
                else:
                    self.logger.info(
                        f"🗑️ Deleted issue {issue_key} and its hierarchy "
                        f"({child_issue_count} subtasks, {attachment_count} direct attachments)"
                    )

        except Exception as e:
            self.logger.error(f"❌ Error handling deleted issue {issue_key}: {e}", exc_info=True)

    async def _delete_issue_children(
        self,
        parent_issue_id: str,
        child_type: RecordType,
        tx_store
    ) -> int:
        """
        Recursively delete all child records of a given type for a deleted issue.

        For TICKET children (subtasks):
        - First recursively deletes any nested subtasks (TICKET children)
        - Then deletes their attachments (FILE children)
        - Finally deletes the TICKET record itself

        For FILE children (attachments):
        - Simply deletes the attachment records

        Note: This is called only for Task/Story deletion (not Epic deletion).
        Epics don't cascade delete their child tasks - that's handled by the caller.
        This ensures: Task → Subtasks → Attachments at each level.

        Comments are stored as blocks within issue BlocksContainer, not as separate records.
        """
        try:
            deleted_count = 0
            # Map child type to readable name (TICKET covers tasks, stories, and subtasks)
            child_type_name = {
                RecordType.TICKET: "child issue",
                RecordType.FILE: "attachment"
            }.get(child_type, str(child_type))

            # Direct query by parent_external_record_id and record_type - efficient
            child_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=parent_issue_id,
                record_type=child_type.value
            )

            for record in child_records:
                # If deleting a TICKET, recursively delete its children first (subtasks AND attachments)
                if child_type == RecordType.TICKET:
                    child_issue_id = record.external_record_id

                    # First, recursively delete any subtasks of this task/story
                    # This handles the full hierarchy: Epic → Task → Subtask
                    nested_subtask_count = await self._delete_issue_children(
                        child_issue_id,
                        RecordType.TICKET,
                        tx_store
                    )
                    if nested_subtask_count > 0:
                        self.logger.debug(f"  Deleted {nested_subtask_count} nested subtasks for {child_issue_id}")

                    # Then delete attachments of this task/story
                    nested_attachment_count = await self._delete_issue_children(
                        child_issue_id,
                        RecordType.FILE,
                        tx_store
                    )
                    if nested_attachment_count > 0:
                        self.logger.debug(f"  Deleted {nested_attachment_count} attachments for {child_issue_id}")

                # Delete record and all its edges (indexer cleanup handled automatically)
                await tx_store.delete_records_and_relations(
                    record_key=record.id,
                    hard_delete=True
                )
                deleted_count += 1
                self.logger.debug(f"  Deleted {child_type_name} {record.external_record_id}")

            return deleted_count

        except Exception as e:
            self.logger.error(f"❌ Error deleting {child_type_name}s for issue {parent_issue_id}: {e}")
            return 0

    # ============================================================================
    # User, Role & Group Management
    # ============================================================================

    async def _fetch_users(self) -> List[AppUser]:
        """
        Fetch all active Jira users using DataSource
        """

        if not self.data_source:
            raise ValueError("DataSource not initialized")

        users: List[Dict[str, Any]] = []
        start_at = 0
        max_results_per_request = USER_PAGE_SIZE

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_users(
                query='',
                maxResults=max_results_per_request,
                startAt=start_at
            )

            if response.status != HttpStatusCode.OK.value:
                raise Exception(f"Failed to fetch users: {response.text()}")

            users_batch = self._safe_json_parse(response, "users fetch")
            if users_batch is None:
                self.logger.error("Failed to parse users response, stopping user fetch")
                break

            if isinstance(users_batch, list):
                batch_users = users_batch
            else:
                batch_users = users_batch.get("values", [])

            if not batch_users:
                break

            users.extend(batch_users)

            if len(batch_users) < max_results_per_request:
                break

            start_at += max_results_per_request

        app_users: List[AppUser] = []

        for user in users:
            account_id = user.get("accountId")

            # Only include active users
            if not user.get("active", True):
                continue

            # Skip users without email address
            email = user.get("emailAddress")
            if not email:
                continue

            app_user = AppUser(
                app_name=Connectors.JIRA,
                connector_id=self.connector_id,
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user.get("displayName", email),
                is_active=user.get("active", True)
            )
            app_users.append(app_user)

        if app_users:
            self.logger.info(f"👥 Synced {len(app_users)} Jira users")
            await self.data_entities_processor.on_new_app_users(app_users)
            # Migrate pseudo-group permissions to real users (for users now with emails)
            for user in app_users:
                if user.email and "@" in user.email and user.source_user_id:
                    try:
                        await self.data_entities_processor.migrate_group_to_user_by_external_id(
                            group_external_id=user.source_user_id,
                            user_email=user.email,
                            connector_id=self.connector_id
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to migrate pseudo-group permissions for user {user.email}: {e}"
                        )
                        continue

        return app_users

    async def _fetch_application_roles_to_groups_mapping(self) -> Dict[str, List[Dict[str, str]]]:
        """
        Fetch all application roles and their associated groups.
        """
        if hasattr(self, '_app_roles_cache') and self._app_roles_cache:
            return self._app_roles_cache

        mapping: Dict[str, List[Dict[str, str]]] = {}

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_application_roles()

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch application roles: {response.text()}")
                return {}

            roles_data = response.json()

            for role in roles_data:
                role_key = role.get("key")
                group_details = role.get("groupDetails", [])

                if role_key and group_details:
                    mapping[role_key] = [
                        {"groupId": g.get("groupId"), "name": g.get("name")}
                        for g in group_details
                        if g.get("groupId")
                    ]
                    self.logger.debug(f"ApplicationRole '{role_key}' → {len(mapping[role_key])} groups")

            # Cache the result
            self._app_roles_cache = mapping
            self.logger.info(f"🔐 Fetched {len(mapping)} application roles with group mappings")

        except Exception as e:
            self.logger.error(f"❌ Error fetching application roles: {e}", exc_info=True)

        return mapping

    async def _sync_user_groups(self, jira_users: List[AppUser]) -> Dict[str, List[AppUser]]:
        """
        Sync user groups and return a mapping of group_id/name -> list of AppUser members.
        This mapping is used to resolve group members for project roles.
        """
        try:
            self.logger.info("🚀 Starting Jira user group synchronization")

            # Fetch all groups
            groups = await self._fetch_groups()
            if not groups:
                self.logger.info("ℹ️ No groups found in Jira")
                return {}

            self.logger.info(f"👥 Found {len(groups)} groups. Fetching members...")

            # Create email -> AppUser lookup for efficient matching
            user_by_email = {user.email.lower(): user for user in jira_users if user.email}

            user_groups_batch = []
            # Mapping: group_id -> members, group_name -> members (for role actor lookup)
            groups_members_map: Dict[str, List[AppUser]] = {}

            for group in groups:
                try:
                    group_id = group.get("groupId")
                    group_name = group.get("name")

                    if not group_id or not group_name:
                        continue

                    self.logger.debug(f"Processing group: {group_name} ({group_id})")

                    # Create AppUserGroup (always create, even if no members)
                    user_group = AppUserGroup(
                        app_name=Connectors.JIRA,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group_name,
                        org_id=self.data_entities_processor.org_id,
                        description=f"Jira user group: {group_name}"
                    )

                    # Fetch members for this group
                    member_emails = await self._fetch_group_members(group_id, group_name)

                    # Map member emails to AppUser objects
                    app_users = []
                    if member_emails:
                        for email in member_emails:
                            user = user_by_email.get(email.lower())
                            if user:
                                app_users.append(user)
                            else:
                                self.logger.warning(f"⚠️️ Member email {email} not found in synced users")

                    # Store mapping by both group_id and group_name for flexible lookup
                    groups_members_map[group_id] = app_users
                    groups_members_map[group_name] = app_users

                    # Add group to batch (with or without members)
                    user_groups_batch.append((user_group, app_users))

                    if app_users:
                        self.logger.debug(f"Group {group_name}: {len(app_users)} members")
                    else:
                        self.logger.debug(f"Group {group_name}: no members with email")

                except Exception as group_error:
                    self.logger.error(f"❌ Failed to process group {group.get('name')}: {group_error}")
                    continue

            # Save all groups in one batch
            if user_groups_batch:
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)
            else:
                self.logger.info("ℹ️ No groups with valid members to sync")

            return groups_members_map

        except Exception as e:
            self.logger.error(f"❌ Error syncing user groups: {e}")
            return {}

    async def _fetch_groups(self) -> List[Dict[str, Any]]:
        """
        Fetch all Jira groups using the bulk_get_groups API.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        groups: List[Dict[str, Any]] = []
        start_at = 0
        max_results = GROUP_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.bulk_get_groups(
                    startAt=start_at,
                    maxResults=max_results
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.error(f"Failed to fetch groups: {response.text()}")
                    break

                groups_data = response.json()
                batch_groups = groups_data.get("values", [])

                if not batch_groups:
                    break

                groups.extend(batch_groups)

                # Check pagination
                is_last = groups_data.get("isLast", False)
                if is_last:
                    break

                start_at += len(batch_groups)

                # Also break if we got less than requested (safety check)
                if len(batch_groups) < max_results:
                    break

            except Exception as e:
                self.logger.error(f"❌ Error fetching groups at offset {start_at}: {e}")
                break

        self.logger.info(f"👥 Fetched {len(groups)} total groups")
        return groups

    async def _fetch_group_members(self, group_id: str, group_name: str) -> List[str]:
        """
        Fetch all members of a Jira group.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        member_emails: List[str] = []
        start_at = 0
        max_results = GROUP_MEMBER_PAGE_SIZE

        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_users_from_group(
                    groupname=group_name,
                    includeInactiveUsers=False,
                    startAt=start_at,
                    maxResults=max_results
                )

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch members for group {group_name}: {response.text()}")
                    break

                members_data = response.json()
                batch_members = members_data.get("values", [])

                if not batch_members:
                    break

                # Extract emails from members
                for member in batch_members:
                    email = member.get("emailAddress")
                    if email:
                        member_emails.append(email)

                # Check pagination
                is_last = members_data.get("isLast", False)
                if is_last:
                    break

                start_at += len(batch_members)

                # Also break if we got less than requested
                if len(batch_members) < max_results:
                    break

            except Exception as e:
                self.logger.error(f"❌ Error fetching members for group {group_name}: {e}")
                break

        return member_emails

    async def _sync_project_roles(
        self,
        project_keys: List[str],
        jira_users: List[AppUser],
        groups_members_map: Dict[str, List[AppUser]] = None
    ) -> None:
        """
        Sync project roles as AppRole entities.
        groups_members_map: Mapping of group_id/name -> list of AppUser members (from _sync_user_groups)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if groups_members_map is None:
            groups_members_map = {}

        self.logger.info(f"🔐 Syncing project roles for {len(project_keys)} projects...")

        # Build email -> AppUser lookup for fast member resolution
        user_by_email: Dict[str, AppUser] = {
            user.email.lower(): user
            for user in jira_users
            if user.email
        }

        # Also build accountId -> AppUser lookup
        user_by_account_id: Dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

        roles_to_sync: List[Tuple[AppRole, List[AppUser]]] = []
        total_roles = 0
        total_members = 0

        for project_key in project_keys:
            try:
                # Step 1: Get all project roles for this project
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_project_roles(projectIdOrKey=project_key)

                if response.status != HttpStatusCode.OK.value:
                    self.logger.warning(f"⚠️ Failed to fetch roles for project {project_key}: {response.status}")
                    continue

                roles_dict = response.json()

                if not roles_dict:
                    self.logger.debug(f"No roles found for project {project_key}")
                    continue

                # Step 2: For each role, fetch role details including actors
                for role_name, role_url in roles_dict.items():
                    try:
                        # Skip app-only roles
                        if role_name == "atlassian-addons-project-access":
                            continue

                        # Extract role ID from URL
                        role_id = role_url.rstrip('/').split('/')[-1]

                        # Fetch role details with actors
                        role_datasource = await self._get_fresh_datasource()
                        role_response = await role_datasource.get_project_role(
                            projectIdOrKey=project_key,
                            id=int(role_id),
                            excludeInactiveUsers=True
                        )

                        if role_response.status != HttpStatusCode.OK.value:
                            self.logger.warning(f"  {project_key}: Failed to fetch role {role_name}: {role_response.status}")
                            continue

                        role_data = role_response.json()
                        actors = role_data.get("actors", [])

                        # Extract available fields from role_data
                        role_name_display = role_data.get("name", role_name)

                        # Build AppRole with external_id matching Permission format
                        app_role = AppRole(
                            app_name=Connectors.JIRA,
                            connector_id=self.connector_id,
                            source_role_id=f"{project_key}_{role_id}",
                            name=f"{project_key} - {role_name_display}",
                            org_id=self.data_entities_processor.org_id,
                            source_created_at=None,  # Jira API doesn't provide role creation timestamp
                            source_updated_at=None   # Jira API doesn't provide role update timestamp
                        )

                        # Step 3: Extract member users from actors
                        member_users: List[AppUser] = []

                        for actor in actors:
                            actor_type = actor.get("type", "")

                            if actor_type == "atlassian-user-role-actor":
                                # Direct user actor
                                actor_user = actor.get("actorUser", {})
                                account_id = actor_user.get("accountId")
                                email = actor_user.get("emailAddress")

                                # Try to find user by accountId first, then by email
                                user = None
                                if account_id:
                                    user = user_by_account_id.get(account_id)
                                if not user and email:
                                    user = user_by_email.get(email.lower())

                                if user:
                                    member_users.append(user)
                                else:
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: User not found - "
                                        f"accountId={account_id}, email={email}"
                                    )

                            elif actor_type == "atlassian-group-role-actor":
                                # Group actor - get group members from already-fetched groups
                                group_name = actor.get("name") or actor.get("displayName")
                                group_id = actor.get("groupId")

                                # Try to find group members by group_id first, then by name
                                group_members = []
                                if group_id and group_id in groups_members_map:
                                    group_members = groups_members_map[group_id]
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: Group actor '{group_name}' (id: {group_id}) "
                                        f"found {len(group_members)} members"
                                    )
                                elif group_name and group_name in groups_members_map:
                                    group_members = groups_members_map[group_name]
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                        f"found {len(group_members)} members"
                                    )
                                else:
                                    self.logger.debug(
                                        f"  {project_key}/{role_name}: Group actor '{group_name}' "
                                        f"(id: {group_id}) not found in synced groups"
                                    )

                                # Add all group members directly to role members (USER->ROLE, not GROUP->ROLE)
                                member_users.extend(group_members)

                        roles_to_sync.append((app_role, member_users))
                        total_roles += 1
                        total_members += len(member_users)

                    except Exception as role_error:
                        self.logger.error(
                            f"  {project_key}: Error processing role {role_name}: {role_error}"
                        )
                        continue

            except Exception as project_error:
                self.logger.error(f"❌ Error syncing roles for project {project_key}: {project_error}")
                continue

        # Step 4: Sync all roles in batch
        if roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(roles_to_sync)
            self.logger.info(
                f"✅ Synced {total_roles} project roles with {total_members} direct user members"
            )
        else:
            self.logger.info("ℹ️ No project roles to sync")

    async def _sync_project_lead_roles(
        self,
        raw_projects: List[Dict[str, Any]],
        jira_users: List[AppUser]
    ) -> None:
        """
        Sync project lead as AppRole for each project.
        """

        # Build accountId -> AppUser lookup
        user_by_account_id: Dict[str, AppUser] = {
            user.source_user_id: user
            for user in jira_users
            if user.source_user_id
        }

        lead_roles_to_sync: List[Tuple[AppRole, List[AppUser]]] = []
        total_leads = 0

        # Iterate through raw project data already fetched with lead
        for project in raw_projects:
            try:
                project_key = project.get("key")
                lead_data = project.get("lead")

                # Create AppRole for project lead (even if no lead exists - to clean up old edges)
                # Extract project timestamps if available
                project_created = project.get("createdAt")
                project_updated = project.get("updatedAt")

                app_role = AppRole(
                    app_name=Connectors.JIRA,
                    connector_id=self.connector_id,
                    source_role_id=f"{project_key}_projectLead",
                    name=f"{project_key} - Project Lead",
                    org_id=self.data_entities_processor.org_id,
                    source_created_at=self._parse_jira_timestamp(project_created) if project_created else None,
                    source_updated_at=self._parse_jira_timestamp(project_updated) if project_updated else None
                )

                # Determine lead user (if any)
                lead_user = None
                if lead_data:
                    lead_account_id = lead_data.get("accountId")
                    lead_display_name = lead_data.get("displayName", "Unknown")

                    if lead_account_id:
                        # Find the lead user in synced users
                        lead_user = user_by_account_id.get(lead_account_id)

                        if not lead_user:
                            self.logger.warning(f"Project lead {lead_display_name} not found in synced users for {project_key}")
                    else:
                        self.logger.warning(f"No accountId for project lead in {project_key}")
                else:
                    self.logger.debug(f"No lead for project {project_key} - syncing role to clean up old edges")

                # Always sync the role (even with empty members list) to ensure old edges are deleted
                members = [lead_user] if lead_user else []
                lead_roles_to_sync.append((app_role, members))
                total_leads += 1


            except Exception as lead_error:
                self.logger.error(f"Error processing lead for project {project.get('key')}: {lead_error}")
                continue

        # Sync all project lead roles in batch
        if lead_roles_to_sync:
            await self.data_entities_processor.on_new_app_roles(lead_roles_to_sync)
        else:
            self.logger.info("No project leads to sync")

    # ============================================================================
    # Security Level Permissions
    # ============================================================================

    async def _get_project_security_scheme_id(self, project_key: str) -> Optional[str]:
        """
        Fetch security scheme ID for a project (if it has issue-level security).

        Args:
            project_key: Project key (e.g., "PROJ")

        Returns:
            Security scheme ID string, or None if no scheme or on error.
        """
        try:
            datasource = await self._get_fresh_datasource()
            scheme_response = await datasource.get_project_issue_security_scheme(
                projectKeyOrId=project_key
            )

            if scheme_response.status == HttpStatusCode.OK.value:
                scheme_data = scheme_response.json()
                scheme_id = scheme_data.get("id")
                if scheme_id:
                    self.logger.debug(f"🔒 {project_key}: Security scheme {scheme_id}")
                    return scheme_id
            else:
                self.logger.debug(f"🔒 {project_key}: No security scheme (status {scheme_response.status})")
            return None
        except Exception as e:
            self.logger.error(f"🔒 {project_key}: Error fetching security scheme: {e}")
            return None

    async def _fetch_security_level_permissions(
        self,
        project_key: str,
        security_level_id: str,
        project_security_scheme_id: str,
        assignee_email: Optional[str] = None,
        reporter_email: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None
    ) -> List[Permission]:
        """
        Fetch security level members from API for this specific issue.
        """

        # Fetch members for this level with pagination
        all_members = []
        start_at = 0
        max_results = 50

        try:
            while True:
                datasource = await self._get_fresh_datasource()
                members_response = await datasource.get_issue_security_level_members(
                    issueSecuritySchemeId=int(project_security_scheme_id),
                    issueSecurityLevelId=[security_level_id],
                    startAt=start_at,
                    maxResults=max_results
                )

                if members_response.status != HttpStatusCode.OK.value:
                    if start_at == 0:
                        response_body = members_response.text()
                        self.logger.debug(
                            f"Failed to fetch members for security level {security_level_id}: "
                            f"status={members_response.status}, response_body={response_body}"
                        )
                    return []

                members_data = members_response.json()
                members_batch = members_data.get("values", [])

                if not members_batch:
                    break

                all_members.extend(members_batch)

                # Check pagination
                is_last = members_data.get("isLast", False)
                total = members_data.get("total", 0)

                if is_last or (total > 0 and start_at + len(members_batch) >= total):
                    break

                start_at += len(members_batch)

                # Also break if we got less than requested
                if len(members_batch) < max_results:
                    break

            if not all_members:
                self.logger.debug(f"Security level {security_level_id} has no members")
                return []

            # Parse all members into Permission objects
            permissions = await self._parse_security_level_members(
                all_members,
                project_key,
                assignee_email=assignee_email,
                reporter_email=reporter_email,
                assignee_account_id=assignee_account_id,
                reporter_account_id=reporter_account_id
            )

            self.logger.debug(
                f"Security level {security_level_id}: Found {len(all_members)} members "
                f"-> {len(permissions)} permissions"
            )
            return permissions

        except Exception as e:
            self.logger.error(f"Error fetching security level {security_level_id}: {e}")
            return []

    async def _get_user_email_by_account_id(self, account_id: str) -> Optional[str]:
        """
        Get user email by accountId via Jira GET /rest/api/3/user.
        Returns email if available, otherwise None.
        """
        if not account_id:
            return None
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_user(accountId=account_id)
            if response.status != HttpStatusCode.OK.value:
                return None
            data = response.json()
            email = data.get("emailAddress") or data.get("email")
            return email if email else None
        except Exception as e:
            self.logger.debug("Could not fetch user for accountId %s: %s", account_id, e)
            return None

    async def _get_or_create_pseudo_group(self, account_id: str) -> Optional[AppUserGroup]:
        """
        Get existing pseudo-group by accountId or create one if not found.
        Used when building permissions for a user whose email is not available;
        """
        async with self.data_store_provider.transaction() as tx_store:
            group = await tx_store.get_user_group_by_external_id(
                connector_id=self.connector_id,
                external_id=account_id,
            )
            if group:
                return group

        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.JIRA,
                connector_id=self.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX} {account_id}",
                org_id=self.data_entities_processor.org_id,
            )
            await self.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info(f"Created pseudo-group for user without email: {account_id}")
            return pseudo_group
        except Exception as e:
            self.logger.error(f"Failed to create pseudo-group for {account_id}: {e}")
            return None

    async def _parse_security_level_members(
        self,
        members: List[Dict],
        project_key: str,
        assignee_email: Optional[str] = None,
        reporter_email: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        reporter_account_id: Optional[str] = None
    ) -> List[Permission]:
        """
        Parse security level members into Permission objects.

        Holder types:
        - user: Individual user by accountId (fetch email, pseudo-group fallback)
        - group: Group by groupId
        - projectRole: Project role by roleId
        - applicationRole: Org-level access (all licensed users of that app role)
        - assignee: The issue's assignee
        - reporter: The issue's reporter
        - projectLead: The project lead (already synced as a role)
        """
        permissions = []
        seen_holders = set()

        for member in members:
            holder = member.get("holder", {})
            holder_type = holder.get("type")

            holder_value = holder.get("value") or holder.get("parameter") or holder_type
            holder_key = f"{holder_type}:{holder_value}"
            if holder_key in seen_holders:
                continue
            seen_holders.add(holder_key)

            if holder_type == "user":
                account_id = holder.get("parameter") or holder.get("value")
                if not account_id:
                    continue
                email = await self._get_user_email_by_account_id(account_id)
                if email:
                    permissions.append(Permission(entity_type=EntityType.USER, email=email, type=PermissionType.READ))
                else:
                    pseudo_group = await self._get_or_create_pseudo_group(account_id)
                    if pseudo_group:
                        self.logger.debug(f"Using pseudo-group for user {account_id} (no email available)")
                        permissions.append(Permission(
                            entity_type=EntityType.GROUP,
                            type=PermissionType.READ,
                            external_id=account_id,
                        ))

            elif holder_type == "group":
                group_id = holder.get("value")
                if group_id:
                    permissions.append(Permission(
                        entity_type=EntityType.GROUP,
                        external_id=group_id,
                        type=PermissionType.READ
                    ))

            elif holder_type == "projectRole":
                # Project role holder
                role_id = holder.get("parameter") or holder.get("value")
                if role_id:
                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_{role_id}",
                        type=PermissionType.READ
                    ))

            elif holder_type == "applicationRole":
                # Two cases: (1) no parameter = any logged-in user (org-level);
                # (2) parameter present = users with that application role (e.g. jira-software = Jira Software license)
                role_key = holder.get("parameter") or holder.get("value")
                external_id = role_key if role_key else "all_licensed_users"
                permissions.append(Permission(
                    entity_type=EntityType.ORG,
                    external_id=external_id,
                    type=PermissionType.READ
                ))

            elif holder_type == "assignee":
                if assignee_email:
                    permissions.append(Permission(
                        entity_type=EntityType.USER,
                        email=assignee_email,
                        type=PermissionType.READ
                    ))
                elif assignee_account_id:
                    pseudo_group = await self._get_or_create_pseudo_group(assignee_account_id)
                    if pseudo_group:
                        self.logger.debug(f"Using pseudo-group for assignee {assignee_account_id} (no email available)")
                        permissions.append(Permission(
                            entity_type=EntityType.GROUP,
                            type=PermissionType.READ,
                            external_id=assignee_account_id,
                        ))

            elif holder_type == "reporter":
                if reporter_email:
                    permissions.append(Permission(
                        entity_type=EntityType.USER,
                        email=reporter_email,
                        type=PermissionType.READ
                    ))
                elif reporter_account_id:
                    pseudo_group = await self._get_or_create_pseudo_group(reporter_account_id)
                    if pseudo_group:
                        self.logger.debug(f"Using pseudo-group for reporter {reporter_account_id} (no email available)")
                        permissions.append(Permission(
                            entity_type=EntityType.GROUP,
                            type=PermissionType.READ,
                            external_id=reporter_account_id,
                        ))

            elif holder_type == "projectLead":
                permissions.append(Permission(
                    entity_type=EntityType.ROLE,
                    external_id=f"{project_key}_projectLead",
                    type=PermissionType.READ
                ))

            else:
                self.logger.debug(f"Unknown security holder type: {holder_type}")

        return permissions

    # ============================================================================
    # Project Management
    # ============================================================================

    async def _fetch_projects_via_api(
        self,
        keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        fetch projects from Jira API with pagination.
        Pass keys=None to fetch all projects; pass keys=[...] to filter by project keys.
        Jira search_projects omits keys param when None; adds keys to query when list.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        projects: List[Dict[str, Any]] = []
        start_at = 0
        expand = ["description", "url", "permissions", "issueTypes", "lead"]

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.search_projects(
                maxResults=DEFAULT_MAX_RESULTS,
                startAt=start_at,
                keys=keys,
                expand=expand
            )

            if response.status != HttpStatusCode.OK.value:
                raise Exception(f"Failed to fetch projects: {response.text()}")

            projects_batch = self._safe_json_parse(response, "project search")
            if projects_batch is None:
                raise Exception("Failed to parse project search response")
            batch_projects = projects_batch.get("values", [])

            if not batch_projects:
                break

            projects.extend(batch_projects)
            start_at += len(batch_projects)

            total = projects_batch.get("total", 0)
            is_last = projects_batch.get("isLast", False)
            if is_last or (total > 0 and start_at >= total):
                break

        return projects

    async def _fetch_project_permission_scheme(
        self,
        project_key: str,
        app_roles_mapping: Dict[str, List[Dict[str, str]]] = None
    ) -> List[Permission]:
        """
        Fetch permission holders for a project from its Permission Scheme.

        Permission Schemes grant permissions (like BROWSE_PROJECTS) through different holder types:
        - group: Direct group permissions (e.g., "jira-software-users")
        - applicationRole: Product access (e.g., "jira-software") - resolved to associated groups
        - user: Individual user permissions (by accountId/email)
        - anyone: All authenticated users (org-level access)
        - projectRole: Project-specific roles (e.g., "Administrators", "Developers") inside that user or groups in role
        - projectLead: The project's designated lead user
        - sd.customer.portal.only: JSM portal customers (external users)
        - groupCustomField/userCustomField: Dynamic permissions based on issue fields

        """
        permissions: List[Permission] = []

        try:
            datasource = await self._get_fresh_datasource()

            # Step 1: Get the permission scheme assigned to this project
            scheme_response = await datasource.get_assigned_permission_scheme(
                projectKeyOrId=project_key,
                expand="all"
            )

            if scheme_response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch permission scheme for {project_key}: {scheme_response.text()}")
                return []

            scheme_data = scheme_response.json()
            scheme_id = scheme_data.get("id")

            # Step 2: Get all permission grants in this scheme
            grants_response = await datasource.get_permission_scheme_grants(
                schemeId=scheme_id,
                expand="all"
            )

            if grants_response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch permission grants for scheme {scheme_id}: {grants_response.text()}")
                return []

            grants_data = grants_response.json()
            permission_grants = grants_data.get("permissions", [])

            # Step 3: Filter for BROWSE_PROJECTS permission (determines who can see the project)
            relevant_permission_types = ["BROWSE_PROJECTS"]

            seen_holders = set()

            for grant in permission_grants:
                permission_name = grant.get("permission")

                if permission_name not in relevant_permission_types:
                    continue

                holder = grant.get("holder", {})
                holder_type = holder.get("type")
                holder_param = holder.get("parameter")
                holder_value = holder.get("value")

                # Create unique key for deduplication
                holder_key = f"{holder_type}:{holder_value or holder_param}"
                if holder_key in seen_holders:
                    continue
                seen_holders.add(holder_key)

                # Process different holder types and create Permission objects
                if holder_type == "group" and holder_value:
                    # Group has BROWSE_PROJECTS permission
                    permissions.append(Permission(
                        entity_type=EntityType.GROUP,
                        external_id=holder_value,
                        type=PermissionType.READ
                    ))

                elif holder_type == "applicationRole":
                    role_key = holder_param

                    if role_key and app_roles_mapping and role_key in app_roles_mapping:
                        role_groups = app_roles_mapping[role_key]
                        for group_info in role_groups:
                            group_id = group_info.get("groupId")
                            if group_id:
                                # Avoid duplicate if this group was already added directly
                                group_key = f"group:{group_id}"
                                if group_key not in seen_holders:
                                    seen_holders.add(group_key)
                                    permissions.append(Permission(
                                        entity_type=EntityType.GROUP,
                                        external_id=group_id,
                                        type=PermissionType.READ
                                    ))
                    else:
                        # Fallback: No mapping found or no role_key - treat as org-level handle any logged in user condition
                        fallback_name = role_key or "all_licensed_users"
                        permissions.append(Permission(
                            entity_type=EntityType.ORG,
                            external_id=fallback_name,
                            type=PermissionType.READ
                        ))

                elif holder_type == "user" and holder_param:
                    # Specific user has access
                    user_data = holder.get("user", {})
                    user_email = user_data.get("emailAddress")
                    if user_email:
                        permissions.append(Permission(
                            entity_type=EntityType.USER,
                            email=user_email,
                            type=PermissionType.READ
                        ))
                    else:
                        self.logger.warning(f"⚠️  {project_key}: User permission skipped - no email for accountId '{holder_param}'")

                elif holder_type == "anyone":
                    # All authenticated users have access handle public condition
                    permissions.append(Permission(
                        entity_type=EntityType.ORG,
                        external_id="anyone_authenticated",
                        type=PermissionType.READ
                    ))

                elif holder_type == "projectRole":
                    project_role = holder.get("projectRole", {})
                    role_name = project_role.get("name", f"Role_{holder_param}")
                    role_id = holder_param or project_role.get("id")

                    if role_name == "atlassian-addons-project-access":
                        continue

                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_{role_id}",
                        type=PermissionType.READ
                    ))

                elif holder_type == "sd.customer.portal.only":
                    # JSM Service Desk customers (portal access)
                    # These are external customers who only access via the service desk portal
                    # Their access is limited to their own tickets through the portal UI
                    self.logger.debug(f"  {project_key}: Skipping JSM portal customers (external users, not synced)")

                elif holder_type == "projectLead":
                    permissions.append(Permission(
                        entity_type=EntityType.ROLE,
                        external_id=f"{project_key}_projectLead",
                        type=PermissionType.READ
                    ))

                elif holder_type in ("groupCustomField", "userCustomField"):
                    continue

                else:
                    self.logger.warning(f"⚠️  {project_key}: Unknown holder type '{holder_type}' with param '{holder_param}' - skipping")

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Error fetching permission scheme for project {project_key}: {e}", exc_info=True)
            return []

    async def _build_record_groups_from_projects(
        self,
        projects: List[Dict[str, Any]],
        app_roles_mapping: Dict[str, List[Dict[str, str]]]
    ) -> List[Tuple[RecordGroup, List[Permission]]]:
        """
        Build (RecordGroup, permissions) for each raw project.
        Single place for record group creation and permission scheme resolution.
        """
        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []

        for project in projects:
            project_id = project.get("id")
            project_key = project.get("key")
            if not project_key:
                self.logger.debug("Skipping project with no key: id=%s", project_id)
                continue

            project_name = project.get("name")

            description = project.get("description")
            if description and isinstance(description, dict):
                description = adf_to_text(description)
            else:
                description = description or None

            record_group = RecordGroup(
                id=str(uuid4()),
                org_id=self.data_entities_processor.org_id,
                external_group_id=project_id,
                connector_id=self.connector_id,
                connector_name=Connectors.JIRA,
                name=project_name,
                short_name=project_key,
                group_type=RecordGroupType.PROJECT,
                description=description,
                web_url=project.get("url"),
            )

            project_permissions = await self._fetch_project_permission_scheme(project_key, app_roles_mapping)
            record_groups.append((record_group, project_permissions))

            if project_permissions:
                self.logger.info(f"🔐 Project {project_key}: {len(project_permissions)} permission grants from scheme")

        return record_groups

    async def _fetch_projects(
        self,
        project_keys: Optional[List[str]] = None,
        project_keys_operator: Optional[FilterOperatorType] = None
    ) -> Tuple[List[Tuple[RecordGroup, List[Permission]]], List[Dict[str, Any]]]:
        """
        Fetch projects using DataSource. Returns (record_groups, raw_projects).

        Args:
            project_keys: Optional list of project keys to include/exclude
            project_keys_operator: Optional filter operator (IN or NOT_IN)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        is_exclude = False
        if project_keys_operator:
            operator_value = project_keys_operator.value if hasattr(project_keys_operator, 'value') else str(project_keys_operator)
            is_exclude = operator_value == "not_in"

        if project_keys:
            if is_exclude:
                self.logger.info(f"📁 Fetching all projects, excluding: {project_keys}")
                all_projects = await self._fetch_projects_via_api(keys=None)
                excluded_set = set(project_keys)
                projects = [p for p in all_projects if p.get("key") and p.get("key") not in excluded_set]
            else:
                self.logger.info(f"📁 Fetching specific projects using keys filter: {project_keys}")
                projects = await self._fetch_projects_via_api(keys=project_keys)
        else:
            self.logger.info("📁 Fetching all projects")
            projects = await self._fetch_projects_via_api(keys=None)

        app_roles_mapping = await self._fetch_application_roles_to_groups_mapping()
        record_groups = await self._build_record_groups_from_projects(projects, app_roles_mapping)

        return record_groups, projects

    # ============================================================================
    # Issue Sync
    # ============================================================================

    async def _sync_all_project_issues(
        self,
        projects: List[Tuple[RecordGroup, List[Permission]]],
        jira_users: List[AppUser],
        last_sync_time: Optional[int]
    ) -> Dict[str, int]:
        """Sync issues for all projects and return statistics."""
        total_synced = 0
        new_count = 0
        updated_count = 0

        for project, _ in projects:
            try:
                project_stats = await self._sync_project_issues(
                    project, jira_users, last_sync_time
                )
                total_synced += project_stats["total_synced"]
                new_count += project_stats["new_count"]
                updated_count += project_stats["updated_count"]
            except Exception as e:
                self.logger.error(f"❌ Error processing issues for project {project.short_name}: {e}", exc_info=True)
                continue

        return {
            "total_synced": total_synced,
            "new_count": new_count,
            "updated_count": updated_count
        }

    async def _sync_project_issues(
        self,
        project: RecordGroup,
        jira_users: List[AppUser],
        global_last_sync_time: Optional[int]
    ) -> Dict[str, int]:
        """
        Sync issues for a single project with project-level sync points.
        Processes in batches and updates sync point after each batch for fault tolerance.
        """
        project_key = project.short_name
        project_id = project.external_group_id

        # Fetch security scheme ID for this project (if exists)
        project_security_scheme_id = await self._get_project_security_scheme_id(project_key)

        # Read project sync point
        project_sync_data = await self._get_project_sync_checkpoint(project_key)

        # Check if this is a new project (no checkpoint exists)
        is_new_project = not project_sync_data or (
            not project_sync_data.get("last_issue_updated") and
            not project_sync_data.get("last_sync_time")
        )

        # Use last_issue_updated if available (works for both resume and incremental sync)
        resume_from_timestamp = None
        if not is_new_project:
            resume_from_timestamp = project_sync_data.get("last_issue_updated")
            if not resume_from_timestamp:
                resume_from_timestamp = project_sync_data.get("last_sync_time") or global_last_sync_time

        # Set project_last_sync_time for fallback in _fetch_issues_batched
        project_last_sync_time = project_sync_data.get("last_sync_time") or global_last_sync_time if not is_new_project else None

        if is_new_project:
            self.logger.info(f"🆕 New project detected: {project_key}. Fetching ALL issues (no timestamp filter).")
        elif resume_from_timestamp:
            self.logger.info(f"🔄 Starting sync for project {project_key} from timestamp {resume_from_timestamp}")

        # Fetch and process issues in batches
        total_issues_processed = 0
        batch_number = 0
        last_issue_updated_in_batch = None
        stats = {"new_count": 0, "updated_count": 0}

        async for issues_batch, has_more, last_issue_timestamp in self._fetch_issues_batched(
            project_key,
            project_id,
            jira_users,
            project_last_sync_time,
            resume_from_timestamp,
            project_security_scheme_id
        ):
            batch_number += 1
            batch_size = len(issues_batch)

            # Track last issue updated timestamp for resume capability
            if last_issue_timestamp:
                last_issue_updated_in_batch = last_issue_timestamp

            # Skip processing if no actual changes (all issues filtered out as unchanged)
            if not issues_batch:
                # Update checkpoint for skipped batch to advance timestamp and prevent re-fetch
                # Safe because these issues were already in DB (just unchanged)
                if last_issue_updated_in_batch:
                    current_time = get_epoch_timestamp_in_ms()
                    await self._update_project_sync_checkpoint(
                        project_key,
                        last_sync_time=current_time,
                        last_issue_updated=last_issue_updated_in_batch
                    )
                continue

            self.logger.info(f"📦 Processing batch {batch_number} for project {project_key}: {batch_size} records")

            # Process this batch
            await self._process_new_records(issues_batch, stats)
            total_issues_processed += batch_size

            # Update checkpoint AFTER successful processing
            if last_issue_updated_in_batch:
                current_time = get_epoch_timestamp_in_ms()
                await self._update_project_sync_checkpoint(
                    project_key,
                    last_sync_time=current_time,
                    last_issue_updated=last_issue_updated_in_batch
                )

        # Final checkpoint update if we processed any issues (ensures last_sync_time stays close to last_issue_updated)
        if last_issue_updated_in_batch:
            current_time = get_epoch_timestamp_in_ms()
            await self._update_project_sync_checkpoint(
                project_key,
                last_sync_time=current_time,
                last_issue_updated=last_issue_updated_in_batch
            )

        if total_issues_processed == 0:
            self.logger.info(f"ℹ️ No new/updated issues for project {project_key}")
        else:
            self.logger.info(f"✅ Synced {total_issues_processed} records for project {project_key}")

        return {
            "total_synced": total_issues_processed,
            "new_count": stats["new_count"],
            "updated_count": stats["updated_count"]
        }

    def _build_issues_jql(
        self,
        project_key: str,
        last_sync_time: Optional[int] = None,
        resume_from_timestamp: Optional[int] = None,
    ) -> str:
        """Build the JQL query for fetching issues of a project, applying sync/date filters and timestamp checkpoints."""
        jql_conditions = [f'project = "{project_key}"']

        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after, modified_before = modified_filter.get_value(default=(None, None)) if modified_filter else (None, None)

        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after, created_before = created_filter.get_value(default=(None, None)) if created_filter else (None, None)

        if resume_from_timestamp:
            modified_after = resume_from_timestamp
            self.logger.info(f"🔄 Starting from timestamp: {resume_from_timestamp}")
        elif modified_after:
            if last_sync_time:
                modified_after = max(modified_after, last_sync_time)
        elif last_sync_time:
            modified_after = last_sync_time

        if modified_after:
            dt = datetime.fromtimestamp(modified_after / 1000, tz=timezone.utc)
            jql_conditions.append(f'updated > "{dt.strftime("%Y-%m-%d %H:%M")}"')

        if modified_before:
            dt = datetime.fromtimestamp(modified_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'updated <= "{dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_after:
            dt = datetime.fromtimestamp(created_after / 1000, tz=timezone.utc)
            jql_conditions.append(f'created >= "{dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_before:
            dt = datetime.fromtimestamp(created_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'created <= "{dt.strftime("%Y-%m-%d %H:%M")}"')

        return " AND ".join(jql_conditions) + " ORDER BY updated ASC, id ASC"

    async def _fetch_issues_batched(
        self,
        project_key: str,
        project_id: str,
        users: List[AppUser],
        last_sync_time: Optional[int] = None,
        resume_from_timestamp: Optional[int] = None,
        project_security_scheme_id: Optional[str] = None
    ) -> AsyncGenerator[Tuple[List[Tuple[Record, List[Permission]]], bool, Optional[int]], None]:
        """
        Fetch issues for a project in batches, yielding processed records.
        Uses timestamp-based pagination for reliable resume capability.

        Yields:
            Tuple of (records_batch, has_more, last_issue_updated)
            - records_batch: List of (Record, permissions) tuples for this batch
            - has_more: True if there are more batches, False if this is the last batch
            - last_issue_updated: Updated timestamp of last issue in this batch (for resume)
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        if not self.cloud_id:
            self.logger.error("❌ cloud_id is not set. Cannot fetch issues.")
            return

        jql = self._build_issues_jql(project_key, last_sync_time, resume_from_timestamp)
        self.logger.info(f"🔍 JQL Query for {project_key}: {jql}")

        page_count = 0
        next_page_token = None
        # Track last issue updated timestamp for resume (starts with resume_from_timestamp if resuming)
        last_issue_updated = resume_from_timestamp

        while True:
            page_count += 1

            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_and_reconsile_issues_using_jql_post(
                    jql=jql,
                    maxResults=DEFAULT_MAX_RESULTS,
                    nextPageToken=next_page_token,
                    fields=ISSUE_SEARCH_FIELDS,
                    expand="renderedFields,changelog"
                )

                if response.status != HttpStatusCode.OK.value:
                    raise Exception(f"Failed to fetch issues: {response.text()}")

                issues_batch_response = self._safe_json_parse(response, f"issues fetch for {project_key}")
                if issues_batch_response is None:
                    raise Exception(f"Failed to parse issues response for project {project_key}")

            except Exception as e:
                self.logger.error(f"❌ Failed to fetch issues for project {project_key}: {e}")
                raise

            batch_issues = issues_batch_response.get("issues", [])
            new_page_token = issues_batch_response.get("nextPageToken")

            if not batch_issues:
                # No more issues - yield empty to signal completion
                yield [], False, last_issue_updated
                break

            # Get updated timestamp of last issue in this batch (for resume capability)
            if batch_issues:
                last_issue = batch_issues[-1]
                fields = last_issue.get("fields", {})
                updated_str = fields.get("updated")
                if updated_str:
                    last_issue_updated = self._parse_jira_timestamp(updated_str)

            # Build records for this batch
            async with self.data_store_provider.transaction() as tx_store:
                records_batch = await self._build_issue_records(
                    batch_issues,
                    project_id,
                    project_key,
                    users,
                    project_security_scheme_id,
                    tx_store
                )

            self.logger.debug(f"📦 Fetched batch {page_count}: {len(batch_issues)} issues -> {len(records_batch)} records (last updated: {last_issue_updated})")

            # Determine if there are more pages
            has_more = new_page_token and new_page_token != next_page_token

            # Yield this batch with resume info
            # But we store last_issue_updated timestamp for resume on next sync
            yield records_batch, has_more, last_issue_updated

            if not has_more:
                break

            # Use token for next page (valid during this sync session)
            next_page_token = new_page_token

    async def _process_new_records(
        self,
        records_with_permissions: List[Tuple[Record, List[Permission]]],
        stats: Dict[str, int]
    ) -> None:
        """
        Process records (new and updated) in batches.
        on_new_records internally handles both new and updated.
        """
        batch_size = BATCH_PROCESSING_SIZE

        for i in range(0, len(records_with_permissions), batch_size):
            batch = records_with_permissions[i:i + batch_size]
            await self.data_entities_processor.on_new_records(batch)

            # Update stats
            new_in_batch = sum(1 for r, _ in batch if r.version == 0)
            stats["new_count"] += new_in_batch
            stats["updated_count"] += len(batch) - new_in_batch

            # Log batch summary
            issues_count = sum(1 for r, _ in batch if isinstance(r, TicketRecord))
            files_count = sum(1 for r, _ in batch if isinstance(r, FileRecord))
            self.logger.info(
                f"📦 Batch {i//batch_size + 1}: {issues_count} issues, "
                f"{files_count} attachments ({new_in_batch} new, {len(batch) - new_in_batch} updated)"
            )

    # ============================================================================
    # Issue Processing
    # ============================================================================

    def _parse_issue_links(self, issue: Dict[str, Any]) -> List[RelatedExternalRecord]:
        """
        Parse issue links from Jira API response and convert to RelatedExternalRecord objects.

        Only processes OUTWARD links to avoid creating duplicate edges.
        When Issue A has a link to Issue B:
        - Issue A has outwardIssue: B (we create A → B edge here)
        - Issue B has inwardIssue: A (we skip this to avoid duplicate)

        Relation types mapped:
        - "blocks" → BLOCKS
        - "duplicates" → DUPLICATES
        - "clones" → CLONES
        - "depends on" → DEPENDS_ON
        - "causes" → CAUSES
        - "relates to" → RELATED
        - Unknown types → RELATED (fallback)

        This ensures exactly one edge is created per link relationship.
        """
        related_records: List[RelatedExternalRecord] = []

        # Handle edge case where issue might be None or not a dict
        if not issue or not isinstance(issue, dict):
            return related_records

        fields = issue.get("fields", {})
        if not fields or not isinstance(fields, dict):
            return related_records

        issue_links = fields.get("issuelinks", [])

        # Handle edge case where issuelinks might not be a list
        if not issue_links or not isinstance(issue_links, list):
            return related_records

        for link in issue_links:
            # Skip if link is not a dict
            if not isinstance(link, dict):
                continue
            try:
                # Get link type information
                link_type = link.get("type", {})
                if not link_type:
                    continue

                # Only process outward links to create a single edge per relationship
                # Skip inward links to avoid creating duplicate edges
                if "outwardIssue" not in link:
                    continue

                linked_issue = link.get("outwardIssue")
                if not linked_issue:
                    continue

                linked_issue_id = linked_issue.get("id")
                if not linked_issue_id:
                    continue

                # Use outward description for relation type mapping
                raw_tag = link_type.get("outward", link_type.get("name", ""))

                # Map Jira link type description to standard RecordRelations enum
                mapped_relation_type = map_relationship_type(raw_tag)

                # Use mapped type if valid enum, otherwise use RELATED as fallback
                if isinstance(mapped_relation_type, RecordRelations):
                    relation_type = mapped_relation_type
                else:
                    # Fallback to RELATED for unmapped/custom link types
                    relation_type = RecordRelations.RELATED

                related_record = RelatedExternalRecord(
                    external_record_id=linked_issue_id,
                    record_type=RecordType.TICKET,
                    relation_type=relation_type,
                )
                related_records.append(related_record)

            except Exception as e:
                self.logger.warning(f"Failed to parse issue link: {e}")
                continue

        return related_records

    def _extract_issue_data(
        self,
        issue: Dict[str, Any],
        user_by_account_id: Dict[str, AppUser]
    ) -> Dict[str, Any]:
        """
        Extract and process issue data from raw Jira issue dictionary.
        """
        issue_id = issue.get("id")
        issue_key = issue.get("key")
        fields = issue.get("fields", {})
        issue_summary = fields.get("summary") or f"Issue {issue_key}"

        # Extract description (ADF to text conversion)
        description_adf = fields.get("description")
        description_text = adf_to_text(description_adf) if description_adf else None

        # Extract issue type and hierarchy information
        issue_type_obj = fields.get("issuetype", {})
        raw_issue_type = issue_type_obj.get("name") if issue_type_obj else None
        # Map issue type to standardized value using value mapper
        issue_type = self.value_mapper.map_type(raw_issue_type)
        hierarchy_level = issue_type_obj.get("hierarchyLevel") if issue_type_obj else None

        # Extract parent issue information
        parent_obj = fields.get("parent")
        parent_external_id = parent_obj.get("id") if parent_obj else None
        parent_key = parent_obj.get("key") if parent_obj else None

        # Categorize issue type based on hierarchy level
        is_epic = hierarchy_level == 1
        is_subtask = hierarchy_level == -1

        # Build record name with issue key in square brackets at start for better searchability
        issue_name = f"[{issue_key}] {issue_summary}" if issue_key else issue_summary

        # Add issue type to description for full searchability
        if issue_type and description_text:
            description = f"Issue Type: {issue_type}\n\n{description_text}"
        elif issue_type:
            description = f"Issue Type: {issue_type}"
        else:
            description = description_text

        # Extract and map status to standardized value
        status_obj = fields.get("status", {})
        raw_status = status_obj.get("name") if status_obj else None
        status = self.value_mapper.map_status(raw_status)

        # Extract and map priority to standardized value
        priority_obj = fields.get("priority", {})
        raw_priority = priority_obj.get("name") if priority_obj else None
        priority = self.value_mapper.map_priority(raw_priority)

        # Extract user information by accountId (email not available in issue fields)
        creator = fields.get("creator")
        creator_account_id = creator.get("accountId") if creator else None
        creator_name = creator.get("displayName") if creator else None
        creator_email = None
        if creator_account_id and creator_account_id in user_by_account_id:
            creator_email = user_by_account_id[creator_account_id].email

        # Reporter (can be changed, unlike creator which is immutable)
        reporter = fields.get("reporter")
        reporter_account_id = reporter.get("accountId") if reporter else None
        reporter_name = reporter.get("displayName") if reporter else None
        reporter_email = None
        if reporter_account_id and reporter_account_id in user_by_account_id:
            reporter_email = user_by_account_id[reporter_account_id].email

        assignee = fields.get("assignee")
        assignee_account_id = assignee.get("accountId") if assignee else None
        assignee_name = assignee.get("displayName") if assignee else None
        assignee_email = None
        if assignee_account_id and assignee_account_id in user_by_account_id:
            assignee_email = user_by_account_id[assignee_account_id].email

        created_at = self._parse_jira_timestamp(fields.get("created"))
        updated_at = self._parse_jira_timestamp(fields.get("updated"))

        # Extract security level
        security_obj = fields.get("security")
        security_level_id = security_obj.get("id") if security_obj else None

        return {
            "issue_id": issue_id,
            "issue_key": issue_key,
            "issue_name": issue_name,
            "description": description,
            "issue_type": issue_type,
            "hierarchy_level": hierarchy_level,
            "is_epic": is_epic,
            "is_subtask": is_subtask,
            "parent_external_id": parent_external_id,
            "parent_key": parent_key,
            "status": status,
            "priority": priority,
            "creator_email": creator_email,
            "creator_name": creator_name,
            "reporter_email": reporter_email,
            "reporter_name": reporter_name,
            "reporter_account_id": reporter_account_id,
            "assignee_email": assignee_email,
            "assignee_name": assignee_name,
            "assignee_account_id": assignee_account_id,
            "created_at": created_at,
            "updated_at": updated_at,
            "security_level_id": security_level_id,
        }

    async def _build_issue_records(
        self,
        issues: List[Dict[str, Any]],
        project_id: str,
        project_key: str,
        users: List[AppUser],
        project_security_scheme_id: Optional[str],
        tx_store
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Build issue records with permissions from raw issue data, respecting Jira hierarchy
        """
        all_records: List[Tuple[Record, List[Permission]]] = []
        skipped_unchanged_count = 0

        # Use the user-facing site URL for weburl construction
        atlassian_domain = self.site_url if self.site_url else ""

        # Create accountId -> AppUser lookup for matching issue creators/assignees
        user_by_account_id = {user.source_user_id: user for user in users if user.source_user_id}

        for issue in issues:
            # Extract and process issue data
            issue_data = self._extract_issue_data(issue, user_by_account_id)

            issue_id = issue_data["issue_id"]
            issue_key = issue_data["issue_key"]
            issue_name = issue_data["issue_name"]
            issue_type = issue_data["issue_type"]
            is_epic = issue_data["is_epic"]
            is_subtask = issue_data["is_subtask"]
            parent_external_id = issue_data["parent_external_id"]
            status = issue_data["status"]
            priority = issue_data["priority"]
            creator_email = issue_data["creator_email"]
            creator_name = issue_data["creator_name"]
            reporter_email = issue_data["reporter_email"]
            reporter_name = issue_data["reporter_name"]
            reporter_account_id = issue_data["reporter_account_id"]
            assignee_email = issue_data["assignee_email"]
            assignee_name = issue_data["assignee_name"]
            assignee_account_id = issue_data["assignee_account_id"]
            created_at = issue_data["created_at"]
            updated_at = issue_data["updated_at"]
            security_level_id = issue_data["security_level_id"]

            # Determine record-level permissions based on security field
            if security_level_id and project_security_scheme_id:

                self.logger.debug(f"🔒 Fetching security level permissions for issue {issue_key} with security level {security_level_id} and project security scheme {project_security_scheme_id}")
                # Issue has security level and project has security scheme - fetch permissions via API
                permissions = await self._fetch_security_level_permissions(
                    project_key=project_key,
                    security_level_id=security_level_id,
                    project_security_scheme_id=project_security_scheme_id,
                    assignee_email=assignee_email,
                    reporter_email=reporter_email,
                    assignee_account_id=assignee_account_id,
                    reporter_account_id=reporter_account_id
                )

                if not permissions:
                    self.logger.debug(
                        f"Issue {issue_key}: Security level {security_level_id} has no members, "
                        "will inherit from project"
                    )
            else:
                # No security field - pass empty list
                permissions = []

            # Get fields for attachments (needed by _fetch_issue_attachments)
            fields = issue.get("fields", {})

            # Handle attachment deletions based on changelog for this issue
            await self._handle_attachment_deletions_from_changelog(issue, tx_store)

            # Check for existing record (works for both Epics and regular issues)
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=issue_id
            )

            record_id = existing_record.id if existing_record else str(uuid4())
            is_new = existing_record is None

            # Only increment version if issue content actually changed
            is_issue_changed = False
            if is_new:
                version = 0
                is_issue_changed = True
                self.logger.debug(f"🆕 New issue found: {issue_key} (external_id: {issue_id})")
            elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                version = existing_record.version + 1
                is_issue_changed = True
                self.logger.debug(f"📝 Issue {issue_key} updated, incrementing version to {version}")
            else:
                version = existing_record.version if existing_record else 0
                # Skip unchanged issues silently - no need to log every unchanged issue

            # Skip processing if issue is unchanged
            if not is_issue_changed:
                skipped_unchanged_count += 1
                continue

            # Set parent relationships and record group
            external_record_group_id = project_id
            record_group_type = RecordGroupType.PROJECT
            parent_record_id = None
            parent_record_type = None

            if is_epic:
                # Epic is a Record that belongs to Project RecordGroup
                pass
            elif parent_external_id and not is_subtask:
                # Story/Task with Epic parent → Epic is now a Record, not RecordGroup
                parent_record_id = parent_external_id
                parent_record_type = RecordType.TICKET
            elif is_subtask and parent_external_id:
                # Sub-task → has parent Record (creates PARENT_CHILD edge in recordRelations)
                parent_record_id = parent_external_id
                parent_record_type = RecordType.TICKET

            # Every ticket is a root node
            issue_record = TicketRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                priority=priority,
                status=status,
                type=issue_type,
                creator_email=creator_email,
                creator_name=creator_name,
                reporter_email=reporter_email,
                reporter_name=reporter_name,
                assignee=assignee_name,
                assignee_email=assignee_email,
                external_record_id=issue_id,
                external_revision_id=str(updated_at) if updated_at else None,
                record_name=issue_name,
                record_type=RecordType.TICKET,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.JIRA,
                connector_id=self.connector_id,
                record_group_type=record_group_type,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_record_id,
                parent_record_type=parent_record_type,
                version=version,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=f"{atlassian_domain}/browse/{issue_key}" if atlassian_domain else None,
                source_created_at=created_at,
                source_updated_at=updated_at,
                created_at=created_at,
                updated_at=updated_at,
                inherit_permissions=True,
                preview_renderable=True,
                is_dependent_node=False,  # Tickets are not dependent
                parent_node_id=None,  # Tickets have no parent node
            )

            # Set indexing status based on filters
            if self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES):
                issue_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            # Parse issue links and set related_external_records for creating LINKED_TO edges
            related_external_records = self._parse_issue_links(issue)
            if related_external_records:
                issue_record.related_external_records = related_external_records
                self.logger.debug(f"🔗 Issue {issue_key} has {len(related_external_records)} linked issues")

            all_records.append((issue_record, permissions))

            # Fetch attachments and create FileRecords
            try:
                attachment_records = await self._fetch_issue_attachments(
                    issue_id,
                    issue_key,
                    fields,
                    permissions,
                    external_record_group_id,
                    tx_store,
                    parent_node_id=issue_record.id,
                )
                if attachment_records:
                    all_records.extend(attachment_records)
            except Exception as e:
                self.logger.error(f"❌ Failed to fetch attachments for issue {issue_key}: {e}")

        # Log summary only if there were skipped issues
        if skipped_unchanged_count > 0:
            self.logger.debug(f"⏭️ Skipped {skipped_unchanged_count} unchanged issue(s)")

        return all_records

    # ============================================================================
    # Attachments
    # ============================================================================

    async def _fetch_issue_attachments(
        self,
        issue_id: str,
        issue_key: str,
        issue_fields: Dict[str, Any],
        parent_permissions: List[Permission],
        parent_record_group_id: str,
        tx_store,
        parent_node_id: Optional[str] = None,
    ) -> List[Tuple[FileRecord, List[Permission]]]:
        """
        Fetch attachments for an issue from issue fields.
        All attachments have the issue as their parent.
        """
        attachment_records: List[Tuple[FileRecord, List[Permission]]] = []

        try:
            # Get attachments from issue fields (already fetched in ISSUE_SEARCH_FIELDS)
            attachments = issue_fields.get("attachment", [])

            if not attachments:
                return []

            # Construct web URL for attachments - use issue browse URL
            weburl = None
            if self.site_url and issue_key:
                weburl = f"{self.site_url}/browse/{issue_key}"

            for attachment in attachments:
                attachment_id = attachment.get("id")
                if not attachment_id:
                    continue

                # Check for existing attachment record
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"attachment_{attachment_id}"
                )

                # Get attachment metadata
                filename = attachment.get("filename", "unknown")
                file_size = attachment.get("size", 0)
                mime_type = attachment.get("mimeType", MimeTypes.UNKNOWN.value)

                # Parse timestamps
                created_str = attachment.get("created")
                created_at = self._parse_jira_timestamp(created_str) if created_str else 0

                # Determine version (increment if file was updated)
                record_id = existing_record.id if existing_record else None
                is_new = existing_record is None

                if is_new:
                    version = 0
                elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != created_at:
                    version = existing_record.version + 1
                else:
                    version = existing_record.version if existing_record else 0

                # Create FileRecord using helper method
                attachment_record = self._create_attachment_file_record(
                    attachment_id=str(attachment_id),
                    filename=filename,
                    mime_type=mime_type,
                    file_size=file_size,
                    created_at=created_at,
                    parent_issue_id=issue_id,
                    parent_node_id=parent_node_id,
                    project_id=parent_record_group_id,
                    weburl=weburl,
                    record_id=record_id,
                    version=version,
                )

                # Attachments inherit permissions from parent issue
                attachment_permissions = parent_permissions.copy()

                attachment_records.append((attachment_record, attachment_permissions))

            self.logger.info(f"📎 Returning {len(attachment_records)} attachment records for issue {issue_key}")
            return attachment_records

        except Exception as e:
            self.logger.error(f"Failed to fetch attachments for issue {issue_key}: {e}", exc_info=True)
            return []

    def _extract_attachment_filenames_from_wiki(self, text: str) -> Set[str]:
        """
        Extract attachment filenames from Jira wiki markup.
        Pattern: !filename.ext|...!
        """
        filenames = set()
        for match in re.finditer(r"!([^!]+)!", text):
            inner = match.group(1)
            filename_part = inner.split("|", 1)[0].strip()
            if filename_part:
                filenames.add(filename_part.lower())
        return filenames

    async def _delete_attachment_record(
        self,
        record: Record,
        issue_key: str,
        tx_store,
        reason: str = "based on changelog event"
    ) -> None:
        """
        Helper method to delete an attachment record and log the action.
        """
        await tx_store.delete_records_and_relations(
            record_key=record.id,
            hard_delete=True
        )
        filename_info = f" (filename: {record.record_name})" if record.record_name else ""
        self.logger.info(
            f"🗑️ Deleted attachment {record.external_record_id}{filename_info} "
            f"for issue {issue_key} {reason}"
        )

    async def _find_attachment_record_by_id(
        self,
        attachment_id: str,
        tx_store
    ) -> Optional[Record]:
        """
        Find attachment record by ID
        """
        external_id = f"attachment_{attachment_id}"

        # First try new-style external ID (attachment_<id>)
        record = await tx_store.get_record_by_external_id(
            connector_id=self.connector_id,
            external_id=external_id,
        )

        return record

    async def _handle_attachment_deletions_from_changelog(
        self,
        issue: Dict[str, Any],
        tx_store,
    ) -> None:
        """
        Detect and delete attachments that were removed from an issue using changelog.
        For each such attachment ID we delete the corresponding FileRecord (if it exists).
        """
        try:
            changelog = issue.get("changelog")
            if not changelog:
                return

            histories = changelog.get("histories", [])
            if not histories:
                return

            issue_key = issue.get("key")
            issue_id = issue.get("id")
            if not issue_id:
                return

            # Get current attachments once (used in multiple places)
            fields = issue.get("fields", {}) or {}
            attachments = fields.get("attachment", []) or []

            # Map current attachments by filename for inline attachment resolution
            attachments_by_filename: Dict[str, List[str]] = {}
            current_attachment_ids: Set[str] = set()

            for att in attachments:
                att_id = att.get("id")
                filename = att.get("filename")
                if att_id:
                    current_attachment_ids.add(str(att_id))
                if att_id and filename:
                    key = str(filename).lower()
                    attachments_by_filename.setdefault(key, []).append(str(att_id))

            # Collect unique deleted attachment IDs from changelog
            deleted_attachment_ids: Set[str] = set()
            unmatched_removed_filenames: Set[str] = set()
            has_description_change = False

            # Parse changelog to find deleted attachments
            for history in histories:
                items = history.get("items", [])
                for item in items:
                    field = item.get("field")
                    field_id = item.get("fieldId")

                    # Track description field changes (inline attachment removed from description)
                    if field_id == "description" or field in ("description", "Description"):
                        has_description_change = True
                        from_str = item.get("fromString") or ""
                        to_str = item.get("toString") or ""

                        # Extract filenames from wiki markup
                        from_filenames = self._extract_attachment_filenames_from_wiki(from_str)
                        to_filenames = self._extract_attachment_filenames_from_wiki(to_str)
                        removed_filenames = from_filenames - to_filenames

                        # Map removed filenames to concrete attachment IDs
                        for filename_key in removed_filenames:
                            matched_ids = attachments_by_filename.get(filename_key, [])
                            if matched_ids:
                                deleted_attachment_ids.update(matched_ids)
                            else:
                                # Filename not found in current attachments - will search DB by filename
                                unmatched_removed_filenames.add(filename_key)

                    # Check for explicit attachment deletion events
                    if field in ("Attachment", "attachment") or field_id == "attachment":
                        from_id = item.get("from")
                        to_id = item.get("to")
                        # Deletion event: attachment removed from issue
                        if from_id and (to_id is None or to_id == ""):
                            deleted_attachment_ids.add(str(from_id))

            # Case 1: Delete attachments with explicit IDs from changelog
            deleted_count = 0
            for attachment_id in deleted_attachment_ids:
                record = await self._find_attachment_record_by_id(attachment_id, tx_store)
                if not record:
                    self.logger.debug(
                        f"Attachment attachment_{attachment_id} referenced in changelog for issue {issue_key} "
                        "but no matching FileRecord found"
                    )
                    continue

                await self._delete_attachment_record(record, issue_key, tx_store)
                deleted_count += 1

            # Early return if no unmatched filenames to handle
            if not unmatched_removed_filenames and not has_description_change:
                if deleted_count > 0:
                    self.logger.info(
                        f"🗑️ Deleted {deleted_count} attachments for issue {issue_key} based on changelog events"
                    )
                return

            # Case 2: Handle unmatched filenames and description changes
            existing_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=issue_id,
                record_type=RecordType.FILE.value
            )

            deleted_by_filename = 0
            for record in existing_records:
                # Check if this record matches an unmatched removed filename
                record_filename_lower = record.record_name.lower() if record.record_name else ""
                if unmatched_removed_filenames and record_filename_lower in unmatched_removed_filenames:
                    await self._delete_attachment_record(
                        record,
                        issue_key,
                        tx_store,
                        "because it was removed from description"
                    )
                    deleted_count += 1
                    deleted_by_filename += 1
                    continue

                # Check if attachment still exists in Jira
                # Extract attachment ID from external_record_id (handles both "attachment_<id>" and legacy formats)
                external_id = record.external_record_id
                attachment_id = external_id.replace("attachment_", "") if external_id.startswith("attachment_") else external_id
                if attachment_id in current_attachment_ids:
                    continue

                # Attachment no longer exists at source -> delete
                await self._delete_attachment_record(
                    record,
                    issue_key,
                    tx_store,
                    "because it no longer exists in Jira"
                )
                deleted_count += 1

            # Log summary if any deletions occurred
            if deleted_count > 0:
                if deleted_by_filename > 0:
                    self.logger.info(
                        f"Deleted {deleted_count} attachments for issue {issue_key} "
                        f"({deleted_by_filename} by filename match, {deleted_count - deleted_by_filename} by ID diff)"
                    )
                else:
                    self.logger.info(
                        f"🗑️ Deleted {deleted_count} attachments for issue {issue_key} that were removed from Jira"
                    )

        except Exception as e:
            issue_key = issue.get("key", "unknown")
            self.logger.error(
                f"❌ Error handling attachment deletions from changelog for issue {issue_key}: {e}",
                exc_info=True,
            )

    # ============================================================================
    # BlockGroups & Blocks Parsing
    # ============================================================================

    async def _fetch_issue_comments_with_pagination(
        self,
        issue_id_or_key: str,
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch all comments for an issue using the dedicated comments endpoint with pagination.
        Returns list of all comments with proper parentId field for threading.
        """
        datasource = await self._get_fresh_datasource()
        comments_data: List[Dict[str, Any]] = []
        start_at = 0

        while True:
            comments_response = await datasource.get_comments(
                issueIdOrKey=issue_id_or_key,
                startAt=start_at,
                maxResults=max_results
            )
            if comments_response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch comments for issue {issue_id_or_key}: {comments_response.status}")
                break

            comments_batch_data = comments_response.json()
            comments_batch = comments_batch_data.get("comments", [])
            comments_data.extend(comments_batch)

            total = comments_batch_data.get("total", 0)
            if start_at + len(comments_batch) >= total:
                break
            start_at += max_results

        return comments_data

    def _organize_issue_comments_to_threads(
        self,
        comments_data: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Group Jira comments by thread (parent comment) and sort by created timestamp.
        Returns list of threads, each thread is a list of comments sorted by created.

        Jira returns threaded comments using direct 'parentId' field (integer).
        - Top-level comments (no parentId) start their own thread
        - Replies grouped under their parent's thread_id
        - Each thread sorted by created timestamp (oldest first)
        - Threads sorted by first comment's created timestamp
        """
        if not comments_data:
            return []

        threads: Dict[str, List[Dict[str, Any]]] = {}

        for comment in comments_data:
            comment_id = comment.get("id", "")
            # Jira API returns parentId as direct integer field, not nested parent object
            parent_id = comment.get("parentId")
            # Thread ID is parent's ID if it's a reply (convert to string), or self ID if top-level
            thread_id = str(parent_id) if parent_id else comment_id
            if not thread_id:
                continue

            if thread_id not in threads:
                threads[thread_id] = []
            threads[thread_id].append(comment)

        # Sort each thread by created timestamp (oldest first)
        for thread_id in threads:
            threads[thread_id].sort(
                key=lambda c: self._parse_jira_timestamp(c.get("created", "")) or 0
            )

        # Sort threads by first comment's created timestamp (oldest thread first)
        sorted_threads = sorted(
            threads.values(),
            key=lambda t: self._parse_jira_timestamp(t[0].get("created", "")) or 0 if t else 0
        )

        return sorted_threads

    def _resolve_attachment_id_from_media(self, media_info: Dict[str, Any], filename_to_id: Dict[str, str]) -> Optional[str]:
        """Resolve ADF media node to attachment ID via filename matching."""
        media_filename = media_info.get("filename", "") or media_info.get("alt", "")
        if not media_filename:
            return None
        attachment_id = filename_to_id.get(media_filename)
        if not attachment_id:
            attachment_id = filename_to_id.get(media_filename.lower().strip())
        return attachment_id

    def _is_image_attachment(self, attachment_id: str, attachment_mime_types: Dict[str, str]) -> bool:
        """Check if attachment is an image based on MIME type."""
        mime_type = attachment_mime_types.get(attachment_id, "")
        return mime_type.startswith("image/")

    def _build_attachment_filename_lookup(
        self,
        attachment_children_map: Optional[Dict[str, ChildRecord]]
    ) -> Dict[str, str]:
        """Build filename -> attachment_id map for case-insensitive matching."""
        filename_to_id: Dict[str, str] = {}
        if attachment_children_map:
            for att_id, child_record in attachment_children_map.items():
                child_name = child_record.child_name
                if child_name:
                    filename_to_id[child_name] = att_id
                    filename_to_id[child_name.lower().strip()] = att_id
        return filename_to_id

    def _identify_comment_and_description_media(
        self,
        issue_data: Dict[str, Any],
        issue_description_adf: Optional[Dict[str, Any]],
        attachment_children_map: Optional[Dict[str, ChildRecord]],
        attachment_mime_types: Dict[str, str],
        filename_to_id: Dict[str, str]
    ) -> Tuple[Set[str], Set[str]]:
        """
        Pre-scan description and comments to identify attachment usage.
        Returns (comment_attachment_ids, description_image_ids).
        """
        comment_attachment_ids: Set[str] = set()
        description_image_ids: Set[str] = set()

        # Pre-scan comments to identify which attachments are used in comments
        comments_data = issue_data.get("comments", [])
        if attachment_children_map:
            for comment in comments_data:
                comment_body_adf = comment.get("body")
                if comment_body_adf and isinstance(comment_body_adf, dict):
                    for media_info in extract_media_from_adf(comment_body_adf):
                        attachment_id = self._resolve_attachment_id_from_media(media_info, filename_to_id)
                        if attachment_id and attachment_id in attachment_children_map:
                            comment_attachment_ids.add(attachment_id)

        # Extract media from description ADF - identify embedded images
        if issue_description_adf and isinstance(issue_description_adf, dict):
            for media_info in extract_media_from_adf(issue_description_adf):
                attachment_id = self._resolve_attachment_id_from_media(media_info, filename_to_id)
                if attachment_id and self._is_image_attachment(attachment_id, attachment_mime_types):
                    description_image_ids.add(attachment_id)

        return comment_attachment_ids, description_image_ids

    async def _create_description_blockgroup_with_content(
        self,
        issue_id: str,
        issue_key: str,
        issue_title: str,
        issue_description_adf: Optional[Dict[str, Any]],
        weburl: str,
        block_group_index: int
    ) -> BlockGroup:
        """Create the description BlockGroup with converted ADF content."""
        if issue_description_adf and isinstance(issue_description_adf, dict):
            description_content = await adf_to_text_with_images(
                issue_description_adf,
                self._create_media_fetcher(issue_id)
            )
        else:
            description_content = ""

        if not description_content:
            description_content = f"# {issue_title}" if issue_title else f"# Issue {issue_key or issue_id}"

        return BlockGroup(
            id=str(uuid4()),
            index=block_group_index,
            name=issue_title if issue_title else (f"{issue_key} - Description" if issue_key else "Issue Description"),
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Description for issue {issue_key}" if issue_key else "Issue description",
            source_group_id=f"{issue_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=weburl,
            requires_processing=True,
        )

    async def _create_comment_blockgroups_for_thread(
        self,
        thread_comments: List[Dict[str, Any]],
        issue_id: str,
        issue_key: str,
        weburl: str,
        thread_block_group_index: int,
        starting_comment_index: int,
        attachment_children_map: Optional[Dict[str, ChildRecord]],
        attachment_mime_types: Dict[str, str],
        filename_to_id: Dict[str, str]
    ) -> List[BlockGroup]:
        """Create BlockGroups for all comments in a thread."""
        comment_block_groups: List[BlockGroup] = []
        block_group_index = starting_comment_index

        for comment in thread_comments:
            comment_id = comment.get("id", "")
            comment_body_adf = comment.get("body")

            if not comment_body_adf:
                continue

            # Convert ADF comment body to markdown with base64 images
            if isinstance(comment_body_adf, dict):
                comment_body = await adf_to_text_with_images(
                    comment_body_adf,
                    self._create_media_fetcher(issue_id)
                )
            else:
                comment_body = str(comment_body_adf) if comment_body_adf else ""

            if not comment_body:
                continue

            # Build comment weburl
            if self.site_url and issue_key and comment_id:
                comment_weburl = f"{self.site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
            else:
                comment_weburl = weburl

            # Get author info
            author = comment.get("author", {})
            author_name = author.get("displayName", "Unknown")

            # Get file attachments used in this comment (images excluded - already as base64)
            comment_children: List[ChildRecord] = []
            if attachment_children_map and isinstance(comment_body_adf, dict):
                for media_info in extract_media_from_adf(comment_body_adf):
                    attachment_id = self._resolve_attachment_id_from_media(media_info, filename_to_id)
                    if attachment_id and attachment_id in attachment_children_map:
                        if not self._is_image_attachment(attachment_id, attachment_mime_types):
                            comment_children.append(attachment_children_map[attachment_id])

            # Create BlockGroup with sub_type=COMMENT
            comment_block_group = BlockGroup(
                id=str(uuid4()),
                index=block_group_index,
                parent_index=thread_block_group_index,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                name=f"Comment by {author_name}",
                description=f"Comment by {author_name}",
                source_group_id=comment_id,
                data=comment_body,
                format=DataFormat.MARKDOWN,
                weburl=comment_weburl,
                requires_processing=True,
                children_records=comment_children if comment_children else None,
            )
            comment_block_groups.append(comment_block_group)
            block_group_index += 1

        return comment_block_groups

    def _assign_description_children(
        self,
        attachment_children_map: Optional[Dict[str, ChildRecord]],
        comment_attachment_ids: Set[str],
        description_image_ids: Set[str]
    ) -> List[ChildRecord]:
        """
        Assign attachments to description children.
        Excludes: attachments used in comments and embedded images.
        """
        description_children: List[ChildRecord] = []
        if attachment_children_map:
            for attachment_id, child_record in attachment_children_map.items():
                if attachment_id in comment_attachment_ids:
                    continue  # Used in comment
                if attachment_id in description_image_ids:
                    continue  # Embedded image (already in content as base64)
                description_children.append(child_record)
        return description_children

    def _populate_blockgroup_hierarchy(self, block_groups: List[BlockGroup]) -> None:
        """Build and populate parent-child relationships for BlockGroups."""
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)

        # Collect all BlockGroup children
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)

        # Populate children arrays
        for bg in block_groups:
            if bg.index in blockgroup_children_map:
                child_bg_indices = sorted(blockgroup_children_map[bg.index])
                bg.children = BlockGroupChildren.from_indices(
                    block_indices=[],
                    block_group_indices=child_bg_indices
                )

    async def _parse_issue_to_blocks(
        self,
        issue_data: Dict[str, Any],
        issue_key: str,
        weburl: Optional[str] = None,
        attachment_children_map: Optional[Dict[str, ChildRecord]] = None,
        attachment_mime_types: Optional[Dict[str, str]] = None,
    ) -> BlocksContainer:
        """
        Parse Jira issue data into BlocksContainer with BlockGroups.

        FLOW:
        1. Build attachment lookup helpers (filename → attachment_id mapping)
        2. Pre-scan description & comments to identify which attachments go where
        3. Create description BlockGroup (index=0) with markdown content + base64 images
        4. Create comment thread BlockGroups (index=1+) and comment BlockGroups (nested)
        5. Assign attachments to description or comments based on usage
        6. Build BlockGroup parent-child hierarchy
        7. Return BlocksContainer

        STRUCTURE:
        - Description BlockGroup (index=0)
          └─ children: standalone attachments + embedded files (NOT images or comment attachments)
        - Thread BlockGroup (index=1+, parent=0)
          └─ Comment BlockGroup (index=N, parent=thread_index)
             └─ children: attachments used in that comment (NOT images)

        ATTACHMENT RULES:
        - Used in comment → assigned to that comment's children
        - Embedded image in description → excluded (already in content as base64)
        - Embedded file in description → included in description children
        - Standalone (not mentioned anywhere) → included in description children

        NOTE: Jira ADF media.attrs.id is a UUID token, NOT the attachment ID. We use FILENAME matching.
        """
        issue_id = issue_data.get("id", "")
        fields = issue_data.get("fields", {})
        issue_title = fields.get("summary", "")
        issue_description_adf = fields.get("description")

        if not weburl:
            raise ValueError("weburl is required when creating BlockGroup for issues")

        # Step 1: Build attachment lookup maps
        filename_to_id = self._build_attachment_filename_lookup(attachment_children_map)

        # Step 2: Pre-scan to identify which attachments go where
        comment_attachment_ids, description_image_ids = self._identify_comment_and_description_media(
            issue_data,
            issue_description_adf,
            attachment_children_map,
            attachment_mime_types or {},
            filename_to_id
        )

        # Step 3: Create description BlockGroup (index=0)
        block_groups: List[BlockGroup] = []
        block_group_index = 0
        description_block_group = await self._create_description_blockgroup_with_content(
            issue_id, issue_key, issue_title, issue_description_adf, weburl, block_group_index
        )
        block_groups.append(description_block_group)
        block_group_index += 1

        # Step 4: Create comment thread BlockGroups and comment BlockGroups
        comments_data = issue_data.get("comments", [])
        if isinstance(comments_data, dict):
            comments_data = comments_data.get("comments", [])

        if comments_data:
            sorted_threads = self._organize_issue_comments_to_threads(comments_data)

            for thread_comments in sorted_threads:
                if not thread_comments:
                    continue

                # Get thread ID from first comment (using parentId field)
                first_comment = thread_comments[0]
                first_comment_id = first_comment.get("id", "")
                parent_id = first_comment.get("parentId")
                thread_id = str(parent_id) if parent_id else first_comment_id

                # Create thread BlockGroup
                thread_block_group_index = block_group_index
                thread_block_group = BlockGroup(
                    id=str(uuid4()),
                    index=thread_block_group_index,
                    parent_index=0,
                    name=f"Comment Thread - {thread_id[:8]}" if thread_id else "Comment Thread",
                    type=GroupType.TEXT_SECTION,
                    sub_type=GroupSubType.COMMENT_THREAD,
                    description=f"Comment thread for issue {issue_key}" if issue_key else "Comment thread",
                    source_group_id=f"{issue_id}_thread_{thread_id}" if thread_id else f"{issue_id}_thread_{thread_block_group_index}",
                    weburl=weburl,
                    requires_processing=False,
                )
                block_groups.append(thread_block_group)
                block_group_index += 1

                # Create comment BlockGroups for this thread
                comment_blockgroups = await self._create_comment_blockgroups_for_thread(
                    thread_comments,
                    issue_id,
                    issue_key,
                    weburl,
                    thread_block_group_index,
                    block_group_index,
                    attachment_children_map,
                    attachment_mime_types or {},
                    filename_to_id
                )
                block_groups.extend(comment_blockgroups)
                block_group_index += len(comment_blockgroups)

        # Step 5: Assign attachments to description children
        description_children = self._assign_description_children(
            attachment_children_map,
            comment_attachment_ids,
            description_image_ids
        )
        if description_children:
            description_block_group.children_records = description_children

        # Step 6: Build parent-child hierarchy
        self._populate_blockgroup_hierarchy(block_groups)

        return BlocksContainer(blocks=[], block_groups=block_groups)

    async def _process_issue_attachments_for_children(
        self,
        attachments_data: List[Dict[str, Any]],
        issue_id: str,
        issue_node_id: str,
        project_id: str,
        issue_weburl: Optional[str],
        tx_store,
    ) -> Dict[str, ChildRecord]:
        """
        Process issue attachments and create ChildRecords for TableRowMetadata.
        Creates FileRecords if they don't exist (for new attachments added after sync).

        ALL attachments are processed including images.
        Returns a MAP of attachment_id -> ChildRecord for proper mapping to description/comments.

        Args:
            attachments_data: List of attachment data from Jira API
            issue_id: Issue external ID
            issue_node_id: Internal record ID of issue
            project_id: Project ID for external_record_group_id
            issue_weburl: Issue web URL (used as weburl for FileRecords)
            tx_store: Transaction store for looking up existing records

        Returns:
            Dict mapping attachment_id -> ChildRecord for proper location assignment
        """
        attachment_children_map: Dict[str, ChildRecord] = {}
        new_file_records: List[Tuple[FileRecord, List[Permission]]] = []

        for attachment in attachments_data:
            try:
                attachment_id = attachment.get("id", "")
                if not attachment_id:
                    continue

                # Look up existing attachment record from database
                external_id = f"attachment_{attachment_id}"
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_id
                )

                # Create FileRecord if it doesn't exist (new attachment added after sync)
                if not existing_record:
                    filename = attachment.get("filename", "unknown")
                    file_size = attachment.get("size", 0)
                    mime_type = attachment.get("mimeType", MimeTypes.UNKNOWN.value)
                    created_str = attachment.get("created")
                    created_at = self._parse_jira_timestamp(created_str) if created_str else 0

                    # Create FileRecord using helper method
                    file_record = self._create_attachment_file_record(
                        attachment_id=str(attachment_id),
                        filename=filename,
                        mime_type=mime_type,
                        file_size=file_size,
                        created_at=created_at,
                        parent_issue_id=issue_id,
                        parent_node_id=issue_node_id,
                        project_id=project_id,
                        weburl=issue_weburl,
                    )

                    new_file_records.append((file_record, []))
                    existing_record = file_record

                if existing_record:
                    attachment_children_map[str(attachment_id)] = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=existing_record.id,
                        child_name=existing_record.record_name
                    )

            except Exception as e:
                attachment_id = attachment.get("id", "unknown")
                self.logger.error(f"❌ Error processing issue attachment {attachment_id} for children_records: {e}", exc_info=True)
                continue

        # Save any new FileRecords
        if new_file_records:
            await self.data_entities_processor.on_new_records(new_file_records)
            self.logger.info(f"📎 Created {len(new_file_records)} new FileRecords for attachments added after sync")

        return attachment_children_map

    async def _process_issue_blockgroups_for_streaming(self, record: Record) -> bytes:
        """
        Process issue BlockGroups for streaming by creating BlocksContainer on-demand.

        This function:
        1. Fetches issue data from Jira API (including comments, attachments)
        2. Fetches related FileRecords from database (for ChildRecords)
        3. Creates new FileRecords if any new attachments/files added since sync
        4. Parses issue to BlocksContainer with Description and Thread BlockGroups
        5. Serializes BlocksContainer to JSON bytes for streaming
        """
        issue_id = record.external_record_id

        datasource = await self._get_fresh_datasource()

        # Fetch issue WITHOUT comments (will fetch separately)
        response = await datasource.get_issue(
            issueIdOrKey=issue_id,
            fields=["summary", "description", "attachment"]
        )

        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch issue content: {response.text()}")

        issue_data = response.json()
        if not issue_data:
            raise Exception(f"No issue data found for ID: {issue_id}")

        fields = issue_data.get("fields", {})

        # Get issue key from API response
        issue_key = issue_data.get("key", "")

        # Build issue weburl
        if self.site_url and issue_key:
            issue_weburl = f"{self.site_url}/browse/{issue_key}"
        else:
            issue_weburl = record.weburl

        # Get attachments from issue data
        attachments_data = fields.get("attachment", [])

        # Fetch comments using dedicated endpoint with pagination (for proper parentId support)
        comments_data = await self._fetch_issue_comments_with_pagination(issue_id)

        # Get project ID from record group
        project_id = record.external_record_group_id or ""

        # Build attachment_id -> mimeType map for determining image vs file
        # This is needed because Jira ADF media nodes always have type="file"
        attachment_mime_types: Dict[str, str] = {}
        for attachment in attachments_data:
            att_id = attachment.get("id", "")
            mime_type = attachment.get("mimeType", "")
            if att_id:
                attachment_mime_types[str(att_id)] = mime_type

        # Fetch child records from database - get map of attachment_id -> ChildRecord
        attachment_children_map: Dict[str, ChildRecord] = {}

        async with self.data_store_provider.transaction() as tx_store:
            # Process attachments (including images)
            if attachments_data:
                attachment_children_map = await self._process_issue_attachments_for_children(
                    attachments_data=attachments_data,
                    issue_id=issue_id,
                    issue_node_id=record.id,
                    project_id=project_id,
                    issue_weburl=issue_weburl,
                    tx_store=tx_store
                )

        # Add comments to issue_data for parsing
        issue_data["comments"] = comments_data

        # Parse issue to BlocksContainer
        # attachment_children_map maps attachment_id -> ChildRecord
        # attachment_mime_types maps attachment_id -> mimeType (for image detection)
        # This allows proper mapping: comment attachments -> comment block, others -> description
        blocks_container = await self._parse_issue_to_blocks(
            issue_data=issue_data,
            issue_key=issue_key,
            weburl=issue_weburl,
            attachment_children_map=attachment_children_map if attachment_children_map else None,
            attachment_mime_types=attachment_mime_types if attachment_mime_types else None,
        )

        # Serialize BlocksContainer to JSON bytes
        blocks_json = blocks_container.model_dump_json(indent=2)
        return blocks_json.encode('utf-8')

    # ============================================================================
    # Media & Streaming
    # ============================================================================

    def _create_media_fetcher(
        self,
        issue_id: str
    ) -> Callable[[str, str], Awaitable[Optional[str]]]:
        """
        Create a media fetcher callback bound to a specific issue.

        This factory method avoids closure issues when creating fetchers inside loops.
        Each call returns a new async function with the issue_id properly captured.

        Args:
            issue_id: The issue ID to bind to the fetcher

        Returns:
            Async function that takes (media_id, alt_text) and returns base64 data URI
        """
        # Capture issue_id in this scope
        captured_issue_id = issue_id

        async def fetcher(media_id: str, alt_text: str) -> Optional[str]:
            return await self._fetch_media_as_base64(captured_issue_id, media_id, alt_text)

        return fetcher

    async def _get_issue_attachments_cached(self, issue_id: str) -> List[Dict[str, Any]]:
        """
        Fetch issue attachments with per-issue caching to avoid repeated API calls.
        """
        if issue_id in self._issue_attachments_cache:
            return self._issue_attachments_cache[issue_id]

        datasource = await self._get_fresh_datasource()
        response = await datasource.get_issue(
            issueIdOrKey=issue_id,
            fields=["attachment"]
        )

        if response.status != HttpStatusCode.OK.value:
            self.logger.warning(f"⚠️ Failed to fetch issue {issue_id} for media: {response.status}")
            return []

        issue_details = self._safe_json_parse(response, f"issue attachments for {issue_id}")
        if not issue_details:
            return []

        attachments = issue_details.get("fields", {}).get("attachment", []) or []
        self._issue_attachments_cache[issue_id] = attachments
        return attachments

    async def _fetch_media_as_base64(
        self,
        issue_id: str,
        media_id: str,
        media_alt: str
    ) -> Optional[str]:
        """
        Fetch attachment content and return as base64 data URI.

        Jira inline media (images in description/comments) reference attachments
        on the issue. Per Jira API, media.attrs.id in ADF IS the attachment ID,
        so we first try direct lookup by media_id, then fall back to filename matching.
        """
        try:
            # Get issue attachments (cached per issue to avoid repeated calls)
            attachments = await self._get_issue_attachments_cached(issue_id)

            if not attachments:
                self.logger.debug(f"No attachments found for issue {issue_id}")
                return None

            # First, try to find attachment by media_id (most reliable - per Jira API docs)
            target_attachment = None
            if media_id:
                for attachment in attachments:
                    if str(attachment.get("id", "")) == str(media_id):
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by media_id: {media_id}")
                        break

            # Fallback: find attachment matching the filename (alt text)
            if not target_attachment and media_alt:
                for attachment in attachments:
                    filename = attachment.get("filename", "")
                    if filename == media_alt:
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by exact filename: {media_alt}")
                        break

            # Fallback: try partial filename match
            if not target_attachment and media_alt:
                for attachment in attachments:
                    filename = attachment.get("filename", "")
                    if media_alt in filename or filename in media_alt:
                        target_attachment = attachment
                        self.logger.debug(f"Found attachment by partial filename match: {media_alt} ~ {filename}")
                        break

            if not target_attachment:
                self.logger.debug(f"No attachment found matching media_id='{media_id}' or filename='{media_alt}' in issue {issue_id}")
                return None

            # Fetch attachment content
            attachment_id = target_attachment.get("id")
            mime_type = target_attachment.get("mimeType", "application/octet-stream")

            datasource = await self._get_fresh_datasource()
            content_response = await datasource.get_attachment_content(
                id=attachment_id,
                redirect=False
            )

            if content_response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch attachment content {attachment_id}: {content_response.status}")
                return None

            # Convert to base64
            content_bytes = content_response.bytes()
            base64_data = base64.b64encode(content_bytes).decode('utf-8')

            # Create data URI
            data_uri = f"data:{mime_type};base64,{base64_data}"

            self.logger.debug(f"Successfully converted attachment '{media_alt or media_id}' to base64 ({len(base64_data)} chars)")
            return data_uri

        except Exception as e:
            self.logger.warning(f"⚠️ Error fetching media (id='{media_id}', alt='{media_alt}') for issue {issue_id}: {e}")
            return None

    def _create_attachment_file_record(
        self,
        attachment_id: str,
        filename: str,
        mime_type: str,
        file_size: int,
        created_at: int,
        parent_issue_id: str,
        parent_node_id: Optional[str],
        project_id: str,
        weburl: Optional[str],
        record_id: Optional[str] = None,
        version: int = 0,
        external_id_prefix: str = "attachment_",
        skip_filter_check: bool = False,
    ) -> FileRecord:
        """
        Create a FileRecord for an attachment with consistent settings.

        This helper consolidates FileRecord creation logic to avoid duplication
        and ensure consistency across sync, streaming, and reindexing flows.

        Args:
            skip_filter_check: If True, skip filter checks (used during reindexing).
                              If False, apply indexing filter checks (default for sync/streaming).

        Returns:
            FileRecord with consistent field settings
        """
        # Extract extension from filename
        extension = None
        if '.' in filename:
            extension = filename.split('.')[-1].lower()

        # Build external_record_id
        external_record_id = f"{external_id_prefix}{attachment_id}"

        file_record = FileRecord(
            id=record_id or str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=external_record_id,
            external_revision_id=str(created_at) if created_at else None,
            parent_external_record_id=parent_issue_id,
            parent_record_type=RecordType.TICKET,
            connector_name=Connectors.JIRA,
            connector_id=self.connector_id,
            origin=OriginTypes.CONNECTOR,
            version=version,
            mime_type=mime_type,
            extension=extension,
            size_in_bytes=file_size,
            record_group_type=RecordGroupType.PROJECT,
            external_record_group_id=project_id,
            created_at=created_at or get_epoch_timestamp_in_ms(),
            updated_at=created_at or get_epoch_timestamp_in_ms(),
            source_created_at=created_at,
            source_updated_at=created_at,
            weburl=weburl,
            inherit_permissions=True,
            is_file=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
        )

        # Set indexing status based on filters (if loaded and not skipping filter check)
        # Skip filter check during reindexing to allow reindexing regardless of filter settings
        if not skip_filter_check and self.indexing_filters and not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
            file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

        return file_record

    # ============================================================================
    # Utility Methods
    # ============================================================================

    def _parse_jira_timestamp(self, timestamp_str: Optional[str]) -> int:
        """
        Parse Jira timestamp to epoch milliseconds.

        Supports multiple Jira timestamp formats:
        - With milliseconds: 2024-01-15T10:30:45.123+0000
        - Without milliseconds: 2024-01-15T10:30:45+0000
        - ISO format with colon in timezone: 2024-01-15T10:30:45.123+00:00
        - Z suffix: 2024-01-15T10:30:45.123Z
        """
        if not timestamp_str:
            return 0

        # Normalize to ISO format that fromisoformat() can handle
        # Replace Z with +00:00, and +0000/-0000 with +00:00/-00:00
        normalized = timestamp_str.replace('Z', '+00:00')
        # Convert +0000 or -0000 format to +00:00 or -00:00 (fromisoformat requires colon)
        normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', normalized)

        try:
            dt = datetime.fromisoformat(normalized)
            return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            # Fallback to strptime for edge cases (requires +0000 format, not +00:00)
            normalized_strptime = re.sub(r'([+-])(\d{2}):(\d{2})$', r'\1\2\3', normalized)
            for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]:
                try:
                    dt = datetime.strptime(normalized_strptime, fmt)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    continue

        self.logger.warning(f"⚠️ Failed to parse timestamp '{timestamp_str}'")
        return 0

    def _safe_json_parse(self, response, context: str = "API response") -> Optional[Dict[str, Any]]:
        """
        Safely parse JSON response with error handling.

        Args:
            response: HTTP response object with .json() method
            context: Description of what we're parsing for error messages

        Returns:
            Parsed JSON as dict, or None if parsing fails
        """
        try:
            return response.json()
        except Exception as e:
            self.logger.error(f"❌ Failed to parse JSON from {context}: {e}")
            return None

    async def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    # ============================================================================
    # Public API Methods
    # ============================================================================

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific record"""
        return ""

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Jira using DataSource"""
        try:
            if not self.data_source:
                await self.init()

            # Test by fetching user info (simple API call)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_current_user()
            return response.status == HttpStatusCode.OK.value
        except Exception as e:
            self.logger.error(f"❌ Connection test failed: {e}")
            return False

    async def run_incremental_sync(self) -> None:
        """Run incremental sync - calls run_sync which handles incremental logic"""
        await self.run_sync()

    async def cleanup(self) -> None:
        """Cleanup resources - close HTTP client connections properly"""
        try:
            self.logger.info("Cleaning up Jira connector resources")

            # Close HTTP client properly BEFORE event loop closes
            # This prevents Windows asyncio "Event loop is closed" errors
            if self.external_client:
                try:
                    internal_client = self.external_client.get_client()
                    if internal_client and hasattr(internal_client, 'close'):
                        await internal_client.close()
                        self.logger.debug("Closed Jira HTTP client connection")
                except Exception as e:
                    # Swallow errors during shutdown - client may already be closed
                    self.logger.debug(f"Error closing Jira client (may be expected during shutdown): {e}")
                finally:
                    self.external_client = None

            # Clear data source reference
            self.data_source = None
            # Clear attachments cache
            self._issue_attachments_cache.clear()

            self.logger.info("Jira connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, comment, or attachment).
        """
        try:
            if not self.cloud_id:
                await self.init()

            if record.record_type == RecordType.TICKET:
                # Stream BlocksContainer as JSON
                content_bytes = await self._process_issue_blockgroups_for_streaming(record)

                return StreamingResponse(
                    iter([content_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.json"'
                    }
                )

            elif record.record_type == RecordType.FILE:
                # Stream attachment content
                attachment_id = record.external_record_id.replace("attachment_", "")

                # Get attachment content using DataSource
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_attachment_content(
                    id=attachment_id,
                    redirect=False
                )

                if response.status != HttpStatusCode.OK.value:
                    raise Exception(f"Failed to fetch attachment content: {response.text()}")

                # Stream the attachment content
                async def generate_attachment() -> AsyncGenerator[bytes, None]:
                    content_bytes = response.bytes()
                    yield content_bytes

                # Determine filename from record name
                filename = record.record_name if hasattr(record, 'record_name') else f"attachment_{attachment_id}"

                # Replace non-ASCII characters to avoid latin-1 encoding errors
                safe_filename = sanitize_filename_for_content_disposition(
                    filename,
                    fallback=f"attachment_{attachment_id}"
                )
                encoded_filename = quote(filename)

                # Jira requires UTF-8 encoded filename in addition to the safe filename
                additional_headers = {
                    "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                }

                return create_stream_record_response(
                    generate_attachment(),
                    filename=filename,
                    mime_type=record.mime_type if hasattr(record, 'mime_type') else MimeTypes.UNKNOWN.value,
                    fallback_filename=f"attachment_{attachment_id}",
                    additional_headers=additional_headers
                )

            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

        except Exception as e:
            self.logger.error(f"Error streaming record {record.external_record_id} ({record.record_type}): {e}")
            raise

    # ============================================================================
    # Reindexing
    # ============================================================================

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex a list of Jira records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor
        4. Skips reindex for records that are not properly typed (base Record class)"""
        try:
            if not record_results:
                return

            self.logger.info(f"Starting reindex for {len(record_results)} Jira records")

            # Ensure external clients are initialized
            if not self.data_source:
                raise Exception("DataSource not initialized. Call init() first.")

            # Check records at source for updates
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non updated records
            if non_updated_records:
                reindexable_records = []
                skipped_count = 0

                for record in non_updated_records:
                    # Only reindex properly typed records (TicketRecord, FileRecord)
                    # Check if it's a subclass of Record but not the base Record class itself
                    record_class_name = type(record).__name__
                    if record_class_name != 'Record':
                        reindexable_records.append(record)
                    else:
                        self.logger.warning(
                            f"Record {record.id} ({record.record_type}) is base Record class "
                            f"(not properly typed), skipping reindex"
                        )
                        skipped_count += 1

                if reindexable_records:
                    try:
                        await self.data_entities_processor.reindex_existing_records(reindexable_records)
                        self.logger.info(f"Published reindex events for {len(reindexable_records)} records")
                    except NotImplementedError as e:
                        self.logger.warning(
                            f"Cannot reindex records - to_kafka_record not implemented: {e}"
                        )

                if skipped_count > 0:
                    self.logger.warning(f"Skipped reindex for {skipped_count} records that are not properly typed")

        except Exception as e:
            self.logger.error(f"Error during Jira reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Note: Comments are no longer separate records - they are processed as Blocks
        within the issue's BlocksContainer during streaming.
        """
        try:
            if record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_issue(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch issue from source for reindexing."""
        try:
            # Load indexing filters if not already loaded (needed for reindexing context)
            if self.indexing_filters is None:
                _, self.indexing_filters = await load_connector_filters(
                    self.config_service,
                    "jira",
                    self.connector_id,
                    self.logger
                )

            issue_id = record.external_record_id

            # Fetch issue from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_issue(
                issueIdOrKey=issue_id,
                expand=[]
            )

            if response.status == HttpStatusCode.GONE.value or response.status == HttpStatusCode.BAD_REQUEST.value:
                self.logger.warning(f"Issue {issue_id} not found at source, may have been deleted")
                return None

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"Failed to fetch issue {issue_id}: HTTP {response.status}")
                return None

            issue = response.json()
            fields = issue.get("fields", {})

            # Check if updated timestamp changed
            current_updated_at = self._parse_jira_timestamp(fields.get("updated")) if fields.get("updated") else 0

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_updated_at:
                self.logger.debug(f"Issue {issue_id} has not changed at source")
                return None

            self.logger.info(f"Issue {issue_id} has changed at source (timestamp: {record.source_updated_at if hasattr(record, 'source_updated_at') else 'N/A'} -> {current_updated_at})")

            # Build user lookup from emailAddress if available (for _extract_issue_data)
            user_by_account_id = {}
            for user_field in ["creator", "reporter", "assignee"]:
                user_obj = fields.get(user_field, {})
                account_id = user_obj.get("accountId")
                email = user_obj.get("emailAddress")
                if account_id and email:
                    user_by_account_id[account_id] = AppUser(
                        id="",
                        connector_id=self.connector_id,
                        email=email,
                        source_user_id=account_id
                    )

            # Extract issue data using existing function
            issue_data = self._extract_issue_data(issue, user_by_account_id)

            # Get project info
            project = fields.get("project", {})
            project_id = project.get("id", "")
            project_key = project.get("key", "")

            # Fetch security level permissions if the issue has a security field
            security_level_id = issue_data.get("security_level_id")
            permissions = []
            if security_level_id and project_key:
                project_security_scheme_id = await self._get_project_security_scheme_id(project_key)
                if project_security_scheme_id:
                    permissions = await self._fetch_security_level_permissions(
                        project_key=project_key,
                        security_level_id=security_level_id,
                        project_security_scheme_id=project_security_scheme_id,
                        assignee_email=issue_data.get("assignee_email"),
                        reporter_email=issue_data.get("reporter_email"),
                        assignee_account_id=issue_data.get("assignee_account_id"),
                        reporter_account_id=issue_data.get("reporter_account_id")
                    )

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Create updated TicketRecord preserving record ID and existing relationships
            issue_record = TicketRecord(
                id=record.id,
                org_id=self.data_entities_processor.org_id,
                priority=issue_data["priority"],
                status=issue_data["status"],
                type=issue_data.get("issue_type"),
                creator_email=issue_data["creator_email"],
                creator_name=issue_data["creator_name"],
                reporter_email=issue_data["reporter_email"],
                reporter_name=issue_data["reporter_name"],
                assignee=issue_data["assignee_name"],
                assignee_email=issue_data["assignee_email"],
                external_record_id=issue_id,
                external_revision_id=str(current_updated_at) if current_updated_at else None,
                record_name=issue_data["issue_name"],
                record_type=RecordType.TICKET,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.JIRA,
                connector_id=self.connector_id,
                record_group_type=record.record_group_type if hasattr(record, 'record_group_type') else RecordGroupType.PROJECT,
                external_record_group_id=record.external_record_group_id if hasattr(record, 'external_record_group_id') else project_id,
                parent_external_record_id=record.parent_external_record_id if hasattr(record, 'parent_external_record_id') else issue_data.get("parent_external_id"),
                parent_record_type=record.parent_record_type if hasattr(record, 'parent_record_type') else (RecordType.TICKET if issue_data.get("parent_external_id") else None),
                version=version,
                mime_type=MimeTypes.BLOCKS.value,  # Use BLOCKS for blockgroups/blocks streaming
                weburl=record.weburl if hasattr(record, 'weburl') else None,
                source_created_at=issue_data["created_at"],
                source_updated_at=current_updated_at,
                created_at=issue_data["created_at"],
                updated_at=current_updated_at,
                preview_renderable=False,
                is_dependent_node=False,  # Tickets are not dependent
                parent_node_id=None,  # Tickets have no parent node
            )

            return (issue_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching issue {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
            # Load indexing filters if not already loaded (needed for reindexing context)
            if self.indexing_filters is None:
                _, self.indexing_filters = await load_connector_filters(
                    self.config_service,
                    "jira",
                    self.connector_id,
                    self.logger
                )

            # Extract attachment ID (remove "attachment_" prefix)
            external_id = record.external_record_id
            if external_id.startswith("attachment_"):
                attachment_id = external_id.replace("attachment_", "")
            else:
                attachment_id = external_id

            # Get parent issue ID (external)
            issue_id = record.parent_external_record_id if hasattr(record, 'parent_external_record_id') else None
            if not issue_id:
                self.logger.warning(f"Attachment {attachment_id} missing parent issue ID")
                return None

            # Get parent ticket's internal record ID
            parent_ticket_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=issue_id
            )
            parent_node_id = parent_ticket_record.id if parent_ticket_record else None

            # Fetch issue to get attachment metadata
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_issue(
                issueIdOrKey=issue_id,
                expand=[]
            )

            if response.status == HttpStatusCode.GONE.value or response.status == HttpStatusCode.BAD_REQUEST.value:
                self.logger.warning(f"Parent issue {issue_id} not found at source")
                return None

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"Failed to fetch parent issue {issue_id}: HTTP {response.status}")
                return None

            issue_data = response.json()
            # Get issue key from the response (it's at the top level, not in fields)
            issue_key = issue_data.get("key")  # Fallback to None if key not found
            fields = issue_data.get("fields", {})
            attachments = fields.get("attachment", [])
            project_key = fields.get("project", {}).get("key", "")

            # Find the specific attachment
            attachment_data = None
            for att in attachments:
                if str(att.get("id")) == str(attachment_id):
                    attachment_data = att
                    break

            if not attachment_data:
                self.logger.warning(f"Attachment {attachment_id} not found in issue {issue_id}, may have been deleted")
                return None

            # Check if created timestamp changed (attachments don't have updated field)
            current_created = attachment_data.get("created")
            current_created_at = self._parse_jira_timestamp(current_created) if current_created else 0

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_created_at:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source")
                return None

            self.logger.info(f"🔄 Attachment {attachment_id} has changed at source")

            # Get attachment metadata
            filename = attachment_data.get("filename", "unknown")
            file_size = attachment_data.get("size", 0)
            mime_type = attachment_data.get("mimeType", MimeTypes.UNKNOWN.value)

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Construct web URL for attachment - use issue browse URL since attachments are visible there
            weburl = None
            if self.site_url and issue_key:
                weburl = f"{self.site_url}/browse/{issue_key}"

            # Get project ID from existing record
            project_id = record.external_record_group_id if hasattr(record, 'external_record_group_id') else ""

            # Create updated FileRecord using helper method (preserving record ID)
            # Skip filter check during reindexing to allow reindexing regardless of filter settings
            attachment_record = self._create_attachment_file_record(
                attachment_id=attachment_id,
                filename=filename,
                mime_type=mime_type,
                file_size=file_size,
                created_at=current_created_at,
                parent_issue_id=issue_id,
                parent_node_id=parent_node_id,
                project_id=project_id,
                weburl=weburl,
                record_id=record.id,
                version=version,
                skip_filter_check=True,
            )

            # Attachments inherit the same security level permissions as their parent issue
            permissions = []
            security_obj = fields.get("security")
            security_level_id = security_obj.get("id") if security_obj else None
            if security_level_id and project_key:
                project_security_scheme_id = await self._get_project_security_scheme_id(project_key)
                if project_security_scheme_id:
                    assignee = fields.get("assignee") or {}
                    reporter = fields.get("reporter") or {}
                    assignee_email = assignee.get("emailAddress")
                    reporter_email = reporter.get("emailAddress")
                    assignee_account_id = assignee.get("accountId")
                    reporter_account_id = reporter.get("accountId")
                    permissions = await self._fetch_security_level_permissions(
                        project_key=project_key,
                        security_level_id=security_level_id,
                        project_security_scheme_id=project_security_scheme_id,
                        assignee_email=assignee_email,
                        reporter_email=reporter_email,
                        assignee_account_id=assignee_account_id,
                        reporter_account_id=reporter_account_id
                    )

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "BaseConnector":
        """Factory method to create JiraConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )
        await data_entities_processor.initialize()

        return JiraConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
