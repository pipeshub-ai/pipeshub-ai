import json
import logging
from typing import Optional

try:
    from app.agents.client.confluence import Confluence  # type: ignore
except ImportError:
    raise ImportError("Confluence client not found. Please install the app.agents.client.confluence package.")

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

logger = logging.getLogger(__name__)


class Confluence:
    """Confluence tool exposed to the agents"""
    def __init__(
        self,
        client: Confluence,
        base_url: str
    ) -> None:
        """Initialize the Confluence tool
        Args:
            client: Confluence client
            base_url: Confluence base URL
        Returns:
            None
        """
        self.confluence = client
        self.base_url = base_url

    @tool(
        app_name="confluence",
        tool_name="create_page",
        parameters=[
            ToolParameter(
                name="space",
                type=ParameterType.STRING,
                description="The space key where the page will be created",
                required=True
            ),
            ToolParameter(
                name="page_title",
                type=ParameterType.STRING,
                description="The title of the page to create",
                required=True
            ),
            ToolParameter(
                name="page_content",
                type=ParameterType.STRING,
                description="The content of the page in Confluence markup",
                required=True
            ),
            ToolParameter(
                name="parent_id",
                type=ParameterType.STRING,
                description="The ID of the parent page (optional)",
                required=False
            )
        ]
    )
    def create_page(self, space: str, page_title: str, page_content: str, parent_id: Optional[str] = None) -> tuple[bool, str]:
        """Create a new page in Confluence
        """
        """
        Args:
            space: The space of the page
            page_title: The title of the page
            page_content: The content of the page
            parent_id: The ID of the parent page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        space_id = self.__get_space_id(space)
        if not space_id:
            return (False, json.dumps({"error": "Space not found"}))

        try:
            page = self.confluence.create_page(
                space=space_id,
                page_title=page_title,
                page_content=page_content,
                parent_id=parent_id
            )
            return (True, json.dumps({
                "confluence_url": f"{self.base_url}/display/{page.id}", # type: ignore
                "page_id": page.id, # type: ignore
                "page_title": page_title,
                "page_content": page_content,
                "parent_id": parent_id
            }))
        except Exception as e:
            logger.error(f"Failed to create page: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="get_page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to retrieve",
                required=True
            )
        ]
    )
    def get_page(self, page_id: str) -> tuple[bool, str]:
        """Get a page from Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            page = self.confluence.get_page(page_id)
            return (True, json.dumps(page))
        except Exception as e:
            logger.error(f"Failed to get page: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="update_page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to update",
                required=True
            ),
            ToolParameter(
                name="page_title",
                type=ParameterType.STRING,
                description="The new title for the page",
                required=True
            ),
            ToolParameter(
                name="page_content",
                type=ParameterType.STRING,
                description="The new content for the page in Confluence markup",
                required=True
            )
        ]
    )
    def update_page(self, page_id: str, page_title: str, page_content: str) -> tuple[bool, str]:
        """Update a page in Confluence
        """
        """
        Args:
            page_id: The ID of the page
            page_title: The title of the page
            page_content: The content of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the page details
        """
        try:
            page = self.confluence.update_page(page_id, page_title, page_content)
            return (True, json.dumps({
                "confluence_url": f"{self.base_url}/display/{page.id}", # type: ignore
                "page_id": page_id,
                "page_title": page_title,
                "page_content": page_content
            }))
        except Exception as e:
            logger.error(f"Failed to update page: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="delete_page",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to delete",
                required=True
            )
        ]
    )
    def delete_page(self, page_id: str) -> tuple[bool, str]:
        """Delete a page from Confluence
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the deletion result
        """
        try:
            self.confluence.delete_page(page_id)
            return (True, json.dumps({
                "message": f"Page {page_id} deleted successfully"
            }))
        except Exception as e:
            logger.error(f"Failed to delete page: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="get_page_children",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the parent page to get children for",
                required=True
            )
        ]
    )
    def get_page_children(self, page_id: str) -> tuple[bool, str]:
        """Get the children of a page
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the children pages
        """
        try:
            children = self.confluence.get_page_children(page_id)
            return (True, json.dumps(children))
        except Exception as e:
            logger.error(f"Failed to get page children: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="get_page_ancestors",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to get ancestors for",
                required=True
            )
        ]
    )
    def get_page_ancestors(self, page_id: str) -> tuple[bool, str]:
        """Get the ancestors of a page
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the ancestor pages
        """
        try:
            ancestors = self.confluence.get_page_ancestors(page_id)
            return (True, json.dumps(ancestors))
        except Exception as e:
            logger.error(f"Failed to get page ancestors: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="get_page_descendants",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to get descendants for",
                required=True
            )
        ]
    )
    def get_page_descendants(self, page_id: str) -> tuple[bool, str]:
        """Get the descendants of a page
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the descendant pages
        """
        try:
            descendants = self.confluence.get_page_descendants(page_id)
            return (True, json.dumps(descendants))
        except Exception as e:
            logger.error(f"Failed to get page descendants: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="get_page_parent",
        parameters=[
            ToolParameter(
                name="page_id",
                type=ParameterType.STRING,
                description="The ID of the page to get the parent for",
                required=True
            )
        ]
    )
    def get_page_parent(self, page_id: str) -> tuple[bool, str]:
        """Get the parent of a page
        """
        """
        Args:
            page_id: The ID of the page
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the parent page
        """
        try:
            parent = self.confluence.get_page_parent(page_id)
            return (True, json.dumps(parent))
        except Exception as e:
            logger.error(f"Failed to get page parent: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="confluence",
        tool_name="search_pages",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query to find pages",
                required=True
            ),
            ToolParameter(
                name="expand",
                type=ParameterType.STRING,
                description="Fields to expand in search results",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of pages to return",
                required=False
            )
        ]
    )
    def search_pages(self, query: str, expand: Optional[str] = None, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search for pages in Confluence
        """
        """
        Args:
            query: The search query to find pages
            expand: Fields to expand in search results
            limit: Maximum number of pages to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            results = self.confluence.cql(cql=query, expand=expand, limit=limit) # type: ignore
            return (True, json.dumps(results))
        except Exception as e:
            logger.error(f"Failed to search pages: {e}")
            return (False, json.dumps({"error": str(e)}))

    def __get_space_id(self, space: str) -> Optional[str]:
        """Get the space ID from the space key"""
        """Get the ID of a space in Confluence
        """
        """
        Args:
            space: The name of the space
        Returns:
            The ID of the space
        """
        try:
            spaces = self.confluence.get_spaces() # type: ignore
            for space_obj in spaces:
                if space_obj.name == space: # type: ignore
                    return space_obj.id # type: ignore
            return None
        except Exception as e:
            logger.error(f"Failed to get space ID: {e}")
            return None
