import asyncio
import json
import logging
import threading
from typing import Coroutine, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.linkedin.linkedin import LinkedInClient
from app.sources.external.linkedin.linkedin import LinkedInDataSource

logger = logging.getLogger(__name__)


class LinkedIn:
    """LinkedIn tools exposed to agents using LinkedInDataSource.

    This provides CRUD operations for LinkedIn posts and search functionality,
    following the same pattern as Confluence and Google Meet tools.
    """

    def __init__(self, client: LinkedInClient) -> None:
        """Initialize the LinkedIn tool with a data source wrapper.

        Args:
            client: An initialized `LinkedInClient` instance
        """
        self.client = LinkedInDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        """Run a coroutine safely from sync context via a dedicated loop."""
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
            logger.warning(f"LinkedIn shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle LinkedIn response and return standardized format."""
        try:
            # LinkedIn SDK returns native response objects
            if hasattr(response, 'entity') or hasattr(response, 'elements'):
                return True, json.dumps({
                    "message": success_message,
                    "data": getattr(response, 'entity', getattr(response, 'elements', {}))
                })
            elif hasattr(response, 'entity_id'):
                return True, json.dumps({
                    "message": success_message,
                    "data": {"entity_id": response.entity_id}
                })
            else:
                return True, json.dumps({
                    "message": success_message,
                    "data": response
                })
        except Exception as e:
            logger.error(f"Error handling response: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="get_userinfo",
        description="Get current user information using OpenID Connect",
        parameters=[],
        returns="JSON with user information"
    )
    def get_userinfo(self) -> Tuple[bool, str]:
        """Get current user information using OpenID Connect."""
        try:
            response = self.client.get_userinfo()
            return self._handle_response(response, "User information retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="create_post",
        description="Create a new post on LinkedIn",
        parameters=[
            ToolParameter(
                name="author",
                type=ParameterType.STRING,
                description="Author URN (e.g., 'urn:li:person:AbCdEfG')"
            ),
            ToolParameter(
                name="commentary",
                type=ParameterType.STRING,
                description="Post text content"
            ),
            ToolParameter(
                name="visibility",
                type=ParameterType.STRING,
                description="Post visibility ('PUBLIC', 'CONNECTIONS', etc.)",
                required=False
            ),
            ToolParameter(
                name="lifecycle_state",
                type=ParameterType.STRING,
                description="Post lifecycle state ('PUBLISHED' or 'DRAFT')",
                required=False
            )
        ],
        returns="JSON with post creation result"
    )
    def create_post(
        self,
        author: str,
        commentary: str,
        visibility: Optional[str] = "PUBLIC",
        lifecycle_state: Optional[str] = "PUBLISHED"
    ) -> Tuple[bool, str]:
        """Create a new post on LinkedIn."""
        try:
            response = self.client.create_post(
                author=author,
                commentary=commentary,
                visibility=visibility,
                lifecycle_state=lifecycle_state
            )
            return self._handle_response(response, "Post created successfully")
        except Exception as e:
            logger.error(f"Error creating post: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="get_post",
        description="Get a post by ID from LinkedIn",
        parameters=[
            ToolParameter(
                name="post_id",
                type=ParameterType.STRING,
                description="Post URN or ID"
            )
        ],
        returns="JSON with post data"
    )
    def get_post(self, post_id: str) -> Tuple[bool, str]:
        """Get a post by ID from LinkedIn."""
        try:
            response = self.client.get_post(post_id=post_id)
            return self._handle_response(response, "Post retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting post: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="update_post",
        description="Update an existing post on LinkedIn",
        parameters=[
            ToolParameter(
                name="post_id",
                type=ParameterType.STRING,
                description="Post URN or ID"
            ),
            ToolParameter(
                name="patch_data",
                type=ParameterType.STRING,
                description="JSON object with patch operations (e.g., {'$set': {'commentary': 'New text'}})"
            )
        ],
        returns="JSON with post update result"
    )
    def update_post(
        self,
        post_id: str,
        patch_data: str
    ) -> Tuple[bool, str]:
        """Update an existing post on LinkedIn."""
        try:
            # Parse patch data
            try:
                patch_dict = json.loads(patch_data)
                if not isinstance(patch_dict, dict):
                    raise ValueError("patch_data must be a JSON object")
            except json.JSONDecodeError as exc:
                return False, json.dumps({"error": f"Invalid JSON for patch_data: {exc}"})

            response = self.client.update_post(
                post_id=post_id,
                patch_data=patch_dict
            )
            return self._handle_response(response, "Post updated successfully")
        except Exception as e:
            logger.error(f"Error updating post: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="delete_post",
        description="Delete a post from LinkedIn",
        parameters=[
            ToolParameter(
                name="post_id",
                type=ParameterType.STRING,
                description="Post URN or ID to delete"
            )
        ],
        returns="JSON with deletion result"
    )
    def delete_post(self, post_id: str) -> Tuple[bool, str]:
        """Delete a post from LinkedIn."""
        try:
            response = self.client.delete_post(post_id=post_id)
            return self._handle_response(response, "Post deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting post: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="linkedin",
        tool_name="search_people",
        description="Search for people on LinkedIn",
        parameters=[
            ToolParameter(
                name="keywords",
                type=ParameterType.STRING,
                description="Search keywords"
            ),
            ToolParameter(
                name="query_params",
                type=ParameterType.STRING,
                description="JSON object with additional search parameters (e.g., {'start': 0, 'count': 25})",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_people(
        self,
        keywords: str,
        query_params: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Search for people on LinkedIn."""
        try:
            # Parse query parameters if provided
            params_dict = None
            if query_params:
                try:
                    params_dict = json.loads(query_params)
                    if not isinstance(params_dict, dict):
                        raise ValueError("query_params must be a JSON object")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for query_params: {exc}"})

            response = self.client.search_people(
                keywords=keywords,
                query_params=params_dict
            )
            return self._handle_response(response, "People search completed successfully")
        except Exception as e:
            logger.error(f"Error searching people: {e}")
            return False, json.dumps({"error": str(e)})
