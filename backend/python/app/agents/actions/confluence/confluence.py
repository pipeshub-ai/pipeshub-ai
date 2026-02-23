import html
import json
import logging
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.sources.atlassian.core.oauth import AtlassianScope
from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.confluence.confluence import ConfluenceDataSource

logger = logging.getLogger(__name__)

# Pydantic schemas for Confluence tools
class CreatePageInput(BaseModel):
    """Schema for creating Confluence pages"""
    space_id: str = Field(description="Space ID or key")
    page_title: str = Field(description="Page title")
    page_content: str = Field(description="Page content in storage format")

class GetPageContentInput(BaseModel):
    """Schema for getting page content"""
    page_id: str = Field(description="Page ID")

class GetPagesInSpaceInput(BaseModel):
    """Schema for getting pages in space"""
    space_id: str = Field(description="Space ID or key")

class UpdatePageTitleInput(BaseModel):
    """Schema for updating page title"""
    page_id: str = Field(description="Page ID")
    new_title: str = Field(description="New title")

class SearchPagesInput(BaseModel):
    """Schema for searching pages"""
    title: str = Field(description="Page title to search")
    space_id: Optional[str] = Field(default=None, description="Space ID to limit search")

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

class GetPageVersionsInput(BaseModel):
    """Schema for getting page versions"""
    page_id: str = Field(description="The page ID")

class SearchContentInput(BaseModel):
    """Schema for full-text content search"""
    query: str = Field(description="Free-text search query — searches across page/blogpost titles, body content, comments, and labels (mirrors the Confluence platform search bar)")
    space_id: Optional[str] = Field(default=None, description="Optional space key or ID to restrict search to one space")
    content_types: Optional[list] = Field(default=None, description="Content types to include: 'page', 'blogpost', or both. Defaults to both when omitted.")
    limit: Optional[int] = Field(default=25, description="Maximum number of results to return (default 25)")

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
                CommonFields.client_secret("Atlassian Developer Console")
            ],
            icon_path="/assets/icons/connectors/confluence.svg",
            app_group="Documentation",
            app_description="Confluence OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/confluence.svg"))\
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
    ) -> Tuple[bool, str]:
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
            if hasattr(client_obj, 'get_token'):
                token = client_obj.get_token()
                if token:
                    resources = await ConfluenceClient.get_accessible_resources(token)
                    if resources and len(resources) > 0:
                        # Extract base URL from resource URL
                        resource_url = resources[0].url
                        # Resource URL is like 'https://example.atlassian.net'
                        self._site_url = resource_url.rstrip('/')
                        return self._site_url
        except Exception as e:
            logger.warning(f"Could not get site URL: {e}")

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

    @tool(
        app_name="confluence",
        tool_name="create_page",
        description="Create a page in Confluence",
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
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a Confluence page",
            "Add a new page to Confluence",
            "Create documentation page"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def create_page(
        self,
        space_id: str,
        page_title: str,
        page_content: str
    ) -> Tuple[bool, str]:
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
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me the Confluence page",
            "Get page content from Confluence",
            "Read the documentation page"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_page_content(self, page_id: str) -> Tuple[bool, str]:
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
        args_schema=GetPagesInSpaceInput,  # NEW: Pydantic schema
        returns="JSON with list of pages",
        when_to_use=[
            "User wants to list all pages in a space",
            "User mentions 'Confluence' + wants space pages",
            "User asks for pages in a space"
        ],
        when_not_to_use=[
            "User wants to search pages (use search_pages)",
            "User wants specific page (use get_page_content)",
            "User wants info ABOUT Confluence (use retrieval)",
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List pages in space X",
            "Show all pages in Confluence space",
            "Get pages from space"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_pages_in_space(self, space_id: str) -> Tuple[bool, str]:
        """Get all pages in a space.

        Args:
            space_id: The ID or key of the space

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id = await self._resolve_space_id(space_id)
            response = await self.client.get_pages_in_space(id=resolved_space_id)
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
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Rename Confluence page",
            "Update page title",
            "Change page name"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def update_page_title(self, page_id: str, new_title: str) -> Tuple[bool, str]:
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
        returns="JSON with list of child pages",
        when_to_use=[
            "User wants to see child/sub-pages",
            "User mentions 'Confluence' + wants child pages",
            "User asks for sub-pages of a page"
        ],
        when_not_to_use=[
            "User wants all pages in space (use get_pages_in_space)",
            "User wants to read page (use get_page_content)",
            "User wants info ABOUT Confluence (use retrieval)",
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get child pages of page X",
            "Show sub-pages",
            "What pages are under this page?"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_child_pages(self, page_id: str) -> Tuple[bool, str]:
        """Get child pages of a page.

        Args:
            page_id: The ID of the parent page

        Returns:
            Tuple of (success, json_response)
        """
        try:
            # Convert page_id to int with proper error handling
            try:
                page_id_int = int(page_id)
            except ValueError:
                return False, json.dumps({"error": f"Invalid page_id format: '{page_id}' is not a valid integer"})

            response = await self.client.get_child_pages(id=page_id_int)
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
        args_schema=SearchPagesInput,  # NEW: Pydantic schema
        returns="JSON with search results",
        when_to_use=[
            "User wants to find pages by title",
            "User mentions 'Confluence' + wants to search",
            "User asks to find a page"
        ],
        when_not_to_use=[
            "User wants to create page (use create_page)",
            "User wants all pages (use get_pages_in_space)",
            "User wants info ABOUT Confluence (use retrieval)",
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for page 'Project Plan'",
            "Find Confluence page by title",
            "Search pages in Confluence"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def search_pages(
        self,
        title: str,
        space_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Search for pages by title.

        Args:
            title: Page title to search for
            space_id: Optional space ID to limit search

        Returns:
            Tuple of (success, json_response)
        """
        try:
            kwargs: Dict[str, object] = {"title": title}
            if space_id:
                kwargs["space_id"] = [space_id]

            response = await self.client.get_pages(**kwargs)
            result = self._handle_response(response, "Search completed successfully")

            # Add web URLs if successful
            if result[0] and response.status == HttpStatusCode.SUCCESS.value:
                try:
                    response.json()
                    site_url = await self._get_site_url()
                    if site_url:
                        result_data = json.loads(result[1])
                        if "data" in result_data:
                            pages = result_data["data"]
                            if isinstance(pages, dict) and "results" in pages:
                                for page in pages["results"]:
                                    page_id = page.get("id")
                                    space_id = page.get("spaceId")
                                    if page_id and space_id:
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

                                        page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                            elif isinstance(pages, list):
                                for page in pages:
                                    page_id = page.get("id")
                                    space_id = page.get("spaceId")
                                    if page_id and space_id:
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

                                        page["url"] = f"{site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                        result = (result[0], json.dumps(result_data))
                except Exception as e:
                    logger.debug(f"Could not add URLs to response: {e}")

            return result

        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="search_content",
        description="Full-text search across Confluence pages and blog posts (mirrors the Confluence platform search bar)",
        args_schema=SearchContentInput,
        returns="JSON with ranked search results including titles, excerpts, and URLs",
        when_to_use=[
            "User wants to find content by topic, keyword, or meaning (not just by title)",
            "User searches for Confluence pages or blog posts matching a theme or concept",
            "User asks 'find pages about X' or 'search Confluence for Y'",
            "Title-only search (search_pages) gives poor results — use this for richer search",
        ],
        when_not_to_use=[
            "User wants to create/update a page (use create_page / update_page)",
            "User already has a page ID and wants its content (use get_page_content)",
            "User wants to list all pages in a space (use get_pages_in_space)",
            "No Confluence mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Confluence pages about deployment",
            "Search for anything related to onboarding",
            "Find documentation about the payment service",
            "Search Confluence for API guidelines",
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
        query: str,
        space_id: Optional[str] = None,
        content_types: Optional[list] = None,
        limit: Optional[int] = 25,
    ) -> Tuple[bool, str]:
        """Full-text search across Confluence using the platform search engine.

        Searches page/blogpost titles, body content, comments, and labels via CQL ``text ~``.
        This is identical to what the Confluence search bar does — far more powerful than
        title-only search.

        Args:
            query: Free-text search query
            space_id: Optional space key or numeric ID to restrict the search
            content_types: List of types to include: "page", "blogpost" (default: both)
            limit: Max results to return (default 25)

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id: Optional[str] = None
            if space_id:
                resolved_space_id = await self._resolve_space_id(space_id)

            response = await self.client.search_full_text(
                query=query,
                space_id=resolved_space_id,
                content_types=content_types,
                limit=limit or 25,
            )

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

            # Normalise results into a clean, LLM-friendly structure
            # and inject web URLs using the site base URL
            site_url = await self._get_site_url()
            cleaned: list = []
            for item in results:
                content = item.get("content") or {}
                content_id   = content.get("id", "")
                content_type = content.get("type", "page")
                title        = content.get("title", "")
                excerpt      = item.get("excerpt", "")
                space_info   = content.get("space") or {}
                space_key    = space_info.get("key", "")
                space_name   = space_info.get("name", "")

                # Prefer the webui link from the API response; fall back to constructing it
                webui = ""
                links = content.get("_links") or {}
                if links.get("webui"):
                    if site_url:
                        webui = site_url + links["webui"]
                    else:
                        webui = links["webui"]
                elif site_url and content_id and space_key:
                    webui = f"{site_url}/wiki/spaces/{space_key}/pages/{content_id}"

                entry: Dict[str, Any] = {
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

                cleaned.append(entry)

            return True, json.dumps({
                "message": "Search completed successfully",
                "query": query,
                "total_results": total,
                "returned": len(cleaned),
                "results": cleaned,
            })

        except Exception as e:
            logger.error(f"Error in search_content: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_spaces",
        description="Get all spaces with permissions in Confluence",
        # No args_schema needed (no parameters)
        returns="JSON with list of spaces",
        when_to_use=[
            "User wants to list all Confluence spaces",
            "User mentions 'Confluence' + wants spaces",
            "User asks for available spaces"
        ],
        when_not_to_use=[
            "User wants specific space (use get_space)",
            "User wants pages (use get_pages_in_space)",
            "User wants info ABOUT Confluence (use retrieval)",
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Confluence spaces",
            "Show me available spaces",
            "What spaces are in Confluence?"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_spaces(self) -> Tuple[bool, str]:
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
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get space X details",
            "Show me Confluence space info",
            "What is space X?"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_space(self, space_id: str) -> Tuple[bool, str]:
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
            "No Confluence mention"
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
    ) -> Tuple[bool, str]:
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
            body: Dict[str, Any] = {
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
            "No Confluence mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get version history of page",
            "Show page versions",
            "What versions does this page have?"
        ],
        category=ToolCategory.DOCUMENTATION
    )
    async def get_page_versions(self, page_id: str) -> Tuple[bool, str]:
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
            "No Confluence mention"
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
    ) -> Tuple[bool, str]:
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
