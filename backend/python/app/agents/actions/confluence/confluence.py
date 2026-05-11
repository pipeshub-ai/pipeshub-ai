import html
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
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
                AuthField(
                    name="baseUrl",
                    display_name="Atlassian site URL",
                    placeholder="https://yourcompany.atlassian.net",
                    description="Atlassian site URL the Confluence agent should work with.",
                    field_type="URL",
                    required=True,
                    max_length=2000,
                    is_secret=False,
                ),
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

    def __init__(self, client: ConfluenceClient) -> None:
        """Initialize the Confluence tool

        Args:
            client: Confluence client object
        """
        self.client = ConfluenceDataSource(client)
        self._site_url = None  # Cache for site URL

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
        app_name="confluence",
        tool_name="create_page",
        description="Create a page in Confluence",
        llm_description="Create a page in Confluence. Requires space_id (numeric ID or key), page_title, and page_content (HTML storage format). Call confluence.get_spaces first if the space is not yet resolved.",
        args_schema=CreatePageInput,  # NEW: Pydantic schema
        returns="JSON with success status and page details",
        when_to_use=[
            "User wants to create a Confluence page",
            "User mentions 'Confluence' + wants to create page",
            "User asks to create documentation/page"
        ],
        when_not_to_use=[
            "User wants to search pages (use search_pages)",
            "User wants to read page (use get_page_content)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a Confluence page",
            "Add a new page to Confluence",
            "Create documentation page",
            "Create a page in X space",
            "Create a wiki page about X and add the Jira ticket link"
        ],
        category=ToolCategory.DOCUMENTATION
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

    @tool(
        app_name="confluence",
        tool_name="get_page_content",
        description="Get the content of a page in Confluence",
        args_schema=GetPageContentInput,  # NEW: Pydantic schema
        returns="JSON with page content and metadata",
        when_to_use=[
            "User wants to read/view a Confluence page",
            "User mentions 'Confluence' + wants page content",
            "User asks to get/show a specific page"
        ],
        when_not_to_use=[
            "User wants to create page (use create_page)",
            "User wants to search pages (use search_pages)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me the Confluence page",
            "Get page content from Confluence",
            "Read the documentation page"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_page_content(self, page_id: str) -> tuple[bool, str]:
        """Get the content of a page in Confluence.

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

            response = await self.client.get_page_by_id(
                id=page_id_int,
                body_format="storage"
            )
            result = self._handle_response(response, "Page content fetched successfully")

            # Add web URL if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    data = response.json()
                    page_id_from_data = data.get("id")
                    space_id = data.get("spaceId")
                    if page_id_from_data and space_id:
                        site_url = await self._get_site_url()
                        if site_url:
                            # Get space key from space ID
                            space_key = space_id
                            try:
                                int(space_id)  # Check if it's numeric
                                spaces_response = await self.client.get_spaces()
                                if spaces_response.status == HttpStatusCode.SUCCESS.value:
                                    spaces_data = spaces_response.json()
                                    for space in spaces_data.get("results", []):
                                        if str(space.get("id")) == str(space_id):
                                            space_key = space.get("key", space_id)
                                            break
                            except ValueError:
                                pass  # Already a key

                            web_url = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id_from_data}"
                            result_data = json.loads(result[1])
                            if "data" in result_data and isinstance(result_data["data"], dict):
                                result_data["data"]["url"] = web_url
                            result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URL to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error getting page content: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_pages_in_space",
        description="Get all pages in a Confluence space",
        args_schema=GetPagesInSpaceInput,
        returns="JSON with the list of pages, each with id, title, url",
        when_to_use=[
            "User wants to enumerate pages in a space without an authorship/date/label filter",
            "User asks for 'recently updated pages in <space>' — set sort_by='-modified-date'",
            "User asks for 'recently created pages in <space>' — set sort_by='-created-date'",
            "User asks for an alphabetical listing of pages in a space — set sort_by='title'",
        ],
        when_not_to_use=[
            "User wants pages they / someone else updated / created (use search_content with `space_id` + the corresponding contributor/creator slot — this v2 endpoint has no author filter)",
            "User wants pages with a specific topic / keyword (use search_content)",
            "User wants pages with a specific label (use search_content with `labels`)",
            "User wants a specific page by name (use search_pages)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List pages in space X",
            "Show all pages in Confluence space HR",
            "Recently updated pages in space X",
            "Newest pages in space ENG",
            "Pages in space X alphabetically",
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="update_page_title",
        description="Update the title of a Confluence page",
        args_schema=UpdatePageTitleInput,  # NEW: Pydantic schema
        returns="JSON with success status",
        when_to_use=[
            "User wants to rename/update page title",
            "User mentions 'Confluence' + wants to change title",
            "User asks to rename page"
        ],
        when_not_to_use=[
            "User wants to create page (use create_page)",
            "User wants to read page (use get_page_content)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Rename Confluence page",
            "Update page title",
            "Change page name"
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="get_child_pages",
        description="Get child pages of a Confluence page",
        args_schema=GetChildPagesInput,
        returns="JSON with the list of child pages, each with id, title, url",
        when_to_use=[
            "User wants the direct sub-pages of a known page",
            "User asks 'what pages are under <page>' (without an authorship filter)",
            "User asks for child pages sorted by recency or alphabetically — set sort_by",
        ],
        when_not_to_use=[
            "User wants child pages filtered by author/date/label — that needs CQL, which this v2 endpoint can't do (use search_content)",
            "User wants ALL pages in a space (use get_pages_in_space)",
            "User wants to read a page's content (use get_page_content)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get child pages of page 12345",
            "Show sub-pages of <page>",
            "Recently updated child pages of <page>",
            "Newest sub-pages of <page>",
            "Child pages alphabetically",
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="search_pages",
        description="Search pages by title in Confluence",
        args_schema=SearchPagesInput,
        returns="JSON with ranked pages (best title match first); a 'note' or 'warning' field flags ambiguity when multiple match",
        when_to_use=[
            "User wants to FIND a specific named page (resolve a name to a page_id)",
            "User asks 'find page X', 'search for page named <name>', 'page called <name>'",
            "User asks for a named page constrained by author/date/label — slot the constraints in (e.g. 'find the FAQ page I created last quarter')",
        ],
        when_not_to_use=[
            "User has a topic / keyword without a page name — use search_content (it ranks better for free-text body queries)",
            "User asks for pages with no name in mind ('what did I update?') — use search_content with the authorship slot",
            "User wants to create page (use create_page)",
            "User wants ALL pages in a space (use get_pages_in_space)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Confluence page named 'Project Plan'",
            "Search for the deployment runbook page",
            "Find the FAQ page I created last quarter",
            "Locate the onboarding page tagged 'hr'",
            "Page named 'API Design' in the SD space",
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="search_content",
        description="Search Confluence pages and blog posts — full-text, by author, by date, by labels, or any combination",
        args_schema=SearchContentInput,
        returns="JSON with ranked search results including titles, excerpts, space, labels, last-modified, and URLs",
        when_to_use=[
            "User wants to find content by topic, keyword, or meaning",
            "User asks for pages they (or someone else) updated / created / contributed to / were mentioned in",
            "User asks for date-bounded results ('last week', 'since May', 'before Q2', 'today')",
            "User asks for a specific ordering ('most recent', 'newest first', 'alphabetical')",
            "User asks for pages with a specific label",
            "User combines any of the above ('pages I updated about deployment', 'pages tagged onboarding I created last quarter')",
            "Title-only search (search_pages) is too narrow",
        ],
        when_not_to_use=[
            "User wants to create / update a page (use create_page / update_page)",
            "User already has a page ID and wants its content (use get_page_content)",
            "User wants to list ALL pages in a space without any filter (use get_pages_in_space)",
            "User wants to find a USER by name (use search_users, then feed the accountId back here)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Confluence pages about deployment",
            "Search Confluence for API guidelines",
            "What pages did I update?",
            "Pages I created last week",
            "Pages mentioning me about API design",
            "What did John Doe update in the last quarter?",
            "Most recent pages tagged with onboarding",
            "Pages I edited in the X space this month",
        ],
        category=ToolCategory.DOCUMENTATION,
        llm_description=(
            "Full-text search across Confluence pages and blog posts using the platform search engine. "
            "Unlike search_pages (title-only), this searches the full body content, comments, and labels — "
            "exactly like the Confluence search bar. Use this whenever you need to find content by topic or keyword."
        )
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
        app_name="confluence",
        tool_name="search_users",
        description="Search Confluence users by name or email",
        args_schema=SearchUsersInput,
        returns="JSON list of matching users (accountId, displayName, email when available, accountStatus, rank); ranked by closeness; with disambiguation flags when multiple users match",
        when_to_use=[
            "User names another person and you need their accountId for an authorship-filtered Confluence search",
            "User asks 'who is X in Confluence' / 'find user X'",
            "User asks 'what did <Name> update / create / contribute to' — call this FIRST, then search_content with the resolved accountId",
        ],
        when_not_to_use=[
            "User asks about themselves — pass `currentUser()` directly to search_content, no lookup needed",
            "User wants pages, not users (use search_content)",
            "No user is named in the query",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Confluence user John Doe",
            "What's the accountId for vishwjeet",
            "Get user info for someone@company.com",
            "Look up user Megan in Confluence",
        ],
        category=ToolCategory.DOCUMENTATION,
        llm_description=(
            "Search Confluence users by display name OR email — handles whichever the user gives, "
            "you don't need to detect the format. Returns each match's accountId, which is what you "
            "wrap in double quotes (`'\"<accountId>\"'`) and pass as search_content's `contributor`, "
            "`creator`, `mention`, or `last_modifier` slot when filtering another user's activity.\n"
            "DO NOT call this for self-queries — pass the literal `currentUser()` to search_content "
            "directly; no lookup needed.\n"
            "When 2+ users match and none is an exact name/email match, the response sets "
            "`disambiguation_required: true` and a `warning` field — stop and ask the user which "
            "person they meant before passing any accountId onward. When exactly one user matches "
            "or one is an exact match, proceed with the top result.\n"
            "Cloud privacy can suppress email matches; if 0 results come back for an email, ask the "
            "user for the display name instead."
        ),
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
                    "Confirm the spelling, try a shorter name fragment, or ask "
                    "the user for the full display name. (Atlassian Cloud "
                    "privacy settings can hide email matches.)"
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
        app_name="confluence",
        tool_name="get_spaces",
        description="Get all spaces with permissions in Confluence",
        llm_description="Get all spaces with permissions in Confluence. Also used to resolve space names/types (e.g., personal space) before creating pages.",
        # No args_schema needed (no parameters)
        returns="JSON with list of spaces including id, key, name, and type fields",
        when_to_use=[
            "User wants to list all Confluence spaces",
            "User mentions 'Confluence' + wants spaces",
            "User asks for available spaces",
            "Need to resolve 'my personal space' or any named space to get its ID before creating/updating a page",
            "User refers to a space by name and you need the space key or numeric ID"
        ],
        when_not_to_use=[
            "Space ID is already known from conversation history",
            "User wants pages (use get_pages_in_space)",
            "User wants info ABOUT Confluence (use retrieval)"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Confluence spaces",
            "Show me available spaces",
            "What spaces are in Confluence?",
            "Create a page in my personal space",
            "Create a page in the Engineering space"
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="get_space",
        description="Get details of a Confluence space by ID",
        args_schema=GetSpaceInput,  # NEW: Pydantic schema
        returns="JSON with space details",
        when_to_use=[
            "User wants details about a specific space",
            "User mentions 'Confluence' + wants space info",
            "User asks about a space"
        ],
        when_not_to_use=[
            "User wants all spaces (use get_spaces)",
            "User wants pages (use get_pages_in_space)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get space X details",
            "Show me Confluence space info",
            "What is space X?"
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="update_page",
        description="Update a Confluence page (title and/or content)",
        args_schema=UpdatePageInput,  # NEW: Pydantic schema
        returns="JSON with success status and updated page details",
        when_to_use=[
            "User wants to update/edit a Confluence page",
            "User mentions 'Confluence' + wants to modify page",
            "User asks to edit/update page content or title"
        ],
        when_not_to_use=[
            "User wants to create page (use create_page)",
            "User wants to read page (use get_page_content)",
            "User only wants to change title (use update_page_title)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update Confluence page content",
            "Edit a page in Confluence",
            "Modify page content",
            "Update page with new information"
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="get_page_versions",
        description="Get versions of a Confluence page",
        args_schema=GetPageVersionsInput,
        returns="JSON with page versions",
        when_to_use=[
            "User wants to see page version history",
            "User mentions 'Confluence' + wants versions",
            "User asks for page history"
        ],
        when_not_to_use=[
            "User wants page content (use get_page_content)",
            "User wants to create page (use create_page)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get version history of page",
            "Show page versions",
            "What versions does this page have?"
        ],
        category=ToolCategory.DOCUMENTATION
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
        app_name="confluence",
        tool_name="comment_on_page",
        description="Add a comment to a Confluence page",
        args_schema=CommentOnPageInput,
        returns="JSON with success status and comment details",
        when_to_use=[
            "User wants to add a comment to a Confluence page",
            "User mentions 'Confluence' + wants to comment",
            "User asks to comment on a page"
        ],
        when_not_to_use=[
            "User wants to create page (use create_page)",
            "User wants to read page (use get_page_content)",
            "User wants info ABOUT Confluence (use retrieval)",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add a comment to the Confluence page",
            "Comment on page X",
            "Leave a comment on this page"
        ],
        category=ToolCategory.DOCUMENTATION,
        llm_description="Add a comment to a Confluence page. The comment_text parameter accepts plain text - it will be automatically formatted with HTML escaping and proper structure for Confluence."
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
