import json
import logging
from typing import Tuple

from app.agents.client.confluence import ConfluenceClient
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

logger = logging.getLogger(__name__)


class Confluence:
    """Confluence tool exposed to the agents"""
    def __init__(
        self,
        client: ConfluenceClient,
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
        description="Create a page in Confluence",
        parameters=[
            ToolParameter(name="space_id", type=ParameterType.STRING, description="The ID of the space to create the page in"),
            ToolParameter(name="page_title", type=ParameterType.STRING, description="The title of the page to create"),
            ToolParameter(name="page_content", type=ParameterType.STRING, description="The content of the page to create"),
        ],
        returns="A message indicating whether the page was created successfully"
    )
    async def create_page(self, space_id: str, page_title: str, page_content: str) -> Tuple[bool, str]:
        try:
            await self.confluence.get_client().create_page(space_id, page_title, page_content) # type: ignore
            return True, json.dumps({"message": "Page created successfully"})
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
    async def get_page_content(self, page_id: str) -> Tuple[bool, str]:
        try:
            content = await self.confluence.get_client().get_page_content(page_id) # type: ignore
            return True, content
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
    async def get_pages(self) -> Tuple[bool, str]:
        try:
            pages = await self.confluence.get_client().get_pages() # type: ignore
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
    async def invite_email(self, email: str) -> Tuple[bool, str]:
        try:
            await self.confluence.get_client().invite_email(email) # type: ignore
            return True, json.dumps({"message": "Email invited successfully"})
        except Exception as e:
            logger.error(f"Error inviting email: {e}")
            return False, json.dumps({"message": f"Error inviting email: {e}"})

    @tool(
        app_name="confluence",
        tool_name="get_spaces_with_permissions",
        description="Get the spaces with permissions in Confluence",
        parameters=[]
    )
    async def get_spaces_with_permissions(self) -> Tuple[bool, str]:
        try:
            spaces = await self.confluence.get_client().get_spaces_with_permissions() # type: ignore
            return True, json.dumps({"message": "Spaces with permissions fetched successfully", "spaces": spaces})
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
    async def get_page(self, page_id: str) -> Tuple[bool, str]:
        try:
            page = await self.confluence.get_client().get_page(page_id) # type: ignore
            return True, json.dumps({"message": "Page fetched successfully", "page": page})
        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"message": f"Error getting page: {e}"})
