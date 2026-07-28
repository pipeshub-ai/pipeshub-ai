import asyncio
import html
import json
import logging
import re
from typing import Any, Optional

from bs4 import BeautifulSoup
from markdownify import markdownify
from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import (
    args_template,
    entity_summary,
    list_summary,
)
from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.models.entities import ArtifactType
from app.services.artifact_registry import (
    MAX_ARTIFACT_BYTES,
    Actor,
    ArtifactRegistryService,
)
from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.confluence.confluence import (
    ConfluenceDataSource,
    _escape_cql_literal,
)

logger = logging.getLogger(__name__)

# Whitelist regex for the ``order_by`` parameter on `search_content`. Compiled
# once at module load so a malformed pattern surfaces at import time, not at
# request time. Accepts every shape Confluence CQL ORDER BY documents:
#   * `<field>`                         (direction defaults to asc)
#   * `<field> (asc|desc)`              (case-insensitive)
#   * `<field> (asc|desc), <field> ...` (comma-separated, multi-key sort)
# Field names themselves are not validated here — Confluence rejects unknown
# fields with a clear 400, which is surfaced to the planner verbatim.
_ORDER_BY_PATTERN = re.compile(
    r"^\s*[A-Za-z_][A-Za-z0-9_]*(\s+(asc|desc))?"
    r"(\s*,\s*[A-Za-z_][A-Za-z0-9_]*(\s+(asc|desc))?)*\s*$",
    re.IGNORECASE,
)

# Pydantic schemas for Confluence tools
class CreatePageInput(BaseModel):
    """Schema for creating Confluence pages"""
    space_id: str = Field(description="Space ID or key (e.g. '~abc123', 'SD', '12345'). IMPORTANT: Resolve via confluence.get_spaces if not already known from Reference Data or conversation history.")
    page_title: str = Field(description="Page title")
    page_content: str = Field(description="Page content in storage format")

class GetPageContentInput(BaseModel):
    """Schema for getting page content"""
    page_id: str = Field(description="Page ID")
    body_format: str = Field(
        default="markdown",
        description=(
            "Content format. 'markdown' (default) converts the page to readable markdown — best "
            "for summarising, reading, and answering questions. Use 'storage' only when you intend "
            "to call update_page with the result, as update_page requires storage format."
        ),
    )
    include_comments: bool = Field(
        default=True,
        description="Include footer and inline comments in the response.",
    )
    include_attachments: bool = Field(
        default=True,
        description=(
            "Include attachment metadata (id, title, mimeType, fileSize) in the response. "
            "Use download_attachment to retrieve attachment content."
        ),
    )

class ListPageAttachmentsInput(BaseModel):
    """Schema for listing page attachments"""
    page_id: str = Field(description="Page ID")
    media_type: Optional[str] = Field(
        default=None,
        description="Filter by MIME type, e.g. 'application/pdf' or 'image/png'.",
    )
    limit: Optional[int] = Field(
        default=25,
        description="Max attachments to return (1-250). Default 25.",
    )


class GetAttachmentMetadataInput(BaseModel):
    """Schema for getting attachment metadata"""
    attachment_id: str = Field(description="Attachment ID (from list_page_attachments or get_page_content)")


class DownloadAttachmentInput(BaseModel):
    """Schema for downloading an attachment"""
    page_id: str = Field(description="Page ID that owns the attachment")
    attachment_id: str = Field(description="Attachment ID (from list_page_attachments)")


class GetPagesInSpaceInput(BaseModel):
    """Schema for getting pages in space"""
    space_id: str = Field(description="Space ID or key")
    sort_by: Optional[str] = Field(
        default=None,
        description=(
            "Optional v2-API sort. Allowed: 'id', 'title', 'created-date', "
            "'modified-date'. Prefix with '-' for descending. Use "
            "'-modified-date' for 'most recently updated first', "
            "'-created-date' for 'newest first', 'title' for A-Z."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        description="Max pages per page of results (Confluence v2 caps at 250).",
    )

class UpdatePageTitleInput(BaseModel):
    """Schema for updating page title"""
    page_id: str = Field(description="Page ID")
    new_title: str = Field(description="New title")

class SearchPagesInput(BaseModel):
    """Schema for searching pages.

    Optimised for the "find page named X" intent (fuzzy title match), with the
    same authorship / date / label / ordering filter slots as `search_content`
    so that combined queries like "page named X that I created last week" work
    in one call. When any filter slot is set, the title becomes the body-text
    query and full-text CQL is used (mirrors `search_content`).
    """
    title: str = Field(description="Page title fragment to search (fuzzy)")
    space_id: Optional[str] = Field(default=None, description="Space ID or key to limit search")

    # ---- Same authorship / date / label / ordering slots as search_content. ----
    contributor: Optional[str] = Field(
        default=None,
        description=(
            "Filter by anyone who EVER edited the page. Pass `currentUser()` "
            "(no quotes) for self, or `\"<accountId>\"` (with double quotes) "
            "for another user — call search_users first."
        ),
    )
    creator: Optional[str] = Field(
        default=None,
        description="Filter by original page author. Same value format as contributor.",
    )
    mention: Optional[str] = Field(
        default=None,
        description="Filter to pages that @-mention this user. Same value format as contributor.",
    )
    last_modifier: Optional[str] = Field(
        default=None,
        description=(
            "Filter by who made the most recent edit (latest version only). "
            "Same value format. Prefer `contributor` for 'pages I updated'."
        ),
    )
    last_modified_after: Optional[str] = Field(
        default=None,
        description=(
            "ISO date (`'2026-05-01'`) or CQL function (`'now(\"-7d\")'`, "
            "`'startOfMonth()'`). Maps to `lastmodified >= ...`."
        ),
    )
    last_modified_before: Optional[str] = Field(
        default=None,
        description="Same value format as last_modified_after. Maps to `lastmodified <= ...`.",
    )
    created_after: Optional[str] = Field(
        default=None,
        description="Same value format as last_modified_after. Maps to `created >= ...`.",
    )
    created_before: Optional[str] = Field(
        default=None,
        description="Same value format as created_after. Maps to `created <= ...`.",
    )
    labels: Optional[list[str]] = Field(
        default=None,
        description="List of label names. Maps to CQL `label in (...)`.",
    )
    order_by: Optional[str] = Field(
        default=None,
        description=(
            "CQL ORDER BY clause, e.g. `'lastmodified desc'`. Set when the user "
            "asks for explicit ordering. Direction defaults to asc when omitted."
        ),
    )

class GetSpaceInput(BaseModel):
    """Schema for getting space"""
    space_id: str = Field(description="Space ID")

class UpdatePageInput(BaseModel):
    """Schema for updating a Confluence page"""
    page_id: str = Field(description="Page ID")
    page_title: Optional[str] = Field(default=None, description="New page title (optional)")
    page_content: Optional[str] = Field(default=None, description="New page content in storage format (optional)")

class CommentOnPageInput(BaseModel):
    """Schema for commenting on a Confluence page"""
    page_id: str = Field(description="Page ID")
    comment_text: str = Field(description="Comment text/content")
    parent_comment_id: Optional[str] = Field(default=None, description="Parent comment ID if replying to a comment (optional)")

class GetChildPagesInput(BaseModel):
    """Schema for getting child pages"""
    page_id: str = Field(description="The parent page ID")
    sort_by: Optional[str] = Field(
        default=None,
        description=(
            "Optional v2-API sort. Allowed: 'id', 'title', 'created-date', "
            "'modified-date'. Prefix with '-' for descending. Use "
            "'-modified-date' for 'most recently updated first', "
            "'-created-date' for 'newest first', 'title' for A-Z."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        description="Max child pages per page of results (Confluence v2 caps at 250).",
    )

class GetPageVersionsInput(BaseModel):
    """Schema for getting page versions"""
    page_id: str = Field(description="The page ID")

class SearchContentInput(BaseModel):
    """Schema for full-text + structured Confluence content search.

    All fields are optional, but at least one of `query`, an authorship slot
    (`contributor` / `creator` / `mention` / `last_modifier`), a temporal slot
    (`*_after` / `*_before`), or `labels` must be set — otherwise the call is
    rejected. `space_id` and `content_types` are scoping modifiers and don't
    count on their own.
    """
    query: Optional[str] = Field(
        default=None,
        description=(
            "Free-text search across page/blogpost titles, body, comments, and "
            "labels. Mirrors the Confluence platform search bar. Leave None or "
            "empty for authorship-only / label-only / date-only queries."
        ),
    )
    space_id: Optional[str] = Field(
        default=None,
        description="Optional space key or numeric ID to restrict search to one space.",
    )
    content_types: Optional[list[str]] = Field(
        default=None,
        description="Content types to include: 'page', 'blogpost', or both. Defaults to both.",
    )
    limit: Optional[int] = Field(
        default=25,
        description="Max number of results (1-50). Default 25.",
    )

    # ---- Authorship slots --------------------------------------------------
    # Pass `currentUser()` (literal, no quotes) for the calling user, OR
    # `"<accountId>"` (with double quotes) for someone else. Resolve names to
    # accountIds by calling `confluence.search_users` first.
    contributor: Optional[str] = Field(
        default=None,
        description=(
            "Filter by anyone who EVER edited the page (any historical version — "
            "the right field for 'pages I updated / edited / contributed to'). "
            "Pass `currentUser()` (no quotes) for self, or `\"<accountId>\"` "
            "(with double quotes) for another user. To get an accountId for "
            "another user, call confluence.search_users first."
        ),
    )
    creator: Optional[str] = Field(
        default=None,
        description=(
            "Filter by the original page author. Same value format as contributor. "
            "Use for 'pages I created', 'pages authored by <name>'."
        ),
    )
    mention: Optional[str] = Field(
        default=None,
        description=(
            "Filter to pages that @-mention this user. Same value format as "
            "contributor. Use for 'pages mentioning me / tagging <name>'."
        ),
    )
    last_modifier: Optional[str] = Field(
        default=None,
        description=(
            "Filter by the user who made the most recent edit (latest version "
            "only — `contributor` is broader). Same value format. Rarely needed; "
            "prefer `contributor` for 'pages I updated'."
        ),
    )

    # ---- Temporal slots ----------------------------------------------------
    last_modified_after: Optional[str] = Field(
        default=None,
        description=(
            "Filter to pages modified on or after this point. Pass an ISO date "
            "(`'2026-05-01'`) or a CQL function call (`'now(\"-7d\")'`, "
            "`'startOfMonth()'`, `'startOfDay(\"-1d\")'`). Use for 'updated last "
            "week / since May / today / yesterday'."
        ),
    )
    last_modified_before: Optional[str] = Field(
        default=None,
        description="Same value format as last_modified_after. Maps to `lastmodified <= ...`.",
    )
    created_after: Optional[str] = Field(
        default=None,
        description=(
            "Filter to pages created on or after this point. Same value format as "
            "last_modified_after. Use for 'created last week / this quarter'."
        ),
    )
    created_before: Optional[str] = Field(
        default=None,
        description="Same value format as created_after. Maps to `created <= ...`.",
    )

    # ---- Labels and ordering ----------------------------------------------
    labels: Optional[list[str]] = Field(
        default=None,
        description=(
            "List of label names. Matches pages tagged with ANY of the given "
            "labels (CQL `label in (...)`). Example: `['onboarding', 'qa-ready']`."
        ),
    )
    order_by: Optional[str] = Field(
        default=None,
        description=(
            "CQL ORDER BY clause. Examples: `'lastmodified desc'`, `'created desc'`, "
            "`'title asc'`, `'lastmodified desc, title asc'`. Field name + optional "
            "asc/desc, comma-separated. Set when the user asks for specific "
            "ordering ('most recent', 'newest first', 'alphabetical'). Direction "
            "defaults to asc when omitted."
        ),
    )


class SearchUsersInput(BaseModel):
    """Schema for searching Confluence users by name or email.

    A single query string is matched against both display name (fuzzy / partial)
    AND the user index (which can carry email or username depending on the
    Atlassian site's privacy settings). The caller doesn't have to detect
    "is this an email?" — both clauses always run.
    """
    query: str = Field(
        description=(
            "User's display name (full or partial — `'John'`, `'John Doe'`) OR "
            "an email address (`'john@x.com'`). Both lookups run for every input. "
            "Cloud privacy settings may suppress email matches; if no users come "
            "back for an email, fall back to asking the user for a display name."
        ),
    )
    max_results: Optional[int] = Field(
        default=10,
        description="Max users to return (1-50). Default 10.",
    )


# ---------------------------------------------------------------------------
# Agent-activity summary labels — see `jira.py`'s equivalent block. Most
# success envelopes here are `{"message": ..., "data": ...}` (via
# `Confluence._handle_response`), but `search_content` and `search_users`
# build their own top-level `{"results": [...], ...}` body without a
# `data` wrapper — each `list_summary(...)` call below passes the right
# `path` for its own tool accordingly.
# ---------------------------------------------------------------------------


def _confluence_page_label(page: dict[str, Any]) -> str:
    return page.get("title") or page.get("id") or "?"


def _confluence_space_label(space: dict[str, Any]) -> str:
    key = space.get("key") or "?"
    name = space.get("name")
    return f"{key}: {name}" if name else key


def _confluence_user_label(user: dict[str, Any]) -> str:
    return user.get("displayName") or user.get("accountId") or "?"


# Register Confluence toolset
@ToolsetBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_description("Confluence integration for wiki pages, documentation, and knowledge management")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Confluence",
            authorize_url="https://auth.atlassian.com/authorize",
            token_url="https://auth.atlassian.com/oauth/token",
            redirect_uri="toolsets/oauth/callback/confluence",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=AtlassianScope.get_confluence_read_access() + [
                    # Write scopes for creating/updating content
                    AtlassianScope.CONFLUENCE_CONTENT_CREATE.value,  # For create_page
                    AtlassianScope.CONFLUENCE_PAGE_WRITE.value,      # For update_page_title
                    AtlassianScope.CONFLUENCE_COMMENT_WRITE.value,      # For comment_on_page
                    AtlassianScope.CONFLUENCE_COMMENT_DELETE.value,      # For delete_comment
                ]
            ),
            fields=[
                CommonFields.client_id("Atlassian Developer Console"),
                CommonFields.client_secret("Atlassian Developer Console"),
            ],
            icon_path=IconPaths.connector_icon("confluence"),
            app_group="Documentation",
            app_description="Confluence OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://yourcompany.atlassian.net",
                description="The base URL of your Atlassian instance",
                field_type="URL",
                required=True,
                usage="CONFIGURE",
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="email",
                display_name="Email",
                placeholder="your-email@company.com",
                description="Your Atlassian account email",
                field_type="TEXT",
                required=True,
                usage="AUTHENTICATE",
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="API Token",
                placeholder="your-api-token",
                description="API token from Atlassian account settings",
                field_type="PASSWORD",
                required=True,
                usage="AUTHENTICATE",
                max_length=2000,
                is_secret=True,
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon("confluence"))
        .add_documentation_link(DocumentationLink(
            "Confluence Cloud OAuth Setup",
            "https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/confluence/confluence",
            "pipeshub",
        )))\
    .build_decorator()
class Confluence:
    """Confluence tool exposed to the agents using ConfluenceDataSource"""

    def __init__(self, client: ConfluenceClient, *, state: Any = None) -> None:
        """Initialize the Confluence tool

        Args:
            client: Confluence client object
            state: Agent runtime state (ChatState). Required for attachment download.
        """
        self.client = ConfluenceDataSource(client)
        self._site_url = None  # Cache for site URL
        self.chat_state = state

    def _registry(self) -> Optional[ArtifactRegistryService]:
        """Return the artifact registry, or None when dependencies are unavailable."""
        if not self.chat_state:
            return None
        graph_provider = self.chat_state.get("graph_provider")
        blob_store = self.chat_state.get("blob_store")
        if graph_provider is None or blob_store is None:
            return None
        return ArtifactRegistryService(graph_provider, blob_store)

    def _actor(self) -> Actor:
        state = self.chat_state or {}
        return Actor(org_id=state.get("org_id", ""), user_id=state.get("user_id", ""))

    def _handle_response(
        self,
        response: HTTPResponse,
        success_message: str
    ) -> tuple[bool, str]:
        """Handle HTTP response and return standardized tuple.

        Args:
            response: HTTP response object
            success_message: Message to return on success

        Returns:
            Tuple of (success_flag, json_string)
        """
        if response.status in [HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value, HttpStatusCode.NO_CONTENT.value]:
            try:
                data = response.json() if response.status != HttpStatusCode.NO_CONTENT else {}
                return True, json.dumps({
                    "message": success_message,
                    "data": data
                })
            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return True, json.dumps({
                    "message": success_message,
                    "data": {}
                })
        else:
            # Fix: response.text is a method, not a property - must call it
            error_text = response.text() if hasattr(response, 'text') else str(response)
            logger.error(f"HTTP error {response.status}: {error_text}")
            return False, json.dumps({
                "error": f"HTTP {response.status}",
                "details": error_text
            })

    async def _get_site_url(self) -> Optional[str]:
        """Get the site URL (web URL) from accessible resources.

        Returns:
            Site URL (e.g., 'https://example.atlassian.net') or None if unavailable
        """
        if self._site_url:
            return self._site_url

        try:
            # Get token from client
            client_obj = self.client._client

            # OAuth: get_base_url() is the API gateway
            # (api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2).
            # Browse URLs need the site host from accessible-resources (*.atlassian.net),
            # and we must match the cloud_id to the correct site (token may access many).
            if hasattr(client_obj, 'get_token'):
                token = client_obj.get_token()
                if token:
                    cloud_id = None
                    if hasattr(client_obj, 'get_base_url'):
                        gateway = (client_obj.get_base_url() or "").rstrip('/')
                        match = re.search(r"/ex/confluence/([^/]+)", gateway)
                        if match:
                            cloud_id = match.group(1)

                    resources = await ConfluenceClient.get_accessible_resources(token)
                    if resources:
                        if cloud_id:
                            picked = next((r for r in resources if r.id == cloud_id), None)
                            if picked is None:
                                logger.warning(
                                    "Confluence _get_site_url: cloud_id %s not found in accessible resources (%s); "
                                    "refusing to fall back to a different site.",
                                    cloud_id, [r.id for r in resources],
                                )
                                return None
                            self._site_url = picked.url.rstrip('/')
                            return self._site_url
                        # Could not extract cloud_id from the gateway URL — only safe
                        # when the token has exactly one accessible site.
                        self._site_url = resources[0].url.rstrip('/')
                        return self._site_url

            # API token / basic: get_base_url() includes /wiki/api/v2, strip it for site URL
            if hasattr(client_obj, 'get_base_url'):
                base_url = client_obj.get_base_url()
                if base_url:
                    # Remove /wiki/api/v2 suffix to get the site URL
                    site_url = base_url.rstrip('/')
                    if site_url.endswith('/wiki/api/v2'):
                        site_url = site_url[:-len('/wiki/api/v2')]
                    self._site_url = site_url
                    return self._site_url
        except Exception as e:
            logger.warning("Could not get site URL: %s", e)

        return None

    async def _resolve_space_id(self, space_identifier: str) -> str:
        """Helper method to resolve space key to numeric space ID.

        The Confluence v2 API requires numeric (long) space IDs. This method
        accepts either a numeric ID or a string key and always returns a numeric
        ID string by looking up the key in the available spaces.

        Personal space keys often carry a leading '~' (e.g. '~abc123'). The
        planner may strip or keep that prefix, so we try all variants.

        Args:
            space_identifier: Numeric space ID or string space key (with or without '~')

        Returns:
            Resolved numeric space ID string, or original value if resolution fails
        """
        # Already numeric — return as-is
        try:
            int(space_identifier)
            return space_identifier
        except ValueError:
            pass

        # Build candidate keys to try (handle leading '~' being present or absent)
        stripped = space_identifier.lstrip("~")
        candidates = {
            space_identifier,           # exact as given
            "~" + stripped,             # with ~ prefix
            stripped,                   # without ~ prefix
        }

        try:
            response = await self.client.get_spaces()
            if response.status == HttpStatusCode.SUCCESS.value:
                spaces_data = response.json()
                results = spaces_data.get("results", [])
                for space in results:
                    if not isinstance(space, dict):
                        continue
                    space_key = space.get("key", "")
                    space_name = space.get("name", "")
                    # Match by key (any candidate variant) or by name
                    if space_key in candidates or space_name == space_identifier:
                        numeric_id = space.get("id")
                        if numeric_id:
                            logger.info(
                                f"Resolved space '{space_identifier}' → id={numeric_id} "
                                f"(key='{space_key}')"
                            )
                            return str(numeric_id)
        except Exception as e:
            logger.warning(f"Failed to resolve space identifier '{space_identifier}': {e}")

        # Resolution failed — return original and let the API surface the error
        logger.warning(
            f"Could not resolve space identifier '{space_identifier}' to a numeric ID"
        )
        return space_identifier

    @staticmethod
    def _extract_space_info(item: dict[str, Any]) -> tuple[str, str]:
        """Extract (space_key, space_name) from a v1 CQL search result item.

        Confluence's v1 CQL search puts space info in two possible places.
        ``expand=space`` populates ``content.space`` for some content types
        but for most page/blogpost results the space is in the top-level
        ``resultGlobalContainer`` instead, which has the shape:

            {"title": "<space name>", "displayUrl": "/spaces/<KEY>"}

        Without the fallback below every result came back with empty
        ``space_key`` / ``space_name``. Both ``search_content`` and
        ``search_pages`` (filter mode) share this loop, so the extraction
        lives here.

        Returns:
            Tuple of (space_key, space_name). Either may be an empty string
            when neither source has the field.
        """
        content = item.get("content") if isinstance(item, dict) else None
        space_info = (content or {}).get("space") or {}
        container = item.get("resultGlobalContainer") if isinstance(item, dict) else None
        container = container or {}

        space_key = space_info.get("key") or ""
        space_name = space_info.get("name") or container.get("title") or ""

        if not space_key:
            display_url = container.get("displayUrl") or ""
            if isinstance(display_url, str) and display_url.startswith("/spaces/"):
                space_key = display_url[len("/spaces/"):].strip("/").split("/")[0]

        return space_key, space_name

    @tool(
        path="/tools/confluence/create_page",
        short_description="Create a page in Confluence",
        description=(
            "Create a page in Confluence. Requires space_id (numeric ID or key), page_title, "
            "and page_content (HTML storage format). Call confluence.get_spaces first if the "
            "space is not yet resolved.\n"
            "\n"
            "Use when the user wants to create a Confluence page, add documentation, or create "
            "a wiki page. Do not use for searching or reading pages."
        ),
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID or key (e.g. '~abc123', 'SD', '12345'). IMPORTANT: Resolve via confluence.get_spaces if not already known from Reference Data or conversation history.", required=True),
            ToolParameter(name="page_title", type=ParameterType.STRING, description="Page title", required=True),
            ToolParameter(name="page_content", type=ParameterType.STRING, description="Page content in storage format", required=True),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="write")],
        args_summary=args_template('Creating Confluence page "{page_title}"', "page_title"),
        result_summary=entity_summary(lambda e: f"Created page: {_confluence_page_label(e)}"),
    )
    async def create_page(
        self,
        space_id: str,
        page_title: str,
        page_content: str
    ) -> tuple[bool, str]:
        """Create a page in Confluence.

        Args:
            space_id: The ID or key of the space
            page_title: The title of the page
            page_content: The content of the page in Confluence storage format (HTML-like tags)

        **CRITICAL: Content Format Requirements**

        The `page_content` parameter MUST contain the FULL actual HTML content in Confluence storage format.
        This content is sent DIRECTLY to Confluence - it is NOT processed or modified.

        **Format Requirements:**
        - Use HTML-like tags: `<h1>`, `<h2>`, `<p>`, `<ul>`, `<li>`, `<strong>`, `<em>`, etc.
        - Use `<br/>` for line breaks
        - Use `<code>` for inline code, `<pre><code>` for code blocks
        - Lists: `<ul><li>Item</li></ul>` or `<ol><li>Item</li></ol>`

        **Content Generation:**
        - Extract content from conversation history or tool results
        - Convert markdown to HTML format:
          - `# Title` → `<h1>Title</h1>`
          - `## Section` → `<h2>Section</h2>`
          - `**bold**` → `<strong>bold</strong>`
          - `- Item` → `<ul><li>Item</li></ul>`
          - Code blocks: ` ```bash\ncmd\n``` ` → `<pre><code>cmd</code></pre>`
        - Include ALL sections, details, bullets, code blocks
        - NEVER include instruction text or placeholders

        **Example:**
        ```python
        page_content = "<h1>Deployment Guide</h1><h2>Prerequisites</h2><ul><li>Docker</li><li>Docker Compose</li></ul><h2>Steps</h2><pre><code>docker compose up</code></pre>"
        ```

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id = await self._resolve_space_id(space_id)

            body = {
                "title": page_title,
                "spaceId": resolved_space_id,
                "body": {
                    "storage": {
                        "value": page_content,
                        "representation": "storage"
                    }
                }
            }

            response = await self.client.create_page(body=body)
            result = self._handle_response(response, "Page created successfully")

            # Add web URL if successful
            if result[0] and response.status in [HttpStatusCode.SUCCESS.value, HttpStatusCode.CREATED.value]:
                try:
                    data = response.json()
                    page_id = data.get("id")
                    space_key = data.get("spaceId") or resolved_space_id
                    if page_id:
                        site_url = await self._get_site_url()
                        if site_url:
                            # Try to get space key from response or use resolved space ID
                            # For Confluence, we need space key, not ID for URL
                            # Try to resolve space key from ID if needed
                            space_key_for_url = space_key
                            try:
                                int(space_key)  # Check if it's numeric
                                # It's numeric, try to get key from spaces
                                spaces_response = await self.client.get_spaces()
                                if spaces_response.status == HttpStatusCode.SUCCESS.value:
                                    spaces_data = spaces_response.json()
                                    for space in spaces_data.get("results", []):
                                        if str(space.get("id")) == str(space_key):
                                            space_key_for_url = space.get("key", space_key)
                                            break
                            except ValueError:
                                pass  # Already a key

                            web_url = f"{site_url}/wiki/spaces/{space_key_for_url}/pages/{page_id}"
                            result_data = json.loads(result[1])
                            if "data" in result_data and isinstance(result_data["data"], dict):
                                result_data["data"]["url"] = web_url
                            result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URL to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @staticmethod
    def _harvest_mentions(html_content: str) -> dict[str, str]:
        """Extract accountId → displayName pairs from export_view HTML.

        The export_view format renders @mentions as anchor tags:
          <a ... class="confluence-userlink user-mention" data-account-id="XXXX">Full Name</a>
        Harvesting these pairs at parse time gives us name resolution at zero
        extra API cost.
        """
        mapping: dict[str, str] = {}
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for tag in soup.find_all("a", attrs={"data-account-id": True}):
                account_id = tag.get("data-account-id", "").strip()
                name = tag.get_text(strip=True)
                if account_id and name:
                    mapping[account_id] = name
        except Exception as exc:
            logger.debug("Mention harvest failed: %s", exc)
        return mapping

    async def _bulk_resolve_users(self, account_ids: set[str]) -> dict[str, dict[str, str]]:
        """Batch-resolve accountIds to user info via POST /users-bulk.

        Confluence's POST /users-bulk accepts up to 25 accountIds per request.
        Returns a mapping of {accountId: {displayName, email?}}.
        """
        if not account_ids:
            return {}
        resolved: dict[str, dict[str, str]] = {}
        ids_list = list(account_ids)
        for i in range(0, len(ids_list), 25):
            chunk = ids_list[i : i + 25]
            try:
                response = await self.client.create_bulk_user_lookup(
                    body={"accountIds": chunk}
                )
                if response.status == HttpStatusCode.SUCCESS.value:
                    data = response.json()
                    for user in data.get("results", []):
                        aid = user.get("accountId", "")
                        if aid:
                            entry: dict[str, str] = {
                                "displayName": user.get("displayName", aid)
                            }
                            email = user.get("email") or user.get("emailAddress")
                            if email:
                                entry["email"] = email
                            resolved[aid] = entry
            except Exception as exc:
                logger.warning("users-bulk chunk failed: %s", exc)
        return resolved

    @tool(
        path="/tools/confluence/get_page_content",
        short_description="Get the content of a Confluence page",
        description=(
            "Retrieve the full content and metadata of a Confluence page by its ID. "
            "Returns page content as markdown (default) or storage XML, along with comments, "
            "attachment metadata, and resolved user display names for all @mentions and authors.\n"
            "\n"
            "body_format options:\n"
            "  - 'markdown' (default): clean readable markdown, best for summarising, reading, "
            "and answering questions. Token-efficient for the LLM.\n"
            "  - 'storage': raw Confluence storage XML. Use ONLY when you intend to call "
            "update_page with the result, since update_page requires storage format.\n"
            "\n"
            "Comments and attachments are fetched in parallel and are non-fatal — if they fail, "
            "the page content is still returned. Use download_attachment to retrieve attachment bytes."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID", required=True),
            ToolParameter(
                name="body_format",
                type=ParameterType.STRING,
                description="'markdown' (default) for readable output; 'storage' only when calling update_page next.",
                required=False,
                default="markdown",
            ),
            ToolParameter(
                name="include_comments",
                type=ParameterType.BOOLEAN,
                description="Include footer and inline comments (default true).",
                required=False,
                default=True,
            ),
            ToolParameter(
                name="include_attachments",
                type=ParameterType.BOOLEAN,
                description="Include attachment metadata (default true). Use download_attachment to get file content.",
                required=False,
                default=True,
            ),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching Confluence page {page_id}", "page_id"),
        result_summary=entity_summary(lambda e: f"Fetched page: {_confluence_page_label(e)}"),
    )
    async def get_page_content(
        self,
        page_id: str,
        body_format: str = "markdown",
        include_comments: bool = True,
        include_attachments: bool = True,
    ) -> tuple[bool, str]:
        """Get the content of a page in Confluence with resolved users, comments, and attachments."""
        try:
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            # Determine which body format to request from the API.
            # For markdown output we fetch export_view (rendered HTML with resolved mentions).
            # For storage output we fetch storage directly.
            api_body_format = "export_view" if body_format == "markdown" else "storage"

            # Build parallel tasks — comments/attachments are optional and non-fatal.
            tasks: list[Any] = [
                self.client.get_page_by_id(id=page_id_int, body_format=api_body_format),
            ]
            if include_comments:
                tasks.append(self.client.get_page_footer_comments(id=page_id_int, body_format={"representation": "storage"}))
                tasks.append(self.client.get_page_inline_comments(id=page_id_int, body_format={"representation": "storage"}))
            if include_attachments:
                tasks.append(self.client.get_page_attachments(id=page_id_int, limit=50))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            page_response = results[0]
            if isinstance(page_response, Exception):
                logger.error("get_page_content: page fetch failed: %s", page_response)
                return False, json.dumps({"error": str(page_response)})
            if page_response.status not in (
                HttpStatusCode.SUCCESS.value,
                HttpStatusCode.CREATED.value,
            ):
                err = page_response.text() if hasattr(page_response, "text") else str(page_response)
                return False, json.dumps({"error": f"HTTP {page_response.status}", "details": err})

            page_data = page_response.json()

            # --- Extract and convert page body ---
            raw_body = ""
            body_node = page_data.get("body", {})
            for fmt in ("export_view", "storage", "view"):
                raw_body = (body_node.get(fmt) or {}).get("value", "")
                if raw_body:
                    break

            mention_map: dict[str, str] = {}
            if body_format == "markdown":
                mention_map = self._harvest_mentions(raw_body)
                content = markdownify(raw_body, heading_style="ATX").strip()
            else:
                content = raw_body

            # --- Parse side-channel results ---
            result_idx = 1
            footer_comments: list[dict] = []
            inline_comments: list[dict] = []
            attachments_list: list[dict] = []

            if include_comments:
                footer_resp = results[result_idx]
                inline_resp = results[result_idx + 1]
                result_idx += 2

                if not isinstance(footer_resp, Exception) and footer_resp.status == HttpStatusCode.SUCCESS.value:
                    for c in footer_resp.json().get("results", []):
                        comment_body_val = ((c.get("body") or {}).get("storage") or {}).get("value", "")
                        footer_comments.append({
                            "id": c.get("id"),
                            "authorId": (c.get("version") or {}).get("authorId"),
                            "createdAt": (c.get("version") or {}).get("createdAt"),
                            "body": comment_body_val,
                        })
                else:
                    logger.warning("get_page_content: footer comments unavailable: %s", footer_resp)

                if not isinstance(inline_resp, Exception) and inline_resp.status == HttpStatusCode.SUCCESS.value:
                    for c in inline_resp.json().get("results", []):
                        comment_body_val = ((c.get("body") or {}).get("storage") or {}).get("value", "")
                        inline_comments.append({
                            "id": c.get("id"),
                            "authorId": (c.get("version") or {}).get("authorId"),
                            "createdAt": (c.get("version") or {}).get("createdAt"),
                            "resolvedAt": c.get("resolvedAt"),
                            "body": comment_body_val,
                        })
                else:
                    logger.warning("get_page_content: inline comments unavailable: %s", inline_resp)

            if include_attachments:
                att_resp = results[result_idx]
                result_idx += 1
                if not isinstance(att_resp, Exception) and att_resp.status == HttpStatusCode.SUCCESS.value:
                    for a in att_resp.json().get("results", []):
                        attachments_list.append({
                            "id": a.get("id"),
                            "title": a.get("title"),
                            "mimeType": a.get("mediaType"),
                            "fileSize": a.get("fileSize"),
                            "comment": a.get("comment"),
                        })
                else:
                    logger.warning("get_page_content: attachments unavailable: %s", att_resp)

            # --- Collect all accountIds that still need resolution ---
            harvested_ids = set(mention_map.keys())
            ids_to_resolve: set[str] = set()

            author_id = (page_data.get("version") or {}).get("authorId")
            # ownerId is already an accountId string in the v2 API; createdBy
            # (v1 shape) is a dict with an accountId key — handle both.
            owner_id = page_data.get("ownerId")
            if not owner_id:
                created_by = page_data.get("createdBy")
                if isinstance(created_by, dict):
                    owner_id = created_by.get("accountId")
            for aid in filter(None, [author_id, owner_id]):
                if aid not in harvested_ids:
                    ids_to_resolve.add(aid)

            for c in footer_comments + inline_comments:
                aid = c.get("authorId")
                if aid and aid not in harvested_ids:
                    ids_to_resolve.add(aid)

            bulk_resolved = await self._bulk_resolve_users(ids_to_resolve)

            # Merge: export_view harvest wins over /users-bulk for parity.
            resolved_users: dict[str, dict[str, str]] = {}
            for aid, name in mention_map.items():
                resolved_users[aid] = {"displayName": name}
            for aid, info in bulk_resolved.items():
                resolved_users.setdefault(aid, info)

            # --- Build web URL ---
            web_url = None
            space_id = page_data.get("spaceId")
            if space_id:
                site_url = await self._get_site_url()
                if site_url:
                    space_key = space_id
                    try:
                        int(space_id)
                        spaces_response = await self.client.get_spaces()
                        if spaces_response.status == HttpStatusCode.SUCCESS.value:
                            for space in spaces_response.json().get("results", []):
                                if str(space.get("id")) == str(space_id):
                                    space_key = space.get("key", space_id)
                                    break
                    except ValueError:
                        pass
                    web_url = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"

            # --- Assemble response ---
            response_body: dict[str, Any] = {
                "message": "Page content fetched successfully",
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "spaceId": space_id,
                "status": page_data.get("status"),
                "contentFormat": body_format,
                "content": content,
                "version": page_data.get("version"),
                "resolvedUsers": resolved_users,
            }
            if web_url:
                response_body["url"] = web_url
            if include_comments:
                response_body["footerComments"] = footer_comments
                response_body["inlineComments"] = inline_comments
            if include_attachments:
                response_body["attachments"] = attachments_list
                if attachments_list:
                    response_body["attachmentsNote"] = (
                        "Use download_attachment(page_id, attachment_id) to retrieve the file content "
                        "of any attachment listed above."
                    )

            return True, json.dumps(response_body)

        except Exception as e:
            logger.error("Error getting page content: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/list_page_attachments",
        short_description="List attachments on a Confluence page",
        description=(
            "List all file attachments on a Confluence page, with metadata (id, title, mimeType, "
            "fileSize). Use when you need the full attachment list for a page, or when filtering "
            "by media type (e.g. 'application/pdf'). The attachment id returned here is the input "
            "for download_attachment and get_attachment_metadata."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID", required=True),
            ToolParameter(
                name="media_type",
                type=ParameterType.STRING,
                description="Optional MIME type filter, e.g. 'application/pdf' or 'image/png'.",
                required=False,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Max attachments to return (1-250). Default 25.",
                required=False,
                default=25,
            ),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template("Listing attachments on page {page_id}", "page_id"),
        result_summary=list_summary("results", lambda a: a.get("title") or a.get("id") or "?", "attachment"),
    )
    async def list_page_attachments(
        self,
        page_id: str,
        media_type: Optional[str] = None,
        limit: Optional[int] = 25,
    ) -> tuple[bool, str]:
        """List file attachments on a Confluence page."""
        try:
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            response = await self.client.get_page_attachments(
                id=page_id_int,
                mediaType=media_type,
                limit=limit,
            )
            if response.status != HttpStatusCode.SUCCESS.value:
                err = response.text() if hasattr(response, "text") else str(response)
                return False, json.dumps({"error": f"HTTP {response.status}", "details": err})

            data = response.json()
            attachments = []
            for a in data.get("results", []):
                attachments.append({
                    "id": a.get("id"),
                    "title": a.get("title"),
                    "mimeType": a.get("mediaType"),
                    "fileSize": a.get("fileSize"),
                    "comment": a.get("comment"),
                    "pageId": page_id,
                })

            return True, json.dumps({
                "message": f"Found {len(attachments)} attachment(s)",
                "pageId": page_id,
                "total": len(attachments),
                "results": attachments,
                "note": "Use download_attachment(page_id, attachment_id) to retrieve file content.",
            })

        except Exception as e:
            logger.error("Error listing page attachments: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/get_attachment_metadata",
        short_description="Get metadata for a single Confluence attachment",
        description=(
            "Retrieve metadata for a specific attachment by its ID: title, mimeType, fileSize, "
            "and version info. Use when you have an attachment ID but need its details before "
            "deciding whether to download it. For listing all attachments on a page use "
            "list_page_attachments."
        ),
        parameters=[
            ToolParameter(
                name="attachment_id",
                type=ParameterType.STRING,
                description="Attachment ID (from list_page_attachments or get_page_content).",
                required=True,
            ),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching attachment metadata {attachment_id}", "attachment_id"),
        result_summary=entity_summary(lambda e: f"Attachment: {e.get('title') or e.get('id') or '?'}"),
    )
    async def get_attachment_metadata(self, attachment_id: str) -> tuple[bool, str]:
        """Get metadata for a single attachment."""
        try:
            response = await self.client.get_attachment_by_id(id=attachment_id)
            if response.status != HttpStatusCode.SUCCESS.value:
                err = response.text() if hasattr(response, "text") else str(response)
                return False, json.dumps({"error": f"HTTP {response.status}", "details": err})

            data = response.json()
            return True, json.dumps({
                "message": "Attachment metadata fetched successfully",
                "id": data.get("id"),
                "title": data.get("title"),
                "mimeType": data.get("mediaType"),
                "fileSize": data.get("fileSize"),
                "comment": data.get("comment"),
                "version": data.get("version"),
            })

        except Exception as e:
            logger.error("Error fetching attachment metadata: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/download_attachment",
        short_description="Download a Confluence attachment and register it as an artifact",
        description=(
            "Download an attachment from a Confluence page and save it as a versioned artifact. "
            "Returns an artifact_id you can pass into run_code's input_artifacts, or into "
            "get_artifact_download_url for a direct download link. Files larger than 25 MiB are "
            "rejected before buffering. Use list_page_attachments or get_page_content to discover "
            "attachment IDs first."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID that owns the attachment.", required=True),
            ToolParameter(name="attachment_id", type=ParameterType.STRING, description="Attachment ID (from list_page_attachments).", required=True),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template("Downloading attachment {attachment_id}", "attachment_id"),
        result_summary=entity_summary(lambda e: f"Attachment artifact: {e.get('name') or e.get('artifact_id') or '?'}"),
    )
    async def download_attachment(self, page_id: str, attachment_id: str) -> tuple[bool, str]:
        """Download an attachment and register it as a tracked artifact."""
        registry = self._registry()
        if registry is None:
            return False, json.dumps({
                "error": "Artifact registry is unavailable — attachment downloads require the agent runtime context."
            })

        # Fetch metadata to get filename and MIME type before streaming.
        meta_response = await self.client.get_attachment_by_id(id=attachment_id)
        if meta_response.status != HttpStatusCode.SUCCESS.value:
            err = meta_response.text() if hasattr(meta_response, "text") else str(meta_response)
            return False, json.dumps({"error": f"Could not fetch attachment metadata: HTTP {meta_response.status}", "details": err})

        meta = meta_response.json()
        filename: str = meta.get("title") or attachment_id
        mime_type: str = meta.get("mediaType") or "application/octet-stream"
        reported_size: int = meta.get("fileSize") or 0

        if reported_size > MAX_ARTIFACT_BYTES:
            return False, json.dumps({
                "error": (
                    f"Attachment '{filename}' is {reported_size:,} bytes, which exceeds the "
                    f"{MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB limit."
                )
            })

        # Stream and accumulate, enforcing the cap before full buffering.
        chunks: list[bytes] = []
        total_bytes = 0
        try:
            async for chunk in self.client.download_attachment(
                parent_page_id=page_id,
                attachment_id=attachment_id,
            ):
                total_bytes += len(chunk)
                if total_bytes > MAX_ARTIFACT_BYTES:
                    return False, json.dumps({
                        "error": (
                            f"Attachment '{filename}' exceeds the "
                            f"{MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB streaming limit."
                        )
                    })
                chunks.append(chunk)
        except Exception as e:
            logger.error("download_attachment: streaming failed: %s", e)
            return False, json.dumps({"error": f"Download failed: {e}"})

        content_bytes = b"".join(chunks)

        # Infer artifact type from MIME.
        if mime_type.startswith("image/"):
            artifact_type = ArtifactType.IMAGE
        elif mime_type in ("application/pdf",):
            artifact_type = ArtifactType.DOCUMENT
        elif mime_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ):
            artifact_type = ArtifactType.DOCUMENT
        elif mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        ):
            artifact_type = ArtifactType.SPREADSHEET
        elif mime_type in (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-powerpoint",
        ):
            artifact_type = ArtifactType.PRESENTATION
        else:
            artifact_type = ArtifactType.DOCUMENT

        conversation_id: Optional[str] = None
        if self.chat_state:
            conversation_id = self.chat_state.get("conversation_id") or self.chat_state.get("session_id")

        try:
            artifact = await registry.register(
                actor=self._actor(),
                name=filename,
                artifact_type=artifact_type,
                mime_type=mime_type,
                content=content_bytes,
                conversation_id=conversation_id,
                is_temporary=True,
                connector_name=Connectors.CONFLUENCE,
                source_tool="confluence.download_attachment",
            )
        except Exception as e:
            logger.error("download_attachment: registry.register failed: %s", e)
            return False, json.dumps({"error": f"Failed to register artifact: {e}"})

        return True, json.dumps({
            "message": "Attachment downloaded and registered as artifact",
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
            "mimeType": mime_type,
            "sizeBytes": total_bytes,
            "note": (
                "Pass artifact_id into run_code's input_artifacts to process this file, "
                "or call get_artifact_download_url for a direct download link."
            ),
        })

    @tool(
        path="/tools/confluence/get_pages_in_space",
        short_description="List pages in a Confluence space",
        description=(
            "Enumerate pages in a Confluence space (v2 API). Supports sorting by id, title, "
            "created-date, or modified-date (prefix with '-' for descending). Use '-modified-date' "
            "for recently updated, '-created-date' for newest first, 'title' for A-Z.\n"
            "\n"
            "Use for simple space page listings without authorship/date/label filters. "
            "For author-aware queries use search_content with space_id + contributor/creator slots. "
            "For keyword/topic searches use search_content. For finding a specific page by name use search_pages."
        ),
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID or key", required=True),
            ToolParameter(name="sort_by", type=ParameterType.STRING, description="Optional v2-API sort. Allowed: 'id', 'title', 'created-date', 'modified-date'. Prefix with '-' for descending.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Max pages per page of results (Confluence v2 caps at 250).", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template("Listing pages in Confluence space {space_id}", "space_id"),
        result_summary=list_summary("results", _confluence_page_label, "page"),
    )
    async def get_pages_in_space(
        self,
        space_id: str,
        sort_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Get pages in a space (v2 enumeration; no contributor filter).

        For author-aware queries (`pages I updated in space SD`) route through
        `search_content` with `space_id` plus the appropriate slot — those need
        CQL which this v2 endpoint doesn't expose. This method's `sort_by` and
        `limit` are the v2 enumeration knobs only.

        Args:
            space_id: ID or key of the space.
            sort_by: Optional v2 sort — 'id', 'title', 'created-date',
                     'modified-date', or any of those prefixed with '-' for
                     descending. Most useful: '-modified-date' for "recently
                     updated first".
            limit: Optional max pages per response.

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id = await self._resolve_space_id(space_id)
            response = await self.client.get_pages_in_space(
                id=resolved_space_id,
                sort=sort_by,
                limit=limit,
            )
            result = self._handle_response(response, "Pages fetched successfully")

            # Add web URLs if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    response.json()
                    site_url = await self._get_site_url()
                    if site_url:
                        # Get space key
                        space_key = space_id
                        try:
                            int(resolved_space_id)  # Check if it's numeric
                            spaces_response = await self.client.get_spaces()
                            if spaces_response.status == HttpStatusCode.SUCCESS.value:
                                spaces_data = spaces_response.json()
                                for space in spaces_data.get("results", []):
                                    if str(space.get("id")) == str(resolved_space_id):
                                        space_key = space.get("key", space_id)
                                        break
                        except ValueError:
                            pass  # Already a key

                        # Add URLs to pages
                        result_data = json.loads(result[1])
                        if "data" in result_data:
                            pages = result_data["data"]
                            if isinstance(pages, dict) and "results" in pages:
                                for page in pages["results"]:
                                    page_id = page.get("id")
                                    if page_id:
                                        page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                            elif isinstance(pages, list):
                                for page in pages:
                                    page_id = page.get("id")
                                    if page_id:
                                        page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                        result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URLs to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error getting pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/update_page_title",
        short_description="Rename a Confluence page",
        description=(
            "Update the title of a Confluence page. Use when the user wants to rename "
            "or change a page title. Do not use for creating pages (use create_page) "
            "or updating page content (use update_page)."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID", required=True),
            ToolParameter(name="new_title", type=ParameterType.STRING, description="New title", required=True),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="write")],
    )
    async def update_page_title(self, page_id: str, new_title: str) -> tuple[bool, str]:
        """Update the title of a page.

        Args:
            page_id: The ID of the page
            new_title: The new title

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            response = await self.client.update_page_title(
                id=page_id_int,
                body={"title": new_title}
            )
            return self._handle_response(response, "Page title updated successfully")

        except Exception as e:
            logger.error(f"Error updating page title: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/get_child_pages",
        short_description="Get child pages of a Confluence page",
        description=(
            "Get direct child (sub) pages of a Confluence page (v2 API). Supports sorting by "
            "id, title, created-date, or modified-date (prefix with '-' for descending).\n"
            "\n"
            "Use when the user wants sub-pages of a known page without authorship/date/label filters. "
            "For author-filtered child pages use search_content with CQL. For all pages in a space "
            "use get_pages_in_space. For reading page content use get_page_content."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The parent page ID", required=True),
            ToolParameter(name="sort_by", type=ParameterType.STRING, description="Optional v2-API sort. Allowed: 'id', 'title', 'created-date', 'modified-date'. Prefix with '-' for descending.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Max child pages per page of results (Confluence v2 caps at 250).", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
    )
    async def get_child_pages(
        self,
        page_id: str,
        sort_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Get direct child pages of a page (v2 enumeration; no contributor filter).

        For author-aware queries (`child pages of X that I edited`) route through
        `search_content` with the parent context — those need CQL which this v2
        endpoint doesn't expose. `sort_by` and `limit` are v2 enumeration knobs.

        Args:
            page_id: ID of the parent page.
            sort_by: Optional v2 sort — 'id', 'title', 'created-date',
                     'modified-date', or any of those prefixed with '-' for
                     descending. Most useful: '-modified-date' for "recently
                     updated first".
            limit: Optional max pages per response.

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            response = await self.client.get_child_pages(
                id=page_id_int,
                sort=sort_by,
                limit=limit,
            )
            result = self._handle_response(response, "Child pages fetched successfully")

            # Add web URLs if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    response.json()
                    # Get parent page to find space
                    parent_response = await self.client.get_page_by_id(id=page_id_int, body_format="storage")
                    if parent_response.status == HttpStatusCode.SUCCESS.value:
                        parent_data = parent_response.json()
                        space_id = parent_data.get("spaceId")
                        if space_id:
                            site_url = await self._get_site_url()
                            if site_url:
                                # Get space key
                                space_key = space_id
                                try:
                                    int(space_id)
                                    spaces_response = await self.client.get_spaces()
                                    if spaces_response.status == HttpStatusCode.SUCCESS.value:
                                        spaces_data = spaces_response.json()
                                        for space in spaces_data.get("results", []):
                                            if str(space.get("id")) == str(space_id):
                                                space_key = space.get("key", space_id)
                                                break
                                except ValueError:
                                    pass

                                # Add URLs to child pages
                                result_data = json.loads(result[1])
                                if "data" in result_data:
                                    pages = result_data["data"]
                                    if isinstance(pages, dict) and "results" in pages:
                                        for page in pages["results"]:
                                            page_id = page.get("id")
                                            if page_id:
                                                page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                                    elif isinstance(pages, list):
                                        for page in pages:
                                            page_id = page.get("id")
                                            if page_id:
                                                page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                                result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URLs to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error getting child pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/search_pages",
        short_description="Search Confluence pages by title",
        description=(
            "Search for Confluence pages by title with optional authorship, date, label, and ordering "
            "filters. Best for resolving a page name to a page_id (e.g. 'find page named X'). "
            "Supports fuzzy title matching, space scoping, and constraints like author/date/label.\n"
            "\n"
            "Use when the user wants to find a specific named page or a named page constrained by "
            "author/date/label (e.g. 'find the FAQ page I created last quarter'). "
            "For topic/keyword searches without a page name use search_content. "
            "For 'what did I update?' queries use search_content with authorship slots."
        ),
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="Page title fragment to search (fuzzy)", required=True),
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID or key to limit search", required=False),
            ToolParameter(name="contributor", type=ParameterType.STRING, description="Filter by anyone who EVER edited the page. Pass `currentUser()` (no quotes) for self, or `\"<accountId>\"` (with double quotes) for another user — call search_users first.", required=False),
            ToolParameter(name="creator", type=ParameterType.STRING, description="Filter by original page author. Same value format as contributor.", required=False),
            ToolParameter(name="mention", type=ParameterType.STRING, description="Filter to pages that @-mention this user. Same value format as contributor.", required=False),
            ToolParameter(name="last_modifier", type=ParameterType.STRING, description="Filter by who made the most recent edit (latest version only). Same value format. Prefer `contributor` for 'pages I updated'.", required=False),
            ToolParameter(name="last_modified_after", type=ParameterType.STRING, description="ISO date ('2026-05-01') or CQL function ('now(\"-7d\")', 'startOfMonth()'). Maps to `lastmodified >= ...`.", required=False),
            ToolParameter(name="last_modified_before", type=ParameterType.STRING, description="Same value format as last_modified_after. Maps to `lastmodified <= ...`.", required=False),
            ToolParameter(name="created_after", type=ParameterType.STRING, description="Same value format as last_modified_after. Maps to `created >= ...`.", required=False),
            ToolParameter(name="created_before", type=ParameterType.STRING, description="Same value format as created_after. Maps to `created <= ...`.", required=False),
            ToolParameter(name="labels", type=ParameterType.ARRAY, description="List of label names. Maps to CQL `label in (...)`.", required=False, items={"type": "string"}),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="CQL ORDER BY clause, e.g. `'lastmodified desc'`. Set when the user asks for explicit ordering. Direction defaults to asc when omitted.", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Confluence pages: "{title}"', "title"),
        result_summary=list_summary("results", _confluence_page_label, "page"),
    )
    async def search_pages(
        self,
        title: str,
        space_id: Optional[str] = None,
        contributor: Optional[str] = None,
        creator: Optional[str] = None,
        mention: Optional[str] = None,
        last_modifier: Optional[str] = None,
        last_modified_after: Optional[str] = None,
        last_modified_before: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        labels: Optional[list[str]] = None,
        order_by: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Search for pages by title, optionally filtered by author/date/label/order.

        Two-mode behaviour, kept simple by intent:
        - **No filter slots set**: original two-pass flow — title CQL first
          (best for clean title matches), then full-text fallback when the
          title pass returns nothing.
        - **Any filter slot set**: skip title CQL (it can't carry the filters)
          and go straight to full-text CQL via `search_full_text`, passing the
          title as the body query plus all the filter slots. Same backend the
          extended `search_content` uses, so behaviour is consistent.

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Validate order_by locally to avoid an opaque Confluence 400.
            if order_by and not _ORDER_BY_PATTERN.match(order_by):
                return False, json.dumps({
                    "error": f"Invalid order_by value: {order_by!r}",
                    "guidance": (
                        "Use field name + optional asc/desc, comma-separated. "
                        "Examples: 'lastmodified desc', 'created asc', "
                        "'title', 'lastmodified desc, title asc'."
                    ),
                })

            resolved_space: Optional[str] = None
            if space_id:
                candidate = await self._resolve_space_id(space_id)
                # _resolve_space_id returns the original string when it cannot find a
                # matching space.  Only use the result when it is a numeric ID — if it
                # came back non-numeric the space doesn't exist and we search globally.
                try:
                    int(candidate)
                    resolved_space = candidate
                except (ValueError, TypeError):
                    logger.info(
                        "space_id '%s' could not be resolved to a numeric Confluence ID — searching globally",
                        space_id,
                    )

            # Detect filter mode — any of the new slots being set means we
            # need CQL (`search_full_text`), not the title-only `search_pages_cql`.
            has_filters = any([
                contributor, creator, mention, last_modifier,
                last_modified_after, last_modified_before,
                created_after, created_before,
                labels, order_by,
            ])

            results: list = []

            if has_filters:
                # Filter mode — single pass via search_full_text. Title becomes
                # the body query so it still narrows results by the title term.
                try:
                    ft_response = await self.client.search_full_text(
                        query=title,
                        space_id=resolved_space,
                        content_types=["page"],
                        limit=10,
                        contributor=contributor,
                        creator=creator,
                        mention=mention,
                        last_modifier=last_modifier,
                        last_modified_after=last_modified_after,
                        last_modified_before=last_modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        labels=labels,
                        order_by=order_by,
                    )
                except ValueError as ve:
                    return False, json.dumps({
                        "error": str(ve),
                        "guidance": (
                            "Provide a `title` term or at least one filter slot."
                        ),
                    })
                if ft_response.status not in (200, 201):
                    error_text = ft_response.text() if hasattr(ft_response, "text") else str(ft_response)
                    return False, json.dumps({"error": f"HTTP {ft_response.status}", "details": error_text})
                results = ft_response.json().get("results", [])
            else:
                # No filters — original title-first flow.
                # Pass 1 — CQL `title ~ "term*"`: best for clean title matches.
                response = await self.client.search_pages_cql(
                    search_term=title,
                    space_id=resolved_space,
                    limit=10,
                )

                if response.status not in (200, 201):
                    error_text = response.text() if hasattr(response, "text") else str(response)
                    return False, json.dumps({"error": f"HTTP {response.status}", "details": error_text})

                results = response.json().get("results", [])

                # Pass 2 — full-text search: fires when the title CQL returns
                # nothing. Same engine as the Confluence search bar, handling cases
                # where the query term appears in body content rather than the title.
                if not results:
                    try:
                        ft_response = await self.client.search_full_text(
                            query=title,
                            space_id=resolved_space,
                            content_types=["page"],
                            limit=10,
                        )
                        if ft_response.status in (200, 201):
                            results = ft_response.json().get("results", [])
                    except Exception as _fe:
                        logger.debug("Full-text fallback failed: %s", _fe)

            site_url = await self._get_site_url()
            base_url = f"{site_url}/wiki" if site_url else ""

            pages = []
            for item in results:
                c = item.get("content") or {}
                page_id = c.get("id", "")
                page_title = c.get("title", "")
                # Same `content.space` → `resultGlobalContainer` fallback as
                # search_content. Without this, every result's spaceKey was
                # empty in filter mode (regression of the same fix already
                # applied to search_content).
                space_key, _ = self._extract_space_info(item)

                entry: dict[str, Any] = {
                    "id": page_id,
                    "title": page_title,
                    "spaceKey": space_key,
                }

                webui_path = (c.get("_links") or {}).get("webui", "")
                if webui_path and base_url:
                    entry["url"] = base_url.rstrip("/") + webui_path
                elif page_id and space_key and site_url:
                    entry["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"

                pages.append(entry)

            # Rank results so the closest title match comes first.
            # Priority: exact (case-insensitive) → starts-with → contains → other.
            # This ensures results[0] is the most likely intended page and prevents
            # cascade operations (update, comment) from acting on the wrong page.
            search_lower = title.lower()

            def _rank(p: dict) -> int:
                t = p.get("title", "").lower()
                if t == search_lower:
                    return 0
                if t.startswith(search_lower):
                    return 1
                if search_lower in t:
                    return 2
                return 3

            pages.sort(key=_rank)

            response_body: dict[str, Any] = {
                "message": "Search completed successfully",
                "data": {"results": pages},
            }

            # When multiple pages match, surface a warning so the LLM confirms the
            # correct page before performing any write operation (update, comment, etc.).
            if len(pages) > 1:
                exact = [p for p in pages if p.get("title", "").lower() == search_lower]
                if exact:
                    response_body["note"] = (
                        f"Exact match found: '{exact[0]['title']}' (id={exact[0]['id']}). "
                        f"{len(pages) - 1} other page(s) also matched. "
                        "Use the exact-match result for write operations."
                    )
                else:
                    response_body["warning"] = (
                        f"{len(pages)} pages matched '{title}' — no exact title match. "
                        "Confirm the correct page with the user before performing any "
                        "write operation (update_page, comment_on_page, etc.)."
                    )

            return True, json.dumps(response_body)

        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/search_content",
        short_description="Full-text search across Confluence content",
        description=(
            "Full-text search across Confluence pages and blog posts using the platform search engine. "
            "Unlike search_pages (title-only), this searches the full body content, comments, and labels — "
            "exactly like the Confluence search bar.\n"
            "\n"
            "Supports filtering by author (contributor/creator/mention/last_modifier), date ranges, "
            "labels, content types, space, and custom ordering. At least one substantive filter "
            "(query, authorship, date, or labels) is required.\n"
            "\n"
            "Use for topic/keyword searches, authorship queries ('pages I updated'), date-bounded "
            "results, label filtering, or any combination. Do not use for creating/updating pages, "
            "reading a known page by ID (use get_page_content), listing all pages without filters "
            "(use get_pages_in_space), or finding users by name (use search_users first)."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Free-text search across page/blogpost titles, body, comments, and labels. Leave None for authorship-only / label-only / date-only queries.", required=False),
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Optional space key or numeric ID to restrict search to one space.", required=False),
            ToolParameter(name="content_types", type=ParameterType.ARRAY, description="Content types to include: 'page', 'blogpost', or both. Defaults to both.", required=False, items={"type": "string"}),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Max number of results (1-50). Default 25.", required=False, default=25),
            ToolParameter(name="contributor", type=ParameterType.STRING, description="Filter by anyone who EVER edited the page. Pass `currentUser()` (no quotes) for self, or `\"<accountId>\"` (with double quotes) for another user — call search_users first.", required=False),
            ToolParameter(name="creator", type=ParameterType.STRING, description="Filter by the original page author. Same value format as contributor.", required=False),
            ToolParameter(name="mention", type=ParameterType.STRING, description="Filter to pages that @-mention this user. Same value format as contributor.", required=False),
            ToolParameter(name="last_modifier", type=ParameterType.STRING, description="Filter by the user who made the most recent edit (latest version only). Prefer `contributor` for 'pages I updated'.", required=False),
            ToolParameter(name="last_modified_after", type=ParameterType.STRING, description="Filter to pages modified on or after this point. Pass ISO date ('2026-05-01') or CQL function ('now(\"-7d\")', 'startOfMonth()').", required=False),
            ToolParameter(name="last_modified_before", type=ParameterType.STRING, description="Same value format as last_modified_after. Maps to `lastmodified <= ...`.", required=False),
            ToolParameter(name="created_after", type=ParameterType.STRING, description="Filter to pages created on or after this point. Same value format as last_modified_after.", required=False),
            ToolParameter(name="created_before", type=ParameterType.STRING, description="Same value format as created_after. Maps to `created <= ...`.", required=False),
            ToolParameter(name="labels", type=ParameterType.ARRAY, description="List of label names. Matches pages tagged with ANY of the given labels (CQL `label in (...)`).", required=False, items={"type": "string"}),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="CQL ORDER BY clause. Examples: 'lastmodified desc', 'created desc', 'title asc'. Direction defaults to asc when omitted.", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searching Confluence: "{args["query"]}"' if args.get("query") else "Searching Confluence content"
        ),
        result_summary=list_summary(("results",), _confluence_page_label, "page"),
    )
    async def search_content(
        self,
        query: Optional[str] = None,
        space_id: Optional[str] = None,
        content_types: Optional[list[str]] = None,
        limit: Optional[int] = 25,
        contributor: Optional[str] = None,
        creator: Optional[str] = None,
        mention: Optional[str] = None,
        last_modifier: Optional[str] = None,
        last_modified_after: Optional[str] = None,
        last_modified_before: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        labels: Optional[list[str]] = None,
        order_by: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Full-text + structured search across Confluence content.

        Combines body search (`siteSearch ~`) with authorship, date, label, and
        ordering filters. See the `llm_description` on the @tool decorator for
        the exact value formats expected for each slot. The datasource builds
        and validates the CQL — this method's job is only:
        1. Validate `order_by` syntax up front (so we don't proxy a vague
           Atlassian 400 back to the planner).
        2. Resolve `space_id` from key/name to numeric ID where possible.
        3. Forward all parameters to `search_full_text`.
        4. Surface a `ValueError` from the datasource (no substantive filter)
           as a clean error+guidance tuple.
        5. Normalise the response into the existing entry shape (id, title,
           excerpt, url, space_key, space_name, labels, last_modified) and
           append the permissions-drift note.

        Returns:
            Tuple of (success, json_response).
        """
        try:
            # 1. Validate order_by locally — `_ORDER_BY_PATTERN` is compiled at
            # module load. We reject malformed values here rather than letting
            # Confluence respond with an opaque "Could not parse cql" 400.
            if order_by and not _ORDER_BY_PATTERN.match(order_by):
                return False, json.dumps({
                    "error": f"Invalid order_by value: {order_by!r}",
                    "guidance": (
                        "Use field name + optional asc/desc, comma-separated. "
                        "Examples: 'lastmodified desc', 'created asc', "
                        "'title', 'lastmodified desc, title asc'."
                    ),
                })

            # 2. Resolve space (numeric ID preferred — see _resolve_space_id docstring)
            resolved_space_id: Optional[str] = None
            if space_id:
                resolved_space_id = await self._resolve_space_id(space_id)

            # 3. Hand off to the datasource. It does CQL escaping, ORDER BY
            # appending, and the empty-query guard. We pass `query` through as
            # `None` when blank so the datasource correctly omits the
            # `siteSearch ~ ""` clause (which would 400).
            normalised_query = (query or '').strip() or None

            try:
                response = await self.client.search_full_text(
                    query=normalised_query,
                    space_id=resolved_space_id,
                    content_types=content_types,
                    limit=limit or 25,
                    contributor=contributor,
                    creator=creator,
                    mention=mention,
                    last_modifier=last_modifier,
                    last_modified_after=last_modified_after,
                    last_modified_before=last_modified_before,
                    created_after=created_after,
                    created_before=created_before,
                    labels=labels,
                    order_by=order_by,
                )
            except ValueError as ve:
                # Datasource raises when no substantive filter is set.
                return False, json.dumps({
                    "error": str(ve),
                    "guidance": (
                        "Provide at least one search constraint: a `query` term, "
                        "an authorship slot (`contributor` / `creator` / `mention` / "
                        "`last_modifier` set to `currentUser()` for self or "
                        "`\"<accountId>\"` for another user — call search_users "
                        "first), a date filter, or `labels`."
                    ),
                })

            if response.status not in [200, 201]:
                error_text = response.text() if hasattr(response, 'text') else str(response)
                return False, json.dumps({
                    "error": f"HTTP {response.status}",
                    "details": error_text
                })

            try:
                data = response.json()
            except Exception:
                return False, json.dumps({"error": "Failed to parse search response"})

            results = data.get("results", [])
            total = data.get("totalSize", len(results))

            # Extract base URL from API response _links.base (e.g., "https://pipeshub.atlassian.net/wiki")
            # This is the most reliable way to get the correct base URL
            response_links = data.get("_links", {})
            base_url = response_links.get("base", "")

            # Fallback to site_url if base_url is not available
            if not base_url:
                base_url = await self._get_site_url()
                if base_url:
                    base_url = f"{base_url}/wiki"

            # Normalise results into a clean, LLM-friendly structure
            # and inject web URLs using the base URL from API response
            cleaned: list = []
            for item in results:
                content = item.get("content") or {}
                content_id   = content.get("id", "")
                content_type = content.get("type", "page")
                title        = content.get("title", "")
                excerpt      = item.get("excerpt", "")
                # `content.space` fallback to `resultGlobalContainer` — see
                # `_extract_space_info` for the rationale. Same helper is used
                # by search_pages's filter-mode loop so the two stay aligned.
                space_key, space_name = self._extract_space_info(item)

                # Construct web URL using the webui link from API response
                # The webui link is relative (e.g., "/spaces/SD/pages/257130498/Holidays+2026")
                # Combine it with the base URL from _links.base
                webui = ""
                content_links = content.get("_links") or {}
                webui_path = content_links.get("webui", "")

                if webui_path and base_url:
                    # Combine base URL with the relative webui path
                    # webui_path already starts with "/spaces/", so just combine
                    webui = base_url.rstrip("/") + webui_path
                elif base_url and content_id and space_key:
                    # Fallback: construct URL manually if webui path is not available
                    webui = f"{base_url.rstrip('/')}/spaces/{space_key}/pages/{content_id}"
                elif webui_path:
                    # Last resort: use webui path as-is if no base URL available
                    webui = webui_path

                entry: dict[str, Any] = {
                    "id": content_id,
                    "type": content_type,
                    "title": title,
                    "space_key": space_key,
                    "space_name": space_name,
                    "excerpt": excerpt,
                    "url": webui,
                }

                # Include last-modified if available
                last_modified = item.get("lastModified") or (
                    (content.get("version") or {}).get("when", "")
                )
                if last_modified:
                    entry["last_modified"] = last_modified

                # Surface labels (the datasource already requests
                # `expand=metadata.labels`). Useful for ranking/display and lets
                # the platform-style "filter by label" UX work without an extra
                # round-trip per result. Field is omitted when empty.
                labels_payload = (content.get("metadata") or {}).get("labels") or {}
                page_labels = [
                    lbl.get("name", "")
                    for lbl in labels_payload.get("results", [])
                    if isinstance(lbl, dict) and lbl.get("name")
                ]
                if page_labels:
                    entry["labels"] = page_labels

                cleaned.append(entry)

            response_body: dict[str, Any] = {
                "message": "Search completed successfully",
                "query": query,
                "total_results": total,
                "returned": len(cleaned),
                "results": cleaned,
                # Permissions-drift note — Confluence's search index respects ACLs,
                # so pages in spaces where the user has lost read access are
                # silently filtered out. The note ensures the LLM can mention this
                # to the user when result counts look unexpectedly low.
                "note": (
                    "Showing pages you can currently view. Pages in spaces "
                    "where your read access has been revoked are excluded by "
                    "Confluence's search index."
                ),
            }
            return True, json.dumps(response_body)

        except Exception as e:
            logger.error(f"Error in search_content: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/search_users",
        short_description="Search Confluence users by name or email",
        description=(
            "Search Confluence users by display name OR email — handles whichever the user gives, "
            "you don't need to detect the format. Returns each match's accountId, which is what you "
            "wrap in double quotes (`'\"<accountId>\"'`) and pass as search_content's `contributor`, "
            "`creator`, `mention`, or `last_modifier` slot when filtering another user's activity.\n"
            "\n"
            "DO NOT call this for self-queries — pass the literal `currentUser()` to search_content "
            "directly; no lookup needed.\n"
            "\n"
            "When 2+ users match and none is an exact name/email match, the response sets "
            "`disambiguation_required: true` — stop and ask the user which person they meant.\n"
            "\n"
            "Confluence's user search only matches on display name. Atlassian Cloud privacy "
            "settings often hide email matches entirely. If this tool returns 0 results, check "
            "the guidance field in the response for next steps — it will direct you to Jira's "
            "user-search tool if one is available, which matches on email as well as name and "
            "returns the same accountId that Confluence uses."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="User's display name (full or partial) OR an email address. Both lookups run for every input.", required=True),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Max users to return (1-50). Default 10.", required=False, default=10),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Confluence users: "{query}"', "query"),
        result_summary=list_summary(("results",), _confluence_user_label, "user"),
    )
    async def search_users(
        self,
        query: str,
        max_results: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """Search Confluence users by display name or email.

        Builds CQL ``type=user AND (user.fullname ~ "<query>*" OR user ~ "<query>")``.
        Both clauses always run — name fragments match the first, usernames /
        accountIds (and emails when the index has them) match the second. The
        ranker below picks the best match across both clauses.

        Args:
            query: Display name fragment, full name, or email.
            max_results: Max users to return (1-50). Default 10.

        Returns:
            Tuple of (success, json_response). On 0 matches returns False with a
            clean ``error`` + ``guidance``. On 1 match returns the user. On 2+
            matches returns all sorted by rank, with either a ``note`` (exact
            match found) or ``disambiguation_required: true`` + ``warning``
            (no exact match — caller must stop and ask the user).
        """
        try:
            if not query or not query.strip():
                return False, json.dumps({
                    "error": "Query is required",
                    "guidance": "Pass a display name or email fragment.",
                })

            query_clean = query.strip()
            # CQL string-literal escaping comes from the centralised helper in
            # the datasource module so all builders stay consistent.
            escaped = _escape_cql_literal(query_clean)

            # Strip a trailing wildcard if the caller already added one; we
            # always append a single `*` below for prefix matching.
            if escaped.endswith('*'):
                escaped = escaped.rstrip('*')

            # Guard the wildcard-collapses-to-empty case: an input of just
            # ``"*"`` / ``"**"`` would land here as an empty string and produce
            # ``user.fullname ~ "*"`` — which Confluence reads as "match any
            # user", a needless full-table fan-out. Reject up front.
            if not escaped:
                return False, json.dumps({
                    "error": f"Query must contain non-wildcard characters; got {query_clean!r}",
                    "guidance": (
                        "Pass a name fragment (e.g. 'John') or an email "
                        "address. The action appends its own wildcard for "
                        "prefix matching."
                    ),
                })

            # The `/wiki/rest/api/search/user` endpoint accepts a small CQL
            # whitelist only — `type=user` plus `user.fullname ~ "..."`. Other
            # operators (notably `user ~ "..."`, which is for filtering CONTENT
            # by user, not for searching users themselves) come back as HTTP 400.
            # We rely on the `~` operator's token-based matching to keep email
            # inputs working: `abhishek@company.com` tokenises and matches
            # `Abhishek <Lastname>` on the local part. Atlassian Cloud privacy
            # already prevents real email-against-email matching through this
            # endpoint, so a richer OR'd CQL would not help there either.
            cql = f'type=user AND user.fullname ~ "{escaped}*"'

            # Confluence caps this endpoint at 50.
            capped_limit = min(max_results or 10, 50)

            response = await self.client.search_users(cql=cql, limit=capped_limit)

            if response.status not in (
                HttpStatusCode.SUCCESS.value,
                HttpStatusCode.CREATED.value,
            ):
                error_text = response.text() if hasattr(response, 'text') else str(response)
                return False, json.dumps({
                    "error": f"HTTP {response.status}",
                    "details": error_text,
                })

            try:
                data = response.json()
            except Exception:
                return False, json.dumps({"error": "Failed to parse user search response"})

            raw_results = data.get("results", []) if isinstance(data, dict) else []

            # Rank by closeness to the query, case-insensitive.
            #   0 = exact match on displayName or email
            #   1 = starts-with displayName or email
            #   2 = contains displayName or email
            #   3 = anything else (CQL OR-clause hit but no string overlap —
            #       can happen when matching by accountId/username only)
            q_lower = query_clean.lower()

            def _rank(name: str, email: str) -> int:
                n = (name or "").lower()
                e = (email or "").lower()
                if n == q_lower or (e and e == q_lower):
                    return 0
                if n.startswith(q_lower) or (e and e.startswith(q_lower)):
                    return 1
                if q_lower in n or (e and q_lower in e):
                    return 2
                return 3

            users: list[dict[str, Any]] = []
            for item in raw_results:
                user_obj = item.get("user") if isinstance(item, dict) else None
                if not isinstance(user_obj, dict):
                    continue
                account_id = user_obj.get("accountId")
                if not account_id:
                    # Anonymized / closed accounts have no accountId; the LLM
                    # can't use them as a CQL filter value, so skip them.
                    continue
                display_name = (
                    user_obj.get("displayName")
                    or user_obj.get("publicName")
                    or ""
                )
                email = user_obj.get("email") or ""
                cleaned_user: dict[str, Any] = {
                    "accountId": account_id,
                    "displayName": display_name,
                    "rank": _rank(display_name, email),
                }
                if email:
                    cleaned_user["email"] = email
                account_status = user_obj.get("accountStatus")
                if account_status:
                    cleaned_user["accountStatus"] = account_status
                users.append(cleaned_user)

            # Sort by rank, then displayName for deterministic output.
            users.sort(key=lambda u: (u["rank"], u["displayName"].lower()))

            body: dict[str, Any] = {
                "query": query_clean,
                "total": len(users),
                "results": users,
            }

            if not users:
                body["error"] = f"No Confluence users matched {query_clean!r}"
                body["guidance"] = (
                    "No Confluence users matched. Confluence's user search only matches display "
                    "names, and Atlassian Cloud privacy settings often hide email matches. "
                    "If you have a Jira or Atlassian user-search tool available, search there "
                    "instead — Jira's user picker matches on email as well as name, and the "
                    "accountId it returns is the same accountId Confluence uses, so you can pass "
                    "it straight into Confluence's contributor / creator / mention filters. "
                    "Otherwise, ask the user for the person's full display name."
                )
                return False, json.dumps(body)

            if len(users) == 1:
                body["message"] = "User found"
                return True, json.dumps(body)

            # 2+ matches — disambiguate.
            exact_matches = [u for u in users if u["rank"] == 0]
            if len(exact_matches) == 1:
                body["message"] = "User found"
                body["note"] = (
                    f"Exact match: {exact_matches[0]['displayName']!r} "
                    f"(accountId={exact_matches[0]['accountId']}). "
                    f"{len(users) - 1} other partial match(es) also returned. "
                    "Use the exact-match result for any downstream call."
                )
            else:
                body["disambiguation_required"] = True
                body["message"] = "Multiple users matched — disambiguation required"
                body["warning"] = (
                    f"{len(users)} Confluence users matched {query_clean!r} and "
                    "none is an exact name match. Stop and ask the user which "
                    "person they meant before any downstream call."
                )

            return True, json.dumps(body)

        except Exception as e:
            logger.error(f"Error searching Confluence users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/get_spaces",
        short_description="List all accessible Confluence spaces",
        description=(
            "Get all Confluence spaces accessible to the current user, including id, key, name, "
            "and type. Also used to resolve space names/types (e.g. personal space) to their "
            "numeric ID or key before creating/updating pages.\n"
            "\n"
            "Use when the user wants to list spaces, needs to resolve a space by name, or before "
            "creating a page when the space ID is unknown. Do not use when the space ID is already "
            "known from conversation history."
        ),
        parameters=[],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
        args_summary=lambda _args: "Fetching Confluence spaces",
        result_summary=list_summary("results", _confluence_space_label, "space"),
    )
    async def get_spaces(self) -> tuple[bool, str]:
        """Get all spaces accessible to the user.

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = await self.client.get_spaces()
            result = self._handle_response(response, "Spaces fetched successfully")

            # Add web URLs if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    site_url = await self._get_site_url()
                    if site_url:
                        result_data = json.loads(result[1])
                        if "data" in result_data:
                            spaces = result_data["data"]
                            if isinstance(spaces, dict) and "results" in spaces:
                                for space in spaces["results"]:
                                    space_key = space.get("key")
                                    if space_key:
                                        space["url"] = f"{site_url}/wiki/spaces/{space_key}"
                            elif isinstance(spaces, list):
                                for space in spaces:
                                    space_key = space.get("key")
                                    if space_key:
                                        space["url"] = f"{site_url}/wiki/spaces/{space_key}"
                        result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URLs to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error getting spaces: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/get_space",
        short_description="Get details of a Confluence space",
        description=(
            "Get details of a specific Confluence space by its numeric ID. "
            "Use when the user wants info about a particular space. "
            "For listing all spaces use get_spaces. For listing pages in a space use get_pages_in_space."
        ),
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Space ID", required=True),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
    )
    async def get_space(self, space_id: str) -> tuple[bool, str]:
        """Get details of a specific space.

        Args:
            space_id: The ID of the space

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert space_id to int with proper error handling
            try:
                space_id_int = int(space_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid space_id format: '{space_id}' is not a valid integer"})

            response = await self.client.get_space_by_id(id=space_id_int)
            result = self._handle_response(response, "Space fetched successfully")

            # Add web URL if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    data = response.json()
                    space_key = data.get("key")
                    if space_key:
                        site_url = await self._get_site_url()
                        if site_url:
                            web_url = f"{site_url}/wiki/spaces/{space_key}"
                            result_data = json.loads(result[1])
                            if "data" in result_data and isinstance(result_data["data"], dict):
                                result_data["data"]["url"] = web_url
                            result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URL to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error getting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/update_page",
        short_description="Update a Confluence page's title and/or content",
        description=(
            "Update a Confluence page (title and/or content). At least one of page_title or "
            "page_content must be provided. Content must be in Confluence storage format (HTML-like tags).\n"
            "\n"
            "Use when the user wants to edit or modify a page. Do not use for creating pages "
            "(use create_page), reading pages (use get_page_content), or title-only changes "
            "(use update_page_title)."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID", required=True),
            ToolParameter(name="page_title", type=ParameterType.STRING, description="New page title (optional)", required=False),
            ToolParameter(name="page_content", type=ParameterType.STRING, description="New page content in storage format (optional)", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="write")],
        args_summary=args_template("Updating Confluence page {page_id}", "page_id"),
        result_summary=entity_summary(lambda e: f"Updated page: {_confluence_page_label(e)}"),
    )
    async def update_page(
        self,
        page_id: str,
        page_title: Optional[str] = None,
        page_content: Optional[str] = None
    ) -> tuple[bool, str]:
        """Update a page in Confluence.

        Args:
            page_id: The ID of the page to update
            page_title: Optional new title for the page
            page_content: Optional new content for the page in Confluence storage format (HTML-like tags)

        **CRITICAL: Content Format Requirements**

        The `page_content` parameter MUST contain the FULL actual HTML content in Confluence storage format.
        This content is sent DIRECTLY to Confluence - it is NOT processed or modified.

        **Format Requirements:**
        - Use HTML-like tags: `<h1>`, `<h2>`, `<p>`, `<ul>`, `<li>`, `<strong>`, `<em>`, etc.
        - Use `<br/>` for line breaks
        - Use `<code>` for inline code, `<pre><code>` for code blocks
        - Lists: `<ul><li>Item</li></ul>` or `<ol><li>Item</li></ol>`

        **Content Generation:**
        - Extract content from conversation history or tool results
        - If updating existing content, merge with current page content (fetch first using get_page_content)
        - Convert markdown to HTML format:
          - `# Title` → `<h1>Title</h1>`
          - `## Section` → `<h2>Section</h2>`
          - `**bold**` → `<strong>bold</strong>`
          - `- Item` → `<ul><li>Item</li></ul>`
          - Code blocks: ` ```bash\ncmd\n``` ` → `<pre><code>cmd</code></pre>`
        - Include ALL sections, details, bullets, code blocks
        - NEVER include instruction text or placeholders

        **Example:**
        ```python
        page_content = "<h1>Updated Guide</h1><h2>New Section</h2><p>Additional information...</p>"
        ```

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            # Validate that at least one field is being updated
            if page_title is None and page_content is None:
                return False, json.dumps({"error": "At least one of page_title or page_content must be provided"})

            # Get current page to preserve spaceId and version
            current_response = await self.client.get_page_by_id(
                id=page_id_int,
                body_format="storage"
            )

            if current_response.status != HttpStatusCode.SUCCESS.value:
                error_text = current_response.text() if hasattr(current_response, 'text') else str(current_response)
                return False, json.dumps({
                    "error": f"Failed to get current page: HTTP {current_response.status}",
                    "details": error_text
                })

            current_data = current_response.json()

            # Extract required fields
            page_id_str = current_data.get("id")  # CRITICAL: Must include id
            space_id = current_data.get("spaceId")
            status = current_data.get("status")  # CRITICAL: Must include status
            version = current_data.get("version", {})
            version_number = version.get("number", 1)

            # Build update body with ALL required fields
            body: dict[str, Any] = {
                "id": page_id_str,  # ✅ REQUIRED by API
                "status": status,   # ✅ REQUIRED by API
                "spaceId": space_id,  # ✅ REQUIRED by API
                "version": {
                    "number": version_number + 1
                }
            }

            # Update title if provided
            if page_title is not None:
                body["title"] = page_title
            else:
                # Preserve existing title
                body["title"] = current_data.get("title", "")

            # Update content if provided
            if page_content is not None:
                body["body"] = {
                    "storage": {
                        "value": page_content,
                        "representation": "storage"
                    }
                }
            else:
                # Preserve existing body
                body["body"] = current_data.get("body", {})

            response = await self.client.update_page(
                id=page_id_int,
                body=body
            )
            result = self._handle_response(response, "Page updated successfully")

            # Add web URL if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    data = response.json()
                    page_id_from_data = data.get("id")
                    space_id = data.get("spaceId")
                    if page_id_from_data and space_id:
                        site_url = await self._get_site_url()
                        if site_url:
                            # Get space key
                            space_key = space_id
                            try:
                                int(space_id)
                                spaces_response = await self.client.get_spaces()
                                if spaces_response.status == HttpStatusCode.SUCCESS.value:
                                    spaces_data = spaces_response.json()
                                    for space in spaces_data.get("results", []):
                                        if str(space.get("id")) == str(space_id):
                                            space_key = space.get("key", space_id)
                                            break
                            except ValueError:
                                pass

                            web_url = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id_from_data}"
                            result_data = json.loads(result[1])
                            if "data" in result_data and isinstance(result_data["data"], dict):
                                result_data["data"]["url"] = web_url
                            result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URL to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error updating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/get_page_versions",
        short_description="Get version history of a Confluence page",
        description=(
            "Get the version history of a Confluence page. Use when the user wants to see "
            "page revision history or past versions. For reading the current page content "
            "use get_page_content."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The page ID", required=True),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="read")],
    )
    async def get_page_versions(self, page_id: str) -> tuple[bool, str]:
        """Get version history of a page.

        Args:
            page_id: The ID of the page

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            response = await self.client.get_page_versions(id=page_id_int)
            return self._handle_response(response, "Page versions fetched successfully")

        except Exception as e:
            logger.error(f"Error getting page versions: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/confluence/comment_on_page",
        short_description="Add a comment to a Confluence page",
        description=(
            "Add a comment to a Confluence page. The comment_text parameter accepts plain text — "
            "it will be automatically formatted with HTML escaping and proper structure for Confluence. "
            "Optionally reply to an existing comment by providing parent_comment_id.\n"
            "\n"
            "Use when the user wants to comment on a page. Do not use for creating pages "
            "(use create_page) or reading pages (use get_page_content)."
        ),
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="Page ID", required=True),
            ToolParameter(name="comment_text", type=ParameterType.STRING, description="Comment text/content", required=True),
            ToolParameter(name="parent_comment_id", type=ParameterType.STRING, description="Parent comment ID if replying to a comment (optional)", required=False),
        ],
        tags=[Tag(key="category", value="knowledge_management"), Tag(key="type", value="write")],
    )
    async def comment_on_page(
        self,
        page_id: str,
        comment_text: str,
        parent_comment_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Add a comment to a Confluence page.

        Args:
            page_id: The ID of the page
            comment_text: The comment text/content
            parent_comment_id: Optional parent comment ID if replying to a comment

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            # ✅ FIX: Properly format comment text with HTML escaping and storage format
            # Escape HTML special characters
            escaped_text = html.escape(comment_text)

            # Convert newlines to <br/> tags
            escaped_text = escaped_text.replace('\n', '<br/>')

            # Wrap in paragraph tags
            html_content = f"<p>{escaped_text}</p>"

            # ✅ FIX: Confluence API v2 expects body in storage format structure
            # The body_body parameter should be a dict/object, not a string
            # Format: {"storage": {"value": "<p>text</p>", "representation": "storage"}}
            comment_body = {
                "storage": {
                    "value": html_content,
                    "representation": "storage"
                }
            }

            response = await self.client.create_footer_comment(
                pageId=str(page_id_int),
                body_body=comment_body,  # Pass as dict, not string
                parentCommentId=parent_comment_id
            )

            return self._handle_response(response, "Comment added successfully")

        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False, json.dumps({"error": str(e)})
