"""Jira Cloud Connector Implementation"""
import base64
import re
from datetime import datetime, timezone
from logging import Logger
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    NoReturn,
    Optional,
    Set,
    Tuple,
)
from uuid import uuid4

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncDataPointType, SyncPoint
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
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import JiraApp
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_JIRA_CONFIG_PATH,
    AtlassianScope,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.jira.jira import JiraClient
from app.sources.external.jira.jira import JiraDataSource
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# API URLs
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

# Pagination constants
DEFAULT_MAX_RESULTS: int = 100
BATCH_PROCESSING_SIZE: int = 200

# JQL query constants
ISSUE_SEARCH_FIELDS: List[str] = [
    "summary", "description", "status", "priority",
    "creator", "reporter", "assignee", "created", "updated",
    "issuetype", "project", "parent", "attachment"
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
            items: List[str] = []
            for child in content:
                if child.get("type") == "taskItem":
                    item_text = extract_text(child, list_depth + 1).strip()
                    if item_text:
                        items.append(item_text)
            if items:
                text = "\n".join(items) + "\n\n"

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
            items: List[str] = []
            for child in content:
                if child.get("type") == "decisionItem":
                    item_text = extract_text(child, list_depth + 1).strip()
                    if item_text:
                        items.append(item_text)
            if items:
                text = "\n".join(items) + "\n\n"

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
    .with_auth_type("OAUTH")\
    .with_description("Sync issues from Jira Cloud")\
    .with_categories(["Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/jira.svg")
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
        .with_redirect_uri("connectors/oauth/callback/Jira", True)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Atlassian Cloud Application ID",
            description="The Application (Client) ID from Atlassian Developer Console"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Atlassian Cloud Client Secret",
            description="The Client Secret from Atlassian Developer Console",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="domain",
            display_name="Atlassian Domain",
            description="https://your-domain.atlassian.net"
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, AtlassianScope.get_full_access())
        .add_filter_field(FilterField(
            name="project_keys",
            display_name="Project Keys",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            description="Filter issues by project keys (e.g., PROJ1, PROJ2)"
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
            name="issue_comments",
            display_name="Index Issue Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of issue comments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="issue_attachments",
            display_name="Index Issue Attachments",
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
        self._sync_in_progress: bool = False
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

    async def init(self) -> None:
        """
        Initialize Jira client using proper Client + DataSource architecture
        """
        try:
            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "jira",
                self.logger
            )

            self.logger.info(f"🔍 Sync filters: {self.sync_filters}")
            self.logger.info(f"🔍 Indexing filters: {self.indexing_filters}")

            # Use JiraClient.build_from_services() to create client with proper auth
            client = await JiraClient.build_from_services(
                self.logger,
                self.config_service
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
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Jira client: {e}")
            raise

    async def _get_access_token(self) -> str:
        """
        Get access token from config
        """
        config = await self.config_service.get_config(f"{OAUTH_JIRA_CONFIG_PATH}")
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
        config = await self.config_service.get_config("/services/connectors/jira/config")

        if not config:
            raise Exception("Jira configuration not found")

        # Extract fresh OAuth access token
        credentials_config = config.get("credentials", {}) or {}
        fresh_token = credentials_config.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client
        internal_client = self.external_client.get_client()
        # Extract token from Authorization header (format: "Bearer {token}")
        auth_header = internal_client.headers.get("Authorization", "")
        auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Jira connector does not support dynamic filter options."""
        raise NotImplementedError("Jira connector does not support dynamic filter options")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        pass

        # # Update client's token if it changed (mutation)
        # if current_token != fresh_token:
        #     self.logger.debug("🔄 Updating client with refreshed access token")
        #     internal_client.headers["Authorization"] = f"Bearer {fresh_token}"

        # # Return datasource with updated client
        # return JiraDataSource(self.external_client)

    def _parse_jira_timestamp(self, timestamp_str: Optional[str]) -> int:
        """
        Parse Jira timestamp to epoch milliseconds
        """
        if not timestamp_str:
            return 0
        try:
            dt = datetime.strptime(
                timestamp_str.replace("+0000", "+00:00"),
                "%Y-%m-%dT%H:%M:%S.%f%z"
            )
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to parse timestamp '{timestamp_str}': {e}")
            return 0

    async def run_sync(self) -> None:
        """
        Run sync of Jira projects and issues - only new/updated tickets
        """
        # Check if sync is already in progress
        if self._sync_in_progress:
            self.logger.warning("⚠️ Sync already in progress, skipping this run")
            return

        self._sync_in_progress = True

        try:
            if not self.cloud_id:
                await self.init()

            users = await self.data_entities_processor.get_all_active_users()

            if not users:
                self.logger.info("ℹ️ No users found")
                return

            # Fetch and sync users
            jira_users = await self._fetch_users()
            if jira_users:
                await self.data_entities_processor.on_new_app_users(jira_users)
                self.logger.info(f"👥 Synced {len(jira_users)} Jira users")

            # Fetch and sync user groups (returns mapping for role resolution)
            groups_members_map = await self._sync_user_groups(jira_users)

            # Get project_keys filter if configured (to fetch only those projects)
            allowed_keys = None
            if self.sync_filters:
                project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
                if project_keys_filter:
                    allowed_keys = project_keys_filter.get_value(default=[])

            # Fetch projects
            projects, raw_projects = await self._fetch_projects(allowed_keys)

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
        finally:
            self._sync_in_progress = False

    async def _get_issues_sync_checkpoint(self) -> Optional[int]:
        """
        Get global sync checkpoint and check for filter changes.
        """
        try:
            sync_point_data = await self.issues_sync_point.read_sync_point("issues_global")
            last_sync_time = sync_point_data.get("last_sync_time") if sync_point_data else None

            if self._have_filters_changed(sync_point_data):
                self.logger.info("🔄 Filters changed, performing full sync")
                return None
            return last_sync_time
        except Exception:
            return None

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

        # Read project sync point
        project_sync_data = await self._get_project_sync_checkpoint(project_key)

        # Check if filters changed - if so, do full sync for this project
        if self._have_filters_changed(project_sync_data):
            self.logger.info(f"🔄 Filters changed for project {project_key}, performing full sync")
            project_last_sync_time = None
            resume_from_timestamp = None
        else:
            # Use last_issue_updated if available (works for both resume and incremental sync)
            # Fall back to project sync time, then global sync time
            resume_from_timestamp = project_sync_data.get("last_issue_updated")
            if not resume_from_timestamp:
                resume_from_timestamp = project_sync_data.get("last_sync_time") or global_last_sync_time

            # Set project_last_sync_time for fallback in _fetch_issues_batched
            project_last_sync_time = project_sync_data.get("last_sync_time") or global_last_sync_time

            if resume_from_timestamp:
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
            resume_from_timestamp
        ):
            batch_number += 1
            batch_size = len(issues_batch)

            if not issues_batch:
                continue

            self.logger.info(f"📦 Processing batch {batch_number} for project {project_key}: {batch_size} records")

            # Process this batch
            await self._process_new_records(issues_batch, project_key, stats)
            total_issues_processed += batch_size

            # Track last issue updated timestamp for resume capability
            if last_issue_timestamp:
                last_issue_updated_in_batch = last_issue_timestamp

            # Update project sync point after each batch
            current_time = get_epoch_timestamp_in_ms()
            # Always save last_issue_updated (works for both resume and next incremental sync)
            # last_sync_time is just metadata for tracking when checkpoint was updated
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

    async def _process_new_records(
        self,
        records_with_permissions: List[Tuple[Record, List[Permission]]],
        project_name: str,
        stats: Dict[str, int]
    ) -> None:
        """
        Process records (new and updated) in batches.
        on_new_records internally handles both new and updated.
        """
        # Sort records: records without parent_external_record_id (Epics) come first
        sorted_records = sorted(
            records_with_permissions,
            key=lambda x: (x[0].parent_external_record_id is not None, x[0].parent_external_record_id or "")
        )

        batch_size = BATCH_PROCESSING_SIZE

        for i in range(0, len(sorted_records), batch_size):
            batch = sorted_records[i:i + batch_size]
            await self.data_entities_processor.on_new_records(batch)

            # Update stats
            new_in_batch = sum(1 for r, _ in batch if r.version == 0)
            stats["new_count"] += new_in_batch
            stats["updated_count"] += len(batch) - new_in_batch

            # Log batch summary
            issues_count = sum(1 for r, _ in batch if isinstance(r, TicketRecord))
            comments_count = sum(1 for r, _ in batch if isinstance(r, CommentRecord))
            files_count = sum(1 for r, _ in batch if isinstance(r, FileRecord))
            self.logger.info(
                f"📦 Batch {i//batch_size + 1}: {issues_count} issues, {comments_count} comments, "
                f"{files_count} attachments ({new_in_batch} new, {len(batch) - new_in_batch} updated)"
            )

    async def _update_issues_sync_checkpoint(self, stats: Dict[str, int], project_count: int) -> None:
        """
        Update global sync checkpoint with current filter values.
        """
        if stats["total_synced"] > 0 or project_count > 0:
            current_time = get_epoch_timestamp_in_ms()
            sync_point_data = {
                "last_sync_time": current_time,
                "filters": self._get_current_filter_values()
            }
            await self.issues_sync_point.update_sync_point("issues_global", sync_point_data)

    async def _get_project_sync_checkpoint(self, project_key: str) -> Dict[str, Any]:
        """
        Get project-specific sync checkpoint.

        Returns:
            Dict with last_sync_time, last_issue_updated, and filters
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
            "last_issue_updated": last_issue_updated if last_issue_updated is not None else existing.get("last_issue_updated"),
            "filters": self._get_current_filter_values()
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

    async def _fetch_users(self) -> List[AppUser]:
        """
        Fetch all active Jira users using DataSource
        """

        if not self.data_source:
            raise ValueError("DataSource not initialized")

        users: List[Dict[str, Any]] = []
        start_at = 0
        max_results_per_request = 50

        while True:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_all_users(
                query='',
                maxResults=max_results_per_request,
                startAt=start_at
            )

            if response.status != HttpStatusCode.OK.value:
                raise Exception(f"Failed to fetch users: {response.text()}")

            users_batch = response.json()

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
                source_user_id=account_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=user.get("displayName", email),
                is_active=user.get("active", True)
            )
            app_users.append(app_user)

        self.logger.info(f"👥 Fetched {len(app_users)} active users with emails")
        return app_users

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
        limit = 1000

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
        Handle deletion of an issue and its related entities (comments, attachments).
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
                self.logger.info(f"✅ Found issue {issue_key} with internal ID {record_internal_id}, external ID {issue_id}")

                # 1. Delete child Sub-tasks
                subtask_count = await self._delete_issue_children(
                    issue_id,
                    RecordType.TICKET,
                    tx_store
                )

                # 2. Delete child comments
                comment_count = await self._delete_issue_children(
                    issue_id,
                    RecordType.COMMENT,
                    tx_store
                )

                # 3. Delete child attachments
                attachment_count = await self._delete_issue_children(
                    issue_id,
                    RecordType.FILE,
                    tx_store
                )

                # 4. Delete the issue itself and all its edges
                await tx_store.arango_service.delete_records_and_relations(
                    node_key=record_internal_id,
                    hard_delete=True,
                    transaction=tx_store.txn
                )

                self.logger.info(
                    f"🗑️ Deleted issue {issue_key} and its children "
                    f"({subtask_count} sub-tasks, {comment_count} comments, {attachment_count} attachments)"
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
        Delete all child records (sub-tasks, comments, or attachments) for a deleted issue.
        """
        try:
            deleted_count = 0
            # Map child type to readable name
            child_type_name = {
                RecordType.TICKET: "sub-task",
                RecordType.COMMENT: "comment",
                RecordType.FILE: "attachment"
            }.get(child_type, str(child_type))

            # Direct query by parent_external_record_id and record_type - efficient
            child_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=parent_issue_id,
                record_type=child_type.value
            )

            for record in child_records:
                # If deleting a sub-task (TICKET), recursively delete its children first
                if child_type == RecordType.TICKET:
                    subtask_id = record.external_record_id
                    # Delete sub-task's comments
                    await self._delete_issue_children(
                        subtask_id,
                        RecordType.COMMENT,
                        tx_store
                    )
                    # Delete sub-task's attachments
                    await self._delete_issue_children(
                        subtask_id,
                        RecordType.FILE,
                        tx_store
                    )

                # Delete record and all its edges (indexer cleanup handled automatically)
                await tx_store.arango_service.delete_records_and_relations(
                    node_key=record.id,
                    hard_delete=True,
                    transaction=tx_store.txn
                )
                deleted_count += 1
                self.logger.debug(f"  Deleted {child_type_name} {record.external_record_id}")

            return deleted_count

        except Exception as e:
            self.logger.error(f"❌ Error deleting {child_type_name}s for issue {parent_issue_id}: {e}")
            return 0

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
        max_results = 50

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
        max_results = 50

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

                        # Build AppRole with external_id matching Permission format
                        app_role = AppRole(
                            app_name=Connectors.JIRA,
                            source_role_id=f"{project_key}_{role_id}",
                            name=f"{project_key} - {role_name}",
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
                app_role = AppRole(
                    app_name=Connectors.JIRA,
                    source_role_id=f"{project_key}_projectLead",
                    name=f"{project_key} - Project Lead"
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

    async def _fetch_projects(self, project_keys: Optional[List[str]] = None) -> Tuple[List[Tuple[RecordGroup, List[Permission]]], List[Dict[str, Any]]]:
        """
        Fetch projects using DataSource. Returns (record_groups, raw_projects).
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        projects: List[Dict[str, Any]] = []

        # If specific project keys are provided, fetch only those projects
        if project_keys is not None and project_keys:
            datasource = await self._get_fresh_datasource()
            for project_key in project_keys:
                try:
                    response = await datasource.get_project(
                        projectIdOrKey=project_key,
                        expand="description,url,permissions,issueTypes,lead"
                    )

                    if response.status == HttpStatusCode.OK.value:
                        project = response.json()
                        projects.append(project)
                        self.logger.debug(f"Successfully fetched project: {project_key}")
                    elif response.status == HttpStatusCode.NOT_FOUND.value:
                        self.logger.warning(f"⚠️ Project {project_key} not found, skipping")
                    else:
                        self.logger.warning(f"⚠️ Failed to fetch project {project_key}: HTTP {response.status}")
                except Exception as e:
                    self.logger.error(f"❌ Error fetching project {project_key}: {e}")
                    continue
        else:
            # project_keys is None or empty - fetch all projects
            self.logger.info("📁 Fetching all projects")
            start_at = 0

            while True:
                # Use DataSource instead of manual HTTP call
                datasource = await self._get_fresh_datasource()
                response = await datasource.search_projects(
                    maxResults=DEFAULT_MAX_RESULTS,
                    startAt=start_at,
                    expand=["description", "url", "permissions", "issueTypes", "lead"]
                )

                if response.status != HttpStatusCode.OK.value:
                    raise Exception(f"Failed to fetch projects: {response.text()}")

                projects_batch = response.json()
                batch_projects = projects_batch.get("values", [])

                if not batch_projects:
                    break

                projects.extend(batch_projects)

                # Move to next page
                start_at += len(batch_projects)

                # Check if we've reached the end
                total = projects_batch.get("total", 0)
                is_last = projects_batch.get("isLast", False)

                if is_last or (total > 0 and start_at >= total):
                    break

        # Fetch application roles → groups mapping once (cached)
        app_roles_mapping = await self._fetch_application_roles_to_groups_mapping()

        record_groups: List[Tuple[RecordGroup, List[Permission]]] = []
        for project in projects:
            project_id = project.get("id")
            project_name = project.get("name")
            project_key = project.get("key")

            description = project.get("description")
            if description and isinstance(description, dict):
                description = adf_to_text(description)
            elif not description:
                description = None

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

            # This determines which groups/users can access the project
            project_permissions = await self._fetch_project_permission_scheme(project_key, app_roles_mapping)

            record_groups.append((record_group, project_permissions))

            if project_permissions:
                self.logger.info(f"🔐 Project {project_key}: {len(project_permissions)} permission grants from scheme")

        return record_groups, projects

    async def _fetch_issues_batched(
        self,
        project_key: str,
        project_id: str,
        users: List[AppUser],
        last_sync_time: Optional[int] = None,
        resume_from_timestamp: Optional[int] = None
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

        # Build JQL query
        jql_conditions = [f'project = "{project_key}"']

        # Get modified filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
        modified_after = None
        modified_before = None

        if modified_filter:
            modified_after, modified_before = modified_filter.get_value(default=(None, None))

        # Get created filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
        created_after = None
        created_before = None

        if created_filter:
            created_after, created_before = created_filter.get_value(default=(None, None))

        # Determine modified_after from filter and/or checkpoint
        # resume_from_timestamp can be from last_issue_updated (resume) or last_sync_time (incremental)
        if resume_from_timestamp:
            # Use checkpoint timestamp (works for both resume and incremental sync)
            modified_after = resume_from_timestamp
            self.logger.info(f"🔄 Starting from timestamp: {resume_from_timestamp}")
        elif modified_after:
            if last_sync_time:
                modified_after = max(modified_after, last_sync_time)
        elif last_sync_time:
            modified_after = last_sync_time

        if modified_after:
            modified_dt = datetime.fromtimestamp(modified_after / 1000, tz=timezone.utc)
            # Always use > to avoid reprocessing the last issue (works for both resume and incremental)
            jql_conditions.append(f'updated > "{modified_dt.strftime("%Y-%m-%d %H:%M")}"')

        if modified_before:
            modified_dt = datetime.fromtimestamp(modified_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'updated <= "{modified_dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_after:
            created_dt = datetime.fromtimestamp(created_after / 1000, tz=timezone.utc)
            jql_conditions.append(f'created >= "{created_dt.strftime("%Y-%m-%d %H:%M")}"')

        if created_before:
            created_dt = datetime.fromtimestamp(created_before / 1000, tz=timezone.utc)
            jql_conditions.append(f'created <= "{created_dt.strftime("%Y-%m-%d %H:%M")}"')

        # Build final JQL (ORDER BY required for consistent pagination)
        # Add id ASC as secondary sort for stable ordering when timestamps are equal
        jql = " AND ".join(jql_conditions) + " ORDER BY updated ASC, id ASC"
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

                issues_batch_response = response.json()

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
                records_batch = await self._build_issue_records(batch_issues, project_id, users, tx_store)

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

    def _extract_issue_data(
        self,
        issue: Dict[str, Any],
        user_by_account_id: Dict[str, AppUser]
    ) -> Dict[str, Any]:
        """
        Extract and process issue data from raw Jira issue dictionary.

        Returns a dictionary with all extracted fields:
        - issue_id, issue_key, issue_name, description
        - issue_type, hierarchy_level, is_epic, is_subtask
        - parent_external_id, parent_key
        - status, priority
        - creator_email, creator_name, reporter_email, reporter_name
        - assignee_email, assignee_name
        - created_at, updated_at
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
        issue_type = issue_type_obj.get("name") if issue_type_obj else None
        hierarchy_level = issue_type_obj.get("hierarchyLevel") if issue_type_obj else None

        # Extract parent issue information
        parent_obj = fields.get("parent")
        parent_external_id = parent_obj.get("id") if parent_obj else None
        parent_key = parent_obj.get("key") if parent_obj else None

        # Categorize issue type based on hierarchy level
        is_epic = hierarchy_level == 1
        is_subtask = hierarchy_level == -1

        # Build record name with issue type for better searchability

        issue_name = issue_summary

        # Add issue type to description for full searchability
        if issue_type and description_text:
            description = f"Issue Type: {issue_type}\n\n{description_text}"
        elif issue_type:
            description = f"Issue Type: {issue_type}"
        else:
            description = description_text

        status_obj = fields.get("status", {})
        status = status_obj.get("name") if status_obj else None

        priority_obj = fields.get("priority", {})
        priority = priority_obj.get("name") if priority_obj else None

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
            "assignee_email": assignee_email,
            "assignee_name": assignee_name,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def _build_issue_records(
        self,
        issues: List[Dict[str, Any]],
        project_id: str,
        users: List[AppUser],
        tx_store
    ) -> List[Tuple[Record, List[Permission]]]:
        """
        Build issue records with permissions from raw issue data, respecting Jira hierarchy
        """
        all_records: List[Tuple[Record, List[Permission]]] = []

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
            issue_data["description"]
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
            assignee_email = issue_data["assignee_email"]
            assignee_name = issue_data["assignee_name"]
            created_at = issue_data["created_at"]
            updated_at = issue_data["updated_at"]

            # Permissions: creator, reporter, and assignee
            permissions = self._build_permissions(reporter_email, assignee_email, creator_email)

            # Get fields for attachments (needed by _fetch_issue_attachments)
            fields = issue.get("fields", {})

            # Handle attachment deletions based on changelog for this issue
            await self._handle_attachment_deletions_from_changelog(issue, tx_store)

            # Detect if there were any comment changes for this issue (add/edit/delete)
            has_comment_changes = self._issue_has_comment_changes(issue)

            # Check for existing record (works for both Epics and regular issues)
            existing_record = await tx_store.get_record_by_external_id(
                connector_id=self.connector_id,
                external_id=issue_id
            )

            record_id = existing_record.id if existing_record else str(uuid4())
            is_new = existing_record is None

            if is_new:
                self.logger.debug(f"🆕 New issue found: {issue_key} (external_id: {issue_id})")
            else:
                self.logger.debug(f"🔄 Existing issue found: {issue_key} (internal_id: {existing_record.id}, external_id: {issue_id})")

            # Only increment version if issue content actually changed
            is_issue_changed = False
            if is_new:
                version = 0
                is_issue_changed = True
            elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                version = existing_record.version + 1
                is_issue_changed = True
                self.logger.debug(f"📝 Issue {issue_key} content changed, incrementing version to {version}")
            else:
                version = existing_record.version if existing_record else 0
                if existing_record:
                    self.logger.debug(f"✅ Issue {issue_key} unchanged, keeping version {version}")

            # Skip processing if issue is unchanged and has no comment changes
            if not is_issue_changed and not has_comment_changes:
                self.logger.debug(f"⏭️ Skipping unchanged issue {issue_key} (no issue or comment changes)")
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
                mime_type=MimeTypes.MARKDOWN.value,
                weburl=f"{atlassian_domain}/browse/{issue_key}" if atlassian_domain else None,
                source_created_at=created_at,
                source_updated_at=updated_at,
                created_at=created_at,
                updated_at=updated_at,
                inherit_permissions=True,
                preview_renderable=False,
                is_dependent_node=False,  # Tickets are not dependent
                parent_node_id=None,  # Tickets have no parent node
            )

            # Set indexing status based on filters
            if not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUES):
                issue_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

            all_records.append((issue_record, permissions))

            # Fetch comments and get media map for attachment-comment linking
            comment_media_map: Dict[str, str] = {}
            try:
                comment_records, comment_media_map = await self._fetch_issue_comments(
                    issue_id,
                    issue_key,
                    permissions,
                    external_record_group_id,
                    record_group_type,
                    user_by_account_id,
                    tx_store,
                    has_comment_changes,
                    parent_node_id=issue_record.id,
                )
                if comment_records:
                    all_records.extend(comment_records)
                    self.logger.debug(f"Added {len(comment_records)} comments for issue {issue_key}")
            except Exception as e:
                self.logger.error(f"❌ Failed to fetch comments for issue {issue_key}: {e}")

            try:
                attachment_records = await self._fetch_issue_attachments(
                    issue_id,
                    issue_key,
                    fields,
                    permissions,
                    external_record_group_id,
                    record_group_type,
                    tx_store,
                    comment_media_map,
                    parent_node_id=issue_record.id,
                )
                if attachment_records:
                    all_records.extend(attachment_records)
            except Exception as e:
                self.logger.error(f"❌ Failed to fetch attachments for issue {issue_key}: {e}")

        return all_records

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
        await tx_store.arango_service.delete_records_and_relations(
            node_key=record.id,
            hard_delete=True,
            transaction=tx_store.txn,
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

    def _issue_has_comment_changes(self, issue: Dict[str, Any]) -> bool:
        """
        Check changelog to see if there were any comment changes (add/edit/delete).
        """
        changelog = issue.get("changelog")
        if not changelog:
            return False

        histories = changelog.get("histories", [])
        if not histories:
            return False

        for history in histories:
            items = history.get("items", [])
            for item in items:
                field = item.get("field")
                field_id = item.get("fieldId")
                if field in ("Comment", "comment") or field_id == "comment":
                    return True

        return False

    async def _fetch_issue_comments(
        self,
        issue_id: str,
        issue_key: str,
        parent_permissions: List[Permission],
        parent_record_group_id: str,
        parent_record_group_type: RecordGroupType,
        user_by_account_id: Dict[str, AppUser],
        tx_store,
        has_comment_changes: bool = False,
        parent_node_id: Optional[str] = None,
    ) -> Tuple[List[Tuple[CommentRecord, List[Permission]]], Dict[str, str]]:
        """
        Fetch comments for an issue.

        Returns:
            Tuple of:
                - List of (CommentRecord, permissions) tuples
                - Dict mapping {filename_lower: comment_external_record_id} for media in comments
        """
        comment_records: List[Tuple[CommentRecord, List[Permission]]] = []
        comment_media_map: Dict[str, str] = {}  # {filename_lower: comment_external_record_id}

        try:
            if not self.data_source:
                raise ValueError("DataSource not initialized")

            start_at = 0
            all_comments = []

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_comments(
                    issueIdOrKey=issue_id,
                    maxResults=DEFAULT_MAX_RESULTS,
                    startAt=start_at
                )

                if response.status != HttpStatusCode.OK.value:
                    raise Exception(f"Failed to fetch comments: {response.text()}")

                comment_data = response.json()
                comments = comment_data.get("comments", [])

                if not comments:
                    break

                all_comments.extend(comments)

                # Move to next page
                start_at += len(comments)

                # Check if there are more comments
                total = comment_data.get("total", 0)
                if total > 0 and start_at >= total:
                    break

                # Also break if we got less than requested (last page)
                if len(comments) < DEFAULT_MAX_RESULTS:
                    break

            if not all_comments:
                # If we know comments changed and now there are none, delete all DB comments for this issue
                if has_comment_changes:
                    await self._delete_missing_comments_for_issue(issue_id, set(), tx_store)
                return [], {}

            self.logger.info(f"💬 Processing {len(all_comments)} comments for issue {issue_key}")

            # If comments changed (add/edit/delete), delete any DB comments that no longer exist at source
            if has_comment_changes:
                current_comment_ids: Set[str] = {
                    str(comment.get("id")) for comment in all_comments if comment.get("id") is not None
                }
                await self._delete_missing_comments_for_issue(issue_id, current_comment_ids, tx_store)

            # Process each comment
            for comment in all_comments:
                comment_id = comment.get("id")

                # Check for existing comment record
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=f"comment_{comment_id}"
                )

                # Parse timestamps first (needed for version check)
                created_at = self._parse_jira_timestamp(comment.get("created"))
                updated_at = self._parse_jira_timestamp(comment.get("updated"))

                record_id = existing_record.id if existing_record else str(uuid4())
                is_new = existing_record is None

                # Check if source_updated_at (comment's updated timestamp) has changed
                if is_new:
                    version = 0
                elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != updated_at:
                    version = existing_record.version + 1  # Comment content changed
                else:
                    version = existing_record.version if existing_record else 0  # Comment unchanged

                # Extract author info using accountId lookup
                author = comment.get("author", {})
                author_account_id = author.get("accountId")
                author_name = author.get("displayName", "Unknown")
                author_email = None
                if author_account_id and author_account_id in user_by_account_id:
                    author_email = user_by_account_id[author_account_id].email

                # Check if this comment is a reply to another comment (threaded comments)
                parent_comment_id = comment.get("parent", {}).get("id") if comment.get("parent") else None

                # Determine parent: if it's a reply to a comment, parent is the comment; otherwise parent is the issue
                if parent_comment_id:
                    # This is a reply to another comment
                    parent_external_id = f"comment_{parent_comment_id}"
                    parent_record_type = RecordType.COMMENT
                    comment_name = f"Reply by {author_name} on {issue_key}"
                else:
                    # This is a top-level comment (direct reply to issue)
                    parent_external_id = issue_id
                    parent_record_type = RecordType.TICKET
                    comment_name = f"Comment by {author_name} on {issue_key}"

                # Create CommentRecord
                comment_record = CommentRecord(
                    id=record_id,
                    org_id=self.data_entities_processor.org_id,
                    record_name=comment_name,
                    record_type=RecordType.COMMENT,
                    external_record_id=f"comment_{comment_id}",
                    external_revision_id=str(updated_at) if updated_at else None,
                    parent_external_record_id=parent_external_id,
                    parent_record_type=parent_record_type,
                    external_record_group_id=parent_record_group_id,
                    connector_name=Connectors.JIRA,
                    connector_id=self.connector_id,
                    origin=OriginTypes.CONNECTOR,
                    version=version,
                    mime_type=MimeTypes.MARKDOWN.value,
                    record_group_type=parent_record_group_type,  # Inherit from parent issue
                    created_at=created_at,
                    updated_at=updated_at,
                    source_created_at=created_at,
                    source_updated_at=updated_at,
                    author_source_id=author_account_id or "unknown",
                    preview_renderable=False,
                    is_dependent_node=True,  # Comments are dependent nodes
                    parent_node_id=parent_node_id,  # Internal record ID of parent ticket
                )

                # Set indexing status based on filters
                if not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_COMMENTS):
                    comment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Comments inherit permissions from parent issue
                comment_permissions = parent_permissions.copy()

                # Add author to permissions if not already there
                if author_email:
                    author_has_permission = any(
                        p.email == author_email for p in comment_permissions
                    )
                    if not author_has_permission:
                        comment_permissions.append(Permission(
                            entity_type=EntityType.USER,
                            email=author_email,
                            type=PermissionType.READ,
                        ))

                comment_records.append((comment_record, comment_permissions))

                # Extract media references from comment body for attachment-comment linking
                comment_body = comment.get("body")
                if comment_body and isinstance(comment_body, dict):
                    media_nodes = extract_media_from_adf(comment_body)
                    for media in media_nodes:
                        # Use 'filename' which tries __fileName first, then alt
                        filename = media.get("filename", "").strip()
                        if filename:
                            # Map filename (lowercased) to this comment's external_record_id
                            comment_media_map[filename.lower()] = f"comment_{comment_id}"

            self.logger.info(f"💬 Returning {len(comment_records)} comment records for issue {issue_key} (media map: {len(comment_media_map)} entries)")
            return comment_records, comment_media_map

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch comments for issue {issue_key}: {e}", exc_info=True)
            return [], {}

    async def _delete_missing_comments_for_issue(
        self,
        issue_id: str,
        current_comment_ids: Set[str],
        tx_store,
    ) -> None:
        """
        Delete CommentRecord entries for this issue that no longer exist in Jira.
        """
        try:
            # Direct query for comments by parent issue - efficient
            existing_records = await tx_store.get_records_by_parent(
                connector_id=self.connector_id,
                parent_external_record_id=issue_id,
                record_type=RecordType.COMMENT.value
            )

            deleted_count = 0

            for record in existing_records:

                external_id = record.external_record_id
                # external_record_id format: "comment_<id>"
                if external_id.startswith("comment_"):
                    comment_id = external_id.replace("comment_", "")
                else:
                    comment_id = external_id

                if current_comment_ids and comment_id in current_comment_ids:
                    continue

                # Comment no longer exists at source -> delete record and its relations
                await tx_store.arango_service.delete_records_and_relations(
                    node_key=record.id,
                    hard_delete=True,
                    transaction=tx_store.txn,
                )
                deleted_count += 1
                self.logger.info(
                    f"🗑️ Deleted comment {external_id} for issue {issue_id} "
                    "because it no longer exists in Jira"
                )

            if deleted_count > 0:
                self.logger.info(
                    f"🗑️ Deleted {deleted_count} comments for issue {issue_id} that were removed from Jira"
                )

        except Exception as e:
            self.logger.error(
                f"❌ Error deleting missing comments for issue {issue_id}: {e}",
                exc_info=True,
            )

    async def _fetch_issue_attachments(
        self,
        issue_id: str,
        issue_key: str,
        issue_fields: Dict[str, Any],
        parent_permissions: List[Permission],
        parent_record_group_id: str,
        parent_record_group_type: RecordGroupType,
        tx_store,
        comment_media_map: Optional[Dict[str, str]] = None,
        parent_node_id: Optional[str] = None,
    ) -> List[Tuple[FileRecord, List[Permission]]]:
        """
        Fetch attachments for an issue from issue fields.

        Args:
            comment_media_map: Optional dict mapping {filename_lower: comment_external_record_id}
                              for linking attachments to their parent comments.
        """
        if comment_media_map is None:
            comment_media_map = {}
        attachment_records: List[Tuple[FileRecord, List[Permission]]] = []

        try:
            # Get attachments from issue fields (already fetched in ISSUE_SEARCH_FIELDS)
            attachments = issue_fields.get("attachment", [])

            if not attachments:
                return []

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

                # Extract extension from filename
                extension = None
                if '.' in filename:
                    extension = filename.split('.')[-1].lower()

                # Determine version (increment if file was updated)
                record_id = existing_record.id if existing_record else str(uuid4())
                is_new = existing_record is None

                if is_new:
                    version = 0
                elif hasattr(existing_record, 'source_updated_at') and existing_record.source_updated_at != created_at:
                    version = existing_record.version + 1
                else:
                    version = existing_record.version if existing_record else 0

                # Construct web URL for attachment
                weburl = None
                if self.site_url:
                    weburl = f"{self.site_url}/rest/api/3/attachment/content/{attachment_id}"

                # Determine parent: check if attachment belongs to a comment
                filename_lower = filename.lower()
                if filename_lower in comment_media_map:
                    # Attachment is referenced in a comment - parent is the comment
                    parent_external_record_id = comment_media_map[filename_lower]
                    parent_record_type = RecordType.COMMENT
                    self.logger.debug(f"📎 Attachment '{filename}' linked to comment {parent_external_record_id}")
                else:
                    # Attachment belongs to the issue directly
                    parent_external_record_id = issue_id
                    parent_record_type = RecordType.TICKET

                # Create FileRecord
                attachment_record = FileRecord(
                    id=record_id,
                    org_id=self.data_entities_processor.org_id,
                    record_name=filename,
                    record_type=RecordType.FILE,
                    external_record_id=f"attachment_{attachment_id}",
                    external_revision_id=str(created_at) if created_at else None,
                    parent_external_record_id=parent_external_record_id,
                    parent_record_type=parent_record_type,
                    external_record_group_id=parent_record_group_id,
                    connector_name=Connectors.JIRA,
                    connector_id=self.connector_id,
                    origin=OriginTypes.CONNECTOR,
                    version=version,
                    mime_type=mime_type,
                    record_group_type=parent_record_group_type,
                    created_at=created_at,
                    updated_at=created_at,
                    source_created_at=created_at,
                    source_updated_at=created_at,
                    is_file=True,
                    size_in_bytes=file_size,
                    extension=extension,
                    is_dependent_node=True,  # Attachments are dependent nodes
                    parent_node_id=parent_node_id,  # Internal record ID of parent ticket
                    weburl=weburl,
                )

                # Set indexing status based on filters
                if not self.indexing_filters.is_enabled(IndexingFilterKey.ISSUE_ATTACHMENTS):
                    attachment_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                # Attachments inherit permissions from parent (issue or comment)
                attachment_permissions = parent_permissions.copy()

                attachment_records.append((attachment_record, attachment_permissions))

            # Log summary of matches
            matched_count = sum(1 for r, _ in attachment_records if r.parent_record_type == RecordType.COMMENT)
            if matched_count > 0:
                self.logger.info(
                    f"📎 Returning {len(attachment_records)} attachment records for issue {issue_key} "
                    f"({matched_count} linked to comments, {len(attachment_records) - matched_count} to issue)"
                )
            else:
                self.logger.info(f"📎 Returning {len(attachment_records)} attachment records for issue {issue_key}")
            return attachment_records

        except Exception as e:
            self.logger.error(f"Failed to fetch attachments for issue {issue_key}: {e}", exc_info=True)
            return []

    def _get_current_filter_values(self) -> Dict[str, Any]:
        """
        Get current filter values for storage in sync point
        """
        filter_values = {}

        if self.sync_filters:
            # Store modified date filter
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            if modified_filter:
                modified_after, modified_before = modified_filter.get_value(default=(None, None))
                filter_values["modified_after"] = modified_after
                filter_values["modified_before"] = modified_before

            # Store created date filter
            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            if created_filter:
                created_after, created_before = created_filter.get_value(default=(None, None))
                filter_values["created_after"] = created_after
                filter_values["created_before"] = created_before

            # Store project keys filter
            project_keys_filter = self.sync_filters.get(SyncFilterKey.PROJECT_KEYS)
            if project_keys_filter:
                project_keys = project_keys_filter.get_value(default=[])
                filter_values["project_keys"] = project_keys

        return filter_values

    def _have_filters_changed(self, sync_point_data: Optional[Dict[str, Any]]) -> bool:
        """
        Check if current filters differ from stored filters in sync point
        """
        if not sync_point_data:
            return False

        stored_filters = sync_point_data.get("filters", {})
        current_filters = self._get_current_filter_values()

        if stored_filters != current_filters:
            self.logger.info(f"🔄 Filter change detected: {stored_filters} -> {current_filters}")
            return True

        return False

    def _build_permissions(
        self,
        reporter_email: Optional[str],
        assignee_email: Optional[str],
        creator_email: Optional[str] = None
    ) -> List[Permission]:
        """
        Build simple permissions list for an issue.
        Creator, reporter, and assignee get OWNER permission.
        """
        permissions: List[Permission] = []
        processed_emails: set = set()

        # Add creator permission (immutable - always has access to issue they created)
        if creator_email and creator_email not in processed_emails:
            permissions.append(Permission(
                entity_type=EntityType.USER,
                email=creator_email,
                type=PermissionType.OWNER,
            ))
            processed_emails.add(creator_email)

        # Add reporter permission (reporter can be changed, so permissions update on change)
        if reporter_email and reporter_email not in processed_emails:
            permissions.append(Permission(
                entity_type=EntityType.USER,
                email=reporter_email,
                type=PermissionType.OWNER,
            ))
            processed_emails.add(reporter_email)

        # Add assignee permission (if different from creator/reporter)
        if assignee_email and assignee_email not in processed_emails:
            permissions.append(Permission(
                entity_type=EntityType.USER,
                email=assignee_email,
                type=PermissionType.OWNER,
            ))
            processed_emails.add(assignee_email)

        return permissions

    async def _fetch_media_as_base64(
        self,
        issue_id: str,
        media_id: str,
        media_alt: str
    ) -> Optional[str]:
        """
        Fetch attachment content by filename and return as base64 data URI.

        Jira inline media (images in description/comments) reference attachments
        on the issue. We find the attachment by filename (media alt text) and
        fetch its content.

        Args:
            issue_id: The issue ID/key containing the attachment
            media_id: The media ID from ADF (not always useful for fetching)
            media_alt: The alt text, usually the filename

        Returns:
            Base64 data URI string like "data:image/png;base64,..." or None
        """
        try:
            datasource = await self._get_fresh_datasource()

            # Get issue to find attachments
            response = await datasource.get_issue(
                issueIdOrKey=issue_id,
                fields=["attachment"]
            )

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"⚠️ Failed to fetch issue {issue_id} for media: {response.status}")
                return None

            issue_details = response.json()
            attachments = issue_details.get("fields", {}).get("attachment", [])

            if not attachments:
                self.logger.debug(f"No attachments found for issue {issue_id}")
                return None

            # Find attachment matching the filename (alt text)
            target_attachment = None
            for attachment in attachments:
                filename = attachment.get("filename", "")
                if filename == media_alt:
                    target_attachment = attachment
                    break

            if not target_attachment:
                # Try partial match if exact match fails
                for attachment in attachments:
                    filename = attachment.get("filename", "")
                    if media_alt in filename or filename in media_alt:
                        target_attachment = attachment
                        break

            if not target_attachment:
                self.logger.debug(f"No attachment found matching '{media_alt}' in issue {issue_id}")
                return None

            # Fetch attachment content
            attachment_id = target_attachment.get("id")
            mime_type = target_attachment.get("mimeType", "application/octet-stream")

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

            self.logger.debug(f"Successfully converted attachment '{media_alt}' to base64 ({len(base64_data)} chars)")
            return data_uri

        except Exception as e:
            self.logger.warning(f"⚠️ Error fetching media '{media_alt}' for issue {issue_id}: {e}")
            return None

    async def _fetch_issue_content(self, issue_id: str) -> str:
        """Fetch full issue content for streaming using DataSource.

        Fetches images from attachments and embeds them as base64 data URIs
        for multimodal indexing.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get issue details
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_issue(
            issueIdOrKey=issue_id,
            expand=["renderedFields"]
        )

        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch issue content: {response.text()}")

        issue_details = response.json()
        fields = issue_details.get("fields", {})
        description = fields.get("description", "")
        summary = fields.get("summary", "")

        summary_text = f"Title: {summary}" if summary else ""

        # Create media fetcher callback for this issue
        async def media_fetcher(media_id: str, alt_text: str) -> Optional[str]:
            return await self._fetch_media_as_base64(issue_id, media_id, alt_text)

        # Convert description with embedded images
        if description:
            description_md = await adf_to_text_with_images(description, media_fetcher)
            description_text = f"Description:\n{description_md}"
        else:
            description_text = ""

        combined_text = f"# {summary_text}\n\n{description_text}"

        return combined_text

    async def _fetch_comment_content(self, comment_id: str, issue_id: str) -> str:
        """Fetch comment content for streaming using DataSource.

        Fetches images from attachments and embeds them as base64 data URIs
        for multimodal indexing.
        """
        if not self.data_source:
            raise ValueError("DataSource not initialized")

        # Use DataSource to get comment details
        datasource = await self._get_fresh_datasource()
        response = await datasource.get_comment(
            issueIdOrKey=issue_id,
            id=comment_id
        )

        if response.status != HttpStatusCode.OK.value:
            raise Exception(f"Failed to fetch comment content: {response.text()}")

        comment_details = response.json()

        # Extract comment body (ADF format)
        body_adf = comment_details.get("body")

        # Create media fetcher callback for the parent issue
        async def media_fetcher(media_id: str, alt_text: str) -> Optional[str]:
            return await self._fetch_media_as_base64(issue_id, media_id, alt_text)

        # Convert comment body with embedded images
        if body_adf:
            comment_text = await adf_to_text_with_images(body_adf, media_fetcher)
        else:
            comment_text = ""

        # Extract author info
        author = comment_details.get("author", {})
        author_name = author.get("displayName", "Unknown")

        # Extract timestamps
        created = comment_details.get("created", "")
        updated = comment_details.get("updated", "")

        # Format comment content
        header = f"Comment by {author_name}"
        if created:
            header += f" on {created}"
        if updated and updated != created:
            header += f" (updated: {updated})"

        combined_text = f"# {header}\n\n{comment_text}"

        return combined_text

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

            self.logger.info("Jira connector cleanup completed")
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")

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
                    # Only reindex properly typed records (TicketRecord, CommentRecord, FileRecord)
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
        """
        try:
            if record.record_type == RecordType.TICKET:
                return await self._check_and_fetch_updated_issue(record)
            elif record.record_type == RecordType.COMMENT:
                return await self._check_and_fetch_updated_comment(record)
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
                        id="", email=email, source_user_id=account_id
                    )

            # Extract issue data using existing function
            issue_data = self._extract_issue_data(issue, user_by_account_id)

            # Get project info
            project = fields.get("project", {})
            project_id = project.get("id", "")

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
                mime_type=MimeTypes.MARKDOWN.value,
                weburl=record.weburl if hasattr(record, 'weburl') else None,
                source_created_at=issue_data["created_at"],
                source_updated_at=current_updated_at,
                created_at=issue_data["created_at"],
                updated_at=current_updated_at,
                preview_renderable=False,
                is_dependent_node=False,  # Tickets are not dependent
                parent_node_id=None,  # Tickets have no parent node
            )

            # Build permissions (creator, reporter, and assignee)
            permissions = self._build_permissions(
                issue_data["reporter_email"],
                issue_data["assignee_email"],
                issue_data["creator_email"]
            )

            return (issue_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching issue {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_comment(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch comment from source for reindexing."""
        try:
            # Extract comment ID (remove "comment_" prefix)
            external_id = record.external_record_id
            if external_id.startswith("comment_"):
                comment_id = external_id.replace("comment_", "")
            else:
                comment_id = external_id

            # Get parent issue ID (external)
            issue_id = record.parent_external_record_id if hasattr(record, 'parent_external_record_id') else None
            if not issue_id:
                self.logger.warning(f"Comment {comment_id} missing parent issue ID")
                return None

            # Get parent ticket's internal record ID
            parent_ticket_record = await self.data_store_provider.arango_service.get_record_by_external_id(
                connector_name=Connectors.JIRA,
                external_id=issue_id
            )
            parent_node_id = parent_ticket_record.id if parent_ticket_record else None

            # Fetch comment from source
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_comment(
                issueIdOrKey=issue_id,
                id=comment_id
            )

            if response.status == HttpStatusCode.GONE.value or response.status == HttpStatusCode.BAD_REQUEST.value:
                self.logger.warning(f"Comment {comment_id} not found at source, may have been deleted")
                return None

            if response.status != HttpStatusCode.OK.value:
                self.logger.warning(f"Failed to fetch comment {comment_id}: HTTP {response.status}")
                return None

            comment_data = response.json()

            # Check if updated timestamp changed
            current_updated = comment_data.get("updated")
            current_updated_at = self._parse_jira_timestamp(current_updated) if current_updated else 0

            # Compare with stored timestamp
            if hasattr(record, 'source_updated_at') and record.source_updated_at == current_updated_at:
                self.logger.debug(f"Comment {comment_id} has not changed at source")
                return None

            self.logger.info(f"🔄 Comment {comment_id} has changed at source")

            # Extract author info
            author = comment_data.get("author", {})
            author_email = author.get("emailAddress")
            author_name = author.get("displayName", "Unknown")
            author_account_id = author.get("accountId")

            # Parse timestamps
            created = comment_data.get("created")
            created_at = self._parse_jira_timestamp(created) if created else 0

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Create updated CommentRecord preserving record ID
            comment_record = CommentRecord(
                id=record.id,
                org_id=self.data_entities_processor.org_id,
                record_name=record.record_name if hasattr(record, 'record_name') else f"Comment by {author_name}",
                record_type=RecordType.COMMENT,
                external_record_id=external_id,
                external_revision_id=str(current_updated_at) if current_updated_at else None,
                parent_external_record_id=issue_id,
                parent_record_type=RecordType.TICKET,
                external_record_group_id=record.external_record_group_id if hasattr(record, 'external_record_group_id') else None,
                connector_name=Connectors.JIRA,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                version=version,
                mime_type=MimeTypes.MARKDOWN.value,
                record_group_type=record.record_group_type if hasattr(record, 'record_group_type') else RecordGroupType.PROJECT,
                created_at=created_at,
                updated_at=current_updated_at,
                source_created_at=created_at,
                source_updated_at=current_updated_at,
                author_source_id=author_account_id or "unknown",
                preview_renderable=False,
                is_dependent_node=True,  # Comments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent ticket
            )

            # Get parent issue to fetch permissions
            datasource_for_parent = await self._get_fresh_datasource()
            parent_response = await datasource_for_parent.get_issue(
                issueIdOrKey=issue_id,
                expand=[]
            )

            # Build parent issue permissions (creator, reporter, and assignee)
            parent_permissions = []
            if parent_response.status == HttpStatusCode.OK.value:
                parent_data = parent_response.json()
                parent_fields = parent_data.get("fields", {})
                parent_creator = parent_fields.get("creator", {})
                parent_reporter = parent_fields.get("reporter", {})
                parent_assignee = parent_fields.get("assignee", {})
                parent_creator_email = parent_creator.get("emailAddress")
                parent_reporter_email = parent_reporter.get("emailAddress")
                parent_assignee_email = parent_assignee.get("emailAddress")
                parent_permissions = self._build_permissions(parent_reporter_email, parent_assignee_email, parent_creator_email)

            # Comment inherits parent permissions
            comment_permissions = parent_permissions.copy()

            # Add comment author to permissions if not already there
            if author_email:
                author_has_permission = any(p.email == author_email for p in comment_permissions)
                if not author_has_permission:
                    comment_permissions.append(Permission(
                        entity_type=EntityType.USER,
                        email=author_email,
                        type=PermissionType.READ,
                    ))

            return (comment_record, comment_permissions)

        except Exception as e:
            self.logger.error(f"Error fetching comment {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
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
            parent_ticket_record = await self.data_store_provider.arango_service.get_record_by_external_id(
                connector_name=Connectors.JIRA,
                external_id=issue_id
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
            fields = issue_data.get("fields", {})
            attachments = fields.get("attachment", [])

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

            # Extract extension
            extension = None
            if '.' in filename:
                extension = filename.split('.')[-1].lower()

            # Increment version
            version = record.version + 1 if hasattr(record, 'version') else 1

            # Construct web URL for attachment
            weburl = None
            if self.site_url:
                weburl = f"{self.site_url}/rest/api/3/attachment/content/{attachment_id}"

            # Create updated FileRecord preserving record ID
            attachment_record = FileRecord(
                id=record.id,
                org_id=self.data_entities_processor.org_id,
                record_name=filename,
                record_type=RecordType.FILE,
                external_record_id=external_id,
                external_revision_id=str(current_created_at) if current_created_at else None,
                parent_external_record_id=issue_id,
                parent_record_type=RecordType.TICKET,
                external_record_group_id=record.external_record_group_id if hasattr(record, 'external_record_group_id') else None,
                connector_name=Connectors.JIRA,
                connector_id=self.connector_id,
                origin=OriginTypes.CONNECTOR,
                version=version,
                mime_type=mime_type,
                record_group_type=record.record_group_type if hasattr(record, 'record_group_type') else RecordGroupType.PROJECT,
                created_at=current_created_at,
                updated_at=current_created_at,
                source_created_at=current_created_at,
                source_updated_at=current_created_at,
                is_file=True,
                size_in_bytes=file_size,
                extension=extension,
                weburl=weburl,
                is_dependent_node=True,  # Attachments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent ticket
            )

            # Get parent issue to fetch permissions (creator, reporter, and assignee)
            fields_for_perms = issue_data.get("fields", {})
            creator_for_perms = fields_for_perms.get("creator", {})
            reporter_for_perms = fields_for_perms.get("reporter", {})
            assignee_for_perms = fields_for_perms.get("assignee", {})
            creator_email_for_perms = creator_for_perms.get("emailAddress")
            reporter_email_for_perms = reporter_for_perms.get("emailAddress")
            assignee_email_for_perms = assignee_for_perms.get("emailAddress")
            permissions = self._build_permissions(reporter_email_for_perms, assignee_email_for_perms, creator_email_for_perms)

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (issue, comment, or attachment).
        """
        try:
            if not self.cloud_id:
                await self.init()

            if record.record_type == RecordType.TICKET:
                # Stream issue content (markdown format from ADF conversion)
                issue_id = record.external_record_id
                content = await self._fetch_issue_content(issue_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    }
                )

            elif record.record_type == RecordType.COMMENT:
                # Stream comment content (markdown format from ADF conversion)
                comment_id = record.external_record_id.replace("comment_", "")

                # Get parent issue ID from parent_external_record_id
                issue_id = record.parent_external_record_id
                if not issue_id:
                    raise ValueError(f"Comment {comment_id} missing parent issue ID")

                # Fetch comment content using the new method
                content = await self._fetch_comment_content(comment_id, issue_id)

                return StreamingResponse(
                    iter([content.encode('utf-8')]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
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
                from urllib.parse import quote
                safe_filename = filename.encode('ascii', 'ignore').decode('ascii') or f"attachment_{attachment_id}"
                encoded_filename = quote(filename)

                return StreamingResponse(
                    generate_attachment(),
                    media_type=record.mime_type if hasattr(record, 'mime_type') else MimeTypes.UNKNOWN.value,
                    headers={
                        "Content-Disposition": f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                    }
                )

            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

        except Exception as e:
            self.logger.error(f"Error streaming record {record.external_record_id} ({record.record_type}): {e}")
            raise

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
