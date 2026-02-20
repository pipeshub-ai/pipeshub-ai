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
            return self._handle_response(response, "Page created successfully")

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
            return self._handle_response(response, "Page content fetched successfully")

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
            return self._handle_response(response, "Pages fetched successfully")

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
            return self._handle_response(response, "Child pages fetched successfully")

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
            return self._handle_response(response, "Search completed successfully")

        except Exception as e:
            logger.error(f"Error searching pages: {e}")
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
            return self._handle_response(response, "Spaces fetched successfully")

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
            return self._handle_response(response, "Space fetched successfully")

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
            return self._handle_response(response, "Page updated successfully")

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
            import html

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
