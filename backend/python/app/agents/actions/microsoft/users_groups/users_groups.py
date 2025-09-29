import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.microsoft.users_groups.users_groups import (
    UsersGroupsDataSource,
)

logger = logging.getLogger(__name__)


class UsersGroups:
    """Microsoft Users & Groups tool exposed to the agents"""
    def __init__(self, client: object) -> None:
        """Initialize the Users & Groups tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = UsersGroupsDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="users_groups",
        tool_name="get_users",
        description="Get users from Microsoft Graph",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of users to retrieve",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="OData filter expression",
                required=False
            )
        ]
    )
    def get_users(
        self,
        top: Optional[int] = None,
        filter: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get users from Microsoft Graph"""
        """
        Args:
            top: Number of users to retrieve
            filter: OData filter expression
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.get_users(
                top=top,
                filter=filter
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="get_user",
        description="Get a specific user from Microsoft Graph",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="ID of the user",
                required=True
            )
        ]
    )
    def get_user(self, user_id: str) -> Tuple[bool, str]:
        """Get a specific user from Microsoft Graph"""
        """
        Args:
            user_id: ID of the user
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.get_user(user_id=user_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="create_user",
        description="Create a new user in Microsoft Graph",
        parameters=[
            ToolParameter(
                name="display_name",
                type=ParameterType.STRING,
                description="Display name of the user",
                required=True
            ),
            ToolParameter(
                name="mail_nickname",
                type=ParameterType.STRING,
                description="Mail nickname of the user",
                required=True
            ),
            ToolParameter(
                name="user_principal_name",
                type=ParameterType.STRING,
                description="User principal name",
                required=True
            ),
            ToolParameter(
                name="password",
                type=ParameterType.STRING,
                description="Password for the user",
                required=True
            )
        ]
    )
    def create_user(
        self,
        display_name: str,
        mail_nickname: str,
        user_principal_name: str,
        password: str
    ) -> Tuple[bool, str]:
        """Create a new user in Microsoft Graph"""
        """
        Args:
            display_name: Display name of the user
            mail_nickname: Mail nickname of the user
            user_principal_name: User principal name
            password: Password for the user
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.create_user(
                display_name=display_name,
                mail_nickname=mail_nickname,
                user_principal_name=user_principal_name,
                password=password
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in create_user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="get_groups",
        description="Get groups from Microsoft Graph",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of groups to retrieve",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="OData filter expression",
                required=False
            )
        ]
    )
    def get_groups(
        self,
        top: Optional[int] = None,
        filter: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get groups from Microsoft Graph"""
        """
        Args:
            top: Number of groups to retrieve
            filter: OData filter expression
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.get_groups(
                top=top,
                filter=filter
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_groups: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="get_group",
        description="Get a specific group from Microsoft Graph",
        parameters=[
            ToolParameter(
                name="group_id",
                type=ParameterType.STRING,
                description="ID of the group",
                required=True
            )
        ]
    )
    def get_group(self, group_id: str) -> Tuple[bool, str]:
        """Get a specific group from Microsoft Graph"""
        """
        Args:
            group_id: ID of the group
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.get_group(group_id=group_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_group: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="create_group",
        description="Create a new group in Microsoft Graph",
        parameters=[
            ToolParameter(
                name="display_name",
                type=ParameterType.STRING,
                description="Display name of the group",
                required=True
            ),
            ToolParameter(
                name="mail_nickname",
                type=ParameterType.STRING,
                description="Mail nickname of the group",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Description of the group",
                required=False
            )
        ]
    )
    def create_group(
        self,
        display_name: str,
        mail_nickname: str,
        description: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Create a new group in Microsoft Graph"""
        """
        Args:
            display_name: Display name of the group
            mail_nickname: Mail nickname of the group
            description: Description of the group
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.create_group(
                display_name=display_name,
                mail_nickname=mail_nickname,
                description=description
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in create_group: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="add_member_to_group",
        description="Add a member to a group in Microsoft Graph",
        parameters=[
            ToolParameter(
                name="group_id",
                type=ParameterType.STRING,
                description="ID of the group",
                required=True
            ),
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="ID of the user to add",
                required=True
            )
        ]
    )
    def add_member_to_group(
        self,
        group_id: str,
        user_id: str
    ) -> Tuple[bool, str]:
        """Add a member to a group in Microsoft Graph"""
        """
        Args:
            group_id: ID of the group
            user_id: ID of the user to add
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.add_member_to_group(
                group_id=group_id,
                user_id=user_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in add_member_to_group: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="users_groups",
        tool_name="get_group_members",
        description="Get members of a group in Microsoft Graph",
        parameters=[
            ToolParameter(
                name="group_id",
                type=ParameterType.STRING,
                description="ID of the group",
                required=True
            )
        ]
    )
    def get_group_members(self, group_id: str) -> Tuple[bool, str]:
        """Get members of a group in Microsoft Graph"""
        """
        Args:
            group_id: ID of the group
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use UsersGroupsDataSource method
            response = self._run_async(self.client.get_group_members(group_id=group_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_group_members: {e}")
            return False, json.dumps({"error": str(e)})
