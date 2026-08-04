import json

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.sources.client.google.google import GoogleClient


class GoogleDriveEnterprise:
    """Google Drive Enterprise tool exposed to the agents
    Args:
        client: Authenticated Google Drive client
    Returns:
        None
    """
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Drive Enterprise tool"""
        self.client = client

    @tool(
        path="/tools/google_drive_enterprise/get_users_list",
        short_description="List users in the Google Workspace domain",
        description="Get the list of users in the Google Workspace domain.",
        parameters=[
            ToolParameter(name="customer", type=ParameterType.STRING, description="The customer ID to get the list of users for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_users_list(self, customer: str = "my_customer") -> tuple[bool, str]:
        """Get the list of users in the domain
        Args:
            customer: The customer ID to get the list of users for
        Returns:
            tuple[bool, str]: True if the list of users is retrieved, False otherwise
        """
        try:
            users = self.client.users().list(customer=customer, orderBy="email", projection="full").execute() # type: ignore
            return True, json.dumps(users)
        except Exception as e:
            return False, json.dumps({"error": str(e)})


    @tool(
        path="/tools/google_drive_enterprise/get_groups_list",
        short_description="List groups in the Google Workspace domain",
        description="Get the list of groups in the Google Workspace domain.",
        parameters=[
            ToolParameter(name="customer", type=ParameterType.STRING, description="The customer ID to get the list of groups for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_groups_list(self, customer: str = "my_customer") -> tuple[bool, str]:
        """Get the list of groups in the domain
        Args:
            customer: The customer ID to get the list of groups for
        Returns:
            tuple[bool, str]: True if the list of groups is retrieved, False otherwise
        """
        try:
            groups = self.client.groups().list(customer=customer).execute() # type: ignore
            return True, json.dumps(groups)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/google_drive_enterprise/get_domains_list",
        short_description="List domains in the Google Workspace domain",
        description="Get the list of domains in the Google Workspace domain.",
        parameters=[
            ToolParameter(name="customer", type=ParameterType.STRING, description="The customer ID to get the list of domains for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_domains_list(self, customer: str = "my_customer") -> tuple[bool, str]:
        """Get the list of domains in the domain
        Args:
            customer: The customer ID to get the list of domains for
        Returns:
            tuple[bool, str]: True if the list of domains is retrieved, False otherwise
        """
        try:
            domains = self.client.domains().list(customer=customer).execute() # type: ignore
            return True, json.dumps(domains)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/google_drive_enterprise/get_group_members_list",
        short_description="List members of a Google Workspace group",
        description="Get the list of members in a Google Workspace group.",
        parameters=[
            ToolParameter(name="group_email", type=ParameterType.STRING, description="The email of the group to get the list of members for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_group_members_list(self, group_email: str) -> tuple[bool, str]:
        """Get the list of members in a group
        Args:
            group_email: The email of the group to get the list of members for
        Returns:
            tuple[bool, str]: True if the list of members is retrieved, False otherwise
        """
        try:
            members = self.client.members().get(groupKey=group_email).execute() # type: ignore
            return True, json.dumps(members)
        except Exception as e:
            return False, json.dumps({"error": str(e)})


    @tool(
        path="/tools/google_drive_enterprise/get_user_info",
        short_description="Get info for a specific Google Workspace user",
        description="Get the info of a user in the Google Workspace domain.",
        parameters=[
            ToolParameter(name="user_email", type=ParameterType.STRING, description="The email of the user to get the info for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_user_info(self, user_email: str) -> tuple[bool, str]:
        """Get the info of a user
        Args:
            user_email: The email of the user to get the info for
        Returns:
            tuple[bool, str]: True if the user info is retrieved, False otherwise
        """

        try:
            user_info = self.client.users().get(userKey=user_email).execute() # type: ignore
            return True, json.dumps(user_info)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/google_drive_enterprise/get_group_info",
        short_description="Get info for a specific Google Workspace group",
        description="Get the info of a group in the Google Workspace domain.",
        parameters=[
            ToolParameter(name="group_email", type=ParameterType.STRING, description="The email of the group to get the info for", required=True),
        ],
        tags=[Tag(key="category", value="enterprise"), Tag(key="type", value="read")],
    )
    async def get_group_info(self, group_email: str) -> tuple[bool, str]:
        """Get the info of a group
        Args:
            group_email: The email of the group to get the info for
        Returns:
            tuple[bool, str]: True if the group info is retrieved, False otherwise
        """
        try:
            group_info = self.client.groups().get(groupKey=group_email).execute() # type: ignore
            return True, json.dumps(group_info)
        except Exception as e:
            return False, json.dumps({"error": str(e)})
