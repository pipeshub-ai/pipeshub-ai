import asyncio
import json
import logging
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

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)


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
            # Use ConfluenceDataSource method (create_page)
            page = self._run_async(self.client.create_page(
                body={
                    "type": "page",
                    "title": page_title,
                    "space": {"id": int(space_id)},
                    "body": {
                        "storage": {
                            "value": page_content,
                            "representation": "storage"
                        }
                    }
                }
            ))
            return True, json.dumps({"message": "Page created successfully", "page": page})
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
            content = self._run_async(self.client.get_page_by_id(
                id=int(page_id),
                body_format={"storage": {}}
            ))
            return True, json.dumps({"message": "Page content fetched successfully", "content": content})
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
            # Use ConfluenceDataSource method
            pages = self._run_async(self.client.get_pages_in_space(
                id=int(space_id)
            ))
            return True, json.dumps({"message": "Pages fetched successfully", "pages": pages})
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
            result = self._run_async(self.client.invite_by_email(
                body={"email": email}
            ))
            return True, json.dumps({"message": "Email invited successfully", "result": result})
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
            spaces = self._run_async(self.client.get_spaces())
            return True, json.dumps({"message": "Spaces fetched successfully", "spaces": spaces})
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
            page = self._run_async(self.client.get_page_by_id(
                id=int(page_id),
                body_format={"storage": {}}
            ))
            return True, json.dumps({"message": "Page fetched successfully", "page": page})
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
            space = self._run_async(self.client.get_space_by_id(id=int(space_id)))
            return True, json.dumps({"message": "Space fetched successfully", "space": space})
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
            versions = self._run_async(self.client.get_page_versions(id=int(page_id)))
            return True, json.dumps({"message": "Page versions fetched successfully", "versions": versions})
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
            result = self._run_async(self.client.update_page_title(
                id=int(page_id),
                body={"title": new_title}
            ))
            return True, json.dumps({"message": "Page title updated successfully", "result": result})
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
            children = self._run_async(self.client.get_child_pages(id=int(page_id)))
            return True, json.dumps({"message": "Child pages fetched successfully", "children": children})
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
                kwargs["space_id"] = [int(space_id)]
            pages = self._run_async(self.client.get_pages(**kwargs))
            return True, json.dumps({"message": "Pages search successful", "pages": pages})
        except Exception as e:
            logger.error(f"Error searching pages: {e}")
            return False, json.dumps({"message": f"Error searching pages: {e}"})
