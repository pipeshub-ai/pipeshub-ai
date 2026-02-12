import asyncio
import json
import logging
import threading
from typing import Coroutine, Dict, Optional, Tuple

from pydantic import BaseModel, Field

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolsetBuilder,
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


# Register Confluence toolset
@ToolsetBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_description("Confluence integration for wiki pages, documentation, and knowledge management")\
    .with_category(ToolCategory.APP)\
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
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop"""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, HTTPResponse]) -> HTTPResponse:
        """Run a coroutine safely from sync context via a dedicated loop.

        Args:
            coro: Coroutine that returns HTTPResponse

        Returns:
            HTTPResponse from the executed coroutine
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"Confluence shutdown encountered an issue: {exc}")

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

    def _resolve_space_id(self, space_identifier: str) -> str:
        """Helper method to resolve space key to space ID if needed.

        Args:
            space_identifier: Space ID or space key

        Returns:
            Resolved space ID
        """
        try:
            # If it's already numeric, return as is
            int(space_identifier)
            return space_identifier
        except ValueError:
            # It's a space key, try to resolve it
            try:
                response = self._run_async(self.client.get_spaces())
                if response.status == HttpStatusCode.SUCCESS.value:
                    spaces = response.json()
                    for space in spaces.get('results', []):
                        if space.get('key') == space_identifier:
                            return str(space.get('id', space_identifier))
                return space_identifier
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Failed to resolve space key {space_identifier}: {e}")
                return space_identifier

    @tool(
        app_name="confluence",
        tool_name="create_page",
        description="Create a page in Confluence",
        args_schema=CreatePageInput,  # NEW: Pydantic schema
        returns="JSON with success status and page details"
    )
    def create_page(
        self,
        space_id: str,
        page_title: str,
        page_content: str
    ) -> Tuple[bool, str]:
        """Create a page in Confluence.

        Args:
            space_id: The ID or key of the space
            page_title: The title of the page
            page_content: The content of the page

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id = self._resolve_space_id(space_id)

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

            response = self._run_async(self.client.create_page(body=body))
            return self._handle_response(response, "Page created successfully")

        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_page_content",
        description="Get the content of a page in Confluence",
        args_schema=GetPageContentInput,  # NEW: Pydantic schema
        returns="JSON with page content and metadata"
    )
    def get_page_content(self, page_id: str) -> Tuple[bool, str]:
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

            response = self._run_async(
                self.client.get_page_by_id(
                    id=page_id_int,
                    body_format="storage"
                )
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
        returns="JSON with list of pages"
    )
    def get_pages_in_space(self, space_id: str) -> Tuple[bool, str]:
        """Get all pages in a space.

        Args:
            space_id: The ID or key of the space

        Returns:
            Tuple of (success, json_response)
        """
        try:
            resolved_space_id = self._resolve_space_id(space_id)
            response = self._run_async(
                self.client.get_pages_in_space(id=resolved_space_id)
            )
            return self._handle_response(response, "Pages fetched successfully")

        except Exception as e:
            logger.error(f"Error getting pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="update_page_title",
        description="Update the title of a Confluence page",
        args_schema=UpdatePageTitleInput,  # NEW: Pydantic schema
        returns="JSON with success status"
    )
    def update_page_title(self, page_id: str, new_title: str) -> Tuple[bool, str]:
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

            response = self._run_async(
                self.client.update_page_title(
                    id=page_id_int,
                    body={"title": new_title}
                )
            )
            return self._handle_response(response, "Page title updated successfully")

        except Exception as e:
            logger.error(f"Error updating page title: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_child_pages",
        description="Get child pages of a Confluence page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the parent page"
            ),
        ],
        returns="JSON with list of child pages"
    )
    def get_child_pages(self, page_id: str) -> Tuple[bool, str]:
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

            response = self._run_async(
                self.client.get_child_pages(id=page_id_int)
            )
            return self._handle_response(response, "Child pages fetched successfully")

        except Exception as e:
            logger.error(f"Error getting child pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="search_pages",
        description="Search pages by title in Confluence",
        args_schema=SearchPagesInput,  # NEW: Pydantic schema
        returns="JSON with search results"
    )
    def search_pages(
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

            response = self._run_async(
                self.client.get_pages(**kwargs)
            )
            return self._handle_response(response, "Search completed successfully")

        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_spaces",
        description="Get all spaces with permissions in Confluence",
        # No args_schema needed (no parameters)
        returns="JSON with list of spaces"
    )
    def get_spaces(self) -> Tuple[bool, str]:
        """Get all spaces accessible to the user.

        Returns:
            Tuple of (success, json_response)
        """
        try:
            response = self._run_async(self.client.get_spaces())
            return self._handle_response(response, "Spaces fetched successfully")

        except Exception as e:
            logger.error(f"Error getting spaces: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_space",
        description="Get details of a Confluence space by ID",
        args_schema=GetSpaceInput,  # NEW: Pydantic schema
        returns="JSON with space details"
    )
    def get_space(self, space_id: str) -> Tuple[bool, str]:
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

            response = self._run_async(
                self.client.get_space_by_id(id=space_id_int)
            )
            return self._handle_response(response, "Space fetched successfully")

        except Exception as e:
            logger.error(f"Error getting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="confluence",
        tool_name="get_page_versions",
        description="Get versions of a Confluence page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page"
            )
        ],
        returns="JSON with page versions"
    )
    def get_page_versions(self, page_id: str) -> Tuple[bool, str]:
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

            response = self._run_async(
                self.client.get_page_versions(id=page_id_int)
            )
            return self._handle_response(response, "Page versions fetched successfully")

        except Exception as e:
            logger.error(f"Error getting page versions: {e}")
            return False, json.dumps({"error": str(e)})

