import asyncio
import json
import logging
import threading
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.confluence.confluence import ConfluenceDataSource

logger = logging.getLogger(__name__)


class Confluence:
    """Confluence tool exposed to the agents using ConfluenceDataSource"""
    def __init__(self, client: object) -> None:
        """Initialize the Confluence tool
        Args:
            client: Confluence client
        Returns:
            None
        """
        self.client = ConfluenceDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(target=self._start_background_loop, daemon=True)
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro):
        """Run a coroutine safely from sync or async contexts via a dedicated loop."""
        # Always submit to background loop; safe in both sync and async callers
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def _resolve_space_id(self, space_identifier: str) -> str:
        """Helper method to resolve space key to space ID if needed"""
        try:
            # If it's already numeric, return as is
            int(space_identifier)
            return space_identifier
        except ValueError:
            # It's a space key, try to resolve it
            try:
                response = self._run_async(self.client.get_spaces())
                if response.status == 200:
                    spaces = response.json()
                    for space in spaces.get('results', []):
                        if space.get('key') == space_identifier:
                            return str(space.get('id', space_identifier))
                # If not found, return original identifier
                return space_identifier
            except Exception:
                # If resolution fails, return original identifier
                return space_identifier


    @tool(
        app_name="confluence",
        tool_name="create_page",
        description="Create a page in Confluence",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="The ID of the space to create the page in"),
            ToolParameter(name="page_title", type=ParameterType.STRING, description="The title of the page to create"),
            ToolParameter(name="page_content", type=ParameterType.STRING, description="The content of the page to create"),
        ],
        returns="A message indicating whether the page was created successfully"
    )
    def create_page(self, space_id: str, page_title: str, page_content: str) -> Tuple[bool, str]:
        try:
            # Resolve space key to space ID if needed
            resolved_space_id = self._resolve_space_id(space_id)

            # Use ConfluenceDataSource method (create_page)
            response = self._run_async(self.client.create_page(
                body={
                    "title": page_title,
                    "spaceId": resolved_space_id,
                    "body": {
                        "storage": {
                            "value": page_content,
                            "representation": "storage"
                        }
                    }
                }
            ))
            if response.status in [200, 201]:
                page_data = response.json()
                return True, json.dumps({"message": "Page created successfully", "page": page_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                logger.error(f"Error creating page: HTTP {response.status} - {error_text}")
                return False, json.dumps({"message": f"Error creating page: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error creating page: {e}")
            return False, json.dumps({"message": f"Error creating page: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_page_content",
        description="Get the content of a page in Confluence",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page to get the content of"),
        ]
    )
    def get_page_content(self, page_id: str) -> Tuple[bool, str]:
        try:
            # Use ConfluenceDataSource method
            response = self._run_async(self.client.get_page_by_id(
                id=int(page_id),
                body_format={"storage": {}}
            ))
            if response.status == 200:
                content_data = response.json()
                return True, json.dumps({"message": "Page content fetched successfully", "content": content_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting page content: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting page content: {e}")
            return False, json.dumps({"message": f"Error getting page content: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_pages",
        description="Get the pages in a space in Confluence",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="The ID of the space to get the pages from"),
        ]
    )
    def get_pages(self, space_id: str) -> Tuple[bool, str]:
        try:
            # Resolve space key to space ID if needed
            resolved_space_id = self._resolve_space_id(space_id)

            # Use ConfluenceDataSource method - space_id should be string, not int
            response = self._run_async(self.client.get_pages_in_space(
                id=resolved_space_id
            ))
            if response.status == 200:
                pages_data = response.json()
                return True, json.dumps({"message": "Pages fetched successfully", "pages": pages_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting pages: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting pages: {e}")
            return False, json.dumps({"message": f"Error getting pages: {e}"})

    @tool(
        app_name="confluence",
        tool_name="invite_email",
        description="Invite an email to Confluence",
        parameters=[
            ToolParameter(name="email", type=ParameterType.STRING, description="The email to invite"),
        ]
    )
    def invite_email(self, email: str) -> Tuple[bool, str]:
        try:
            # Use ConfluenceDataSource method
            response = self._run_async(self.client.invite_by_email(
                body={"email": email}
            ))
            if response.status in [200, 201]:
                result_data = response.json()
                return True, json.dumps({"message": "Email invited successfully", "result": result_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error inviting email: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error inviting email: {e}")
            return False, json.dumps({"message": f"Error inviting email: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_spaces_with_permissions",
        description="Get the spaces with permissions in Confluence",
        parameters=[]
    )
    def get_spaces_with_permissions(self) -> Tuple[bool, str]:
        try:
            # List spaces (basic details)
            response = self._run_async(self.client.get_spaces())
            if response.status == 200:
                spaces_data = response.json()
                return True, json.dumps({"message": "Spaces fetched successfully", "spaces": spaces_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting spaces: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting spaces with permissions: {e}")
            return False, json.dumps({"message": f"Error getting spaces with permissions: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_page",
        description="Get the details of a page in Confluence",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page to get the details of"),
        ]
    )
    def get_page(self, page_id: str) -> Tuple[bool, str]:
        try:
            # Use ConfluenceDataSource method
            response = self._run_async(self.client.get_page_by_id(
                id=int(page_id),
                body_format={"storage": {}}
            ))
            if response.status == 200:
                page_data = response.json()
                return True, json.dumps({"message": "Page fetched successfully", "page": page_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting page: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"message": f"Error getting page: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_space",
        description="Get details of a Confluence space by ID",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="The ID of the space")
        ]
    )
    def get_space(self, space_id: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_space_by_id(id=int(space_id)))
            if response.status == 200:
                space_data = response.json()
                return True, json.dumps({"message": "Space fetched successfully", "space": space_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting space: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting space: {e}")
            return False, json.dumps({"message": f"Error getting space: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_page_versions",
        description="Get versions of a Confluence page",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page")
        ]
    )
    def get_page_versions(self, page_id: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_page_versions(id=int(page_id)))
            if response.status == 200:
                versions_data = response.json()
                return True, json.dumps({"message": "Page versions fetched successfully", "versions": versions_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting page versions: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting page versions: {e}")
            return False, json.dumps({"message": f"Error getting page versions: {e}"})

    @tool(
        app_name="confluence",
        tool_name="update_page_title",
        description="Update the title of a Confluence page",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the page"),
            ToolParameter(name="new_title", type=ParameterType.STRING, description="The new title for the page"),
        ]
    )
    def update_page_title(self, page_id: str, new_title: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.update_page_title(
                id=int(page_id),
                body={"title": new_title}
            ))
            if response.status in [200, 204]:
                result_data = response.json() if response.status == 200 else {}
                return True, json.dumps({"message": "Page title updated successfully", "result": result_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error updating page title: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error updating page title: {e}")
            return False, json.dumps({"message": f"Error updating page title: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_child_pages",
        description="Get child pages for a Confluence page",
        parameters=[
            ToolParameter(name="page_id", type=ParameterType.STRING, description="The ID of the parent page"),
        ]
    )
    def get_child_pages(self, page_id: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_child_pages(id=int(page_id)))
            if response.status == 200:
                children_data = response.json()
                return True, json.dumps({"message": "Child pages fetched successfully", "children": children_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error getting child pages: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error getting child pages: {e}")
            return False, json.dumps({"message": f"Error getting child pages: {e}"})

    @tool(
        app_name="confluence",
        tool_name="search_pages",
        description="Search pages by title, optionally within a space",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="Page title to search for"),
            ToolParameter(name="space_id", type=ParameterType.STRING, description="Optional space ID to limit search", required=False),
        ]
    )
    def search_pages(self, title: str, space_id: Optional[str] = None) -> Tuple[bool, str]:
        try:
            kwargs = {"title": title}
            if space_id:
                kwargs["space_id"] = [space_id]
            response = self._run_async(self.client.get_pages(**kwargs))
            if response.status == 200:
                pages_data = response.json()
                return True, json.dumps({"message": "Pages search successful", "pages": pages_data})
            else:
                error_text = response.text() if hasattr(response, 'text') and callable(response.text) else str(response.text)
                return False, json.dumps({"message": f"Error searching pages: HTTP {response.status} - {error_text}"})
        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"message": f"Error searching pages: {e}"})
