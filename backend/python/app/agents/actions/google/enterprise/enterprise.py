import json

from app.agents.actions.google.google_drive.google_drive import (
    ParameterType,
    ToolParameter,
    tool,
)
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
        app_name="google_drive_enterprise",
        tool_name="get_users_list",
        parameters=[
            ToolParameter(
                name="customer",
                type=ParameterType.STRING,
                description="The customer ID to get the list of users for",
                required=True
            )
        ]
    )
    def get_users_list(self, customer: str = "my_customer") -> tuple[bool, str]:
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
        app_name="google_drive_enterprise",
        tool_name="get_groups_list",
        parameters=[
            ToolParameter(
                name="customer",
                type=ParameterType.STRING,
                description="The customer ID to get the list of groups for",
                required=True
            )
        ]
    )
    def get_groups_list(self, customer: str = "my_customer") -> tuple[bool, str]:
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
        app_name="google_drive_enterprise",
        tool_name="get_domains_list",
        parameters=[
            ToolParameter(
                name="customer",
                type=ParameterType.STRING,
                description="The customer ID to get the list of domains for",
                required=True
            )
        ]
    )
    def get_domains_list(self, customer: str = "my_customer") -> tuple[bool, str]:
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
        app_name="google_drive_enterprise",
        tool_name="get_group_members_list",
        parameters=[
            ToolParameter(
                name="group_email",
                type=ParameterType.STRING,
                description="The email of the group to get the list of members for",
                required=True
            )
        ]
    )
    def get_group_members_list(self, group_email: str) -> tuple[bool, str]:
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
        app_name="google_drive_enterprise",
        tool_name="get_user_info",
        parameters=[
            ToolParameter(
                name="user_email",
                type=ParameterType.STRING,
                description="The email of the user to get the info for",
                required=True
            )
        ]
    )
    def get_user_info(self, user_email: str) -> tuple[bool, str]:
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
        app_name="google_drive_enterprise",
        tool_name="get_group_info",
        parameters=[
            ToolParameter(
                name="group_email",
                type=ParameterType.STRING,
                description="The email of the group to get the info for",
                required=True
            )
        ]
    )
    def get_group_info(self, group_email: str) -> tuple[bool, str]:
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
