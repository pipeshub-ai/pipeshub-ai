import json
import logging
from typing import Optional

from app.agents.actions.slack.config import SlackTokenConfig
from app.agents.tool.decorator import tool
from app.agents.tool.enums import ParameterType
from app.agents.tool.models import ToolParameter

logger = logging.getLogger(__name__)

class Slack:
    """Slack tool exposed to the agents"""
    def __init__(self, config: SlackTokenConfig) -> None:
        """Initialize the Slack tool"""
        """
        Args:
            config: Slack configuration (SlackTokenConfig)
        Returns:
            None
        """
        self.config = config
        self.client = config.create_client()

    @tool(
        app_name="slack",
        tool_name="send_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to send the message to",
                required=True
            ),
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="The message to send",
                required=True
            )
        ]
    )
    def send_message(self, channel: str, message: str) -> tuple[bool, str]:
        """Send a message to a channel"""
        """
        Args:
            channel: The channel to send the message to
            message: The message to send
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            response = self.client.chat_postMessage(channel=channel, text=message)
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_history",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get the history of",
                required=True
            )
        ]
    )
    def get_channel_history(self, channel: str) -> tuple[bool, str]:
        """Get the history of a channel"""
        """
        Args:
            channel: The channel to get the history of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the history details
        """
        try:
            response = self.client.conversations_history(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel history: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_info",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get the info of",
                required=True
            )
        ]
    )
    def get_channel_info(self, channel: str) -> tuple[bool, str]:
        """Get the info of a channel"""
        """
        Args:
            channel: The channel to get the info of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel info
        """
        try:
            response = self.client.conversations_info(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel info: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_user_info",
        parameters=[
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="The user to get the info of",
                required=True
            )
        ]
    )
    def get_user_info(self, user: str) -> tuple[bool, str]:
        """Get the info of a user"""
        """
        Args:
            user: The user to get the info of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the user info
        """
        try:
            response = self.client.users_info(user=user) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="fetch_channels"
    )
    def fetch_channels(self) -> tuple[bool, str]:
        """Fetch all channels"""
        """
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channels
        """
        try:
            response = self.client.conversations_list() # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to fetch channels: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="search_messages",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query to find messages",
                required=True
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of messages to return",
                required=False
            )
        ]
    )
    def search_messages(self, query: str, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search messages in Slack"""
        """
        Args:
            query: The search query to find messages
            limit: Maximum number of messages to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            response = self.client.search_messages(query=query, count=limit) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to search messages: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_user_presence",
        parameters=[
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="The user to get the presence of",
                required=True
            )
        ]
    )
    def get_user_presence(self, user: str) -> tuple[bool, str]:
        """Get the presence of a user"""
        """
        Args:
            user: The user to get the presence of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the user presence
        """
        try:
            response = self.client.users_getPresence(user=user) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get user presence: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_users",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get the users of",
                required=True
            )
        ]
    )
    def get_channel_users(self, channel: str) -> tuple[bool, str]:
        """Get the users of a channel"""
        """
        Args:
            channel: The channel to get the users of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel users
        """
        try:
            response = self.client.conversations_members(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel users: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_members",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get the members of",
                required=True
            )
        ]
    )
    def get_channel_members(self, channel: str) -> tuple[bool, str]:
        """Get the members of a channel"""
        """
        Args:
            channel: The channel to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            response = self.client.conversations_members(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_members_by_id",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="The channel ID to get the members of",
                required=True
            )
        ]
    )
    def get_channel_members_by_id(self, channel_id: str) -> tuple[bool, str]:
        """Get the members of a channel by ID"""
        """
        Args:
            channel_id: The channel ID to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            response = self.client.conversations_members(channel=channel_id) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members by ID: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_members_by_name",
        parameters=[
            ToolParameter(
                name="channel_name",
                type=ParameterType.STRING,
                description="The channel name to get the members of",
                required=True
            )
        ]
    )
    def get_channel_members_by_name(self, channel_name: str) -> tuple[bool, str]:
        """Get the members of a channel by name"""
        """
        Args:
            channel_name: The channel name to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # First get channel info by name
            channel_info = self.client.conversations_info(channel=channel_name) # type: ignore
            channel_id = channel_info['channel']['id']
            response = self.client.conversations_members(channel=channel_id) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members by name: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(
        app_name="slack",
        tool_name="get_channel_members_by_id_and_name",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="The channel ID to get the members of",
                required=True
            ),
            ToolParameter(
                name="channel_name",
                type=ParameterType.STRING,
                description="The channel name for verification",
                required=True
            )
        ]
    )
    def get_channel_members_by_id_and_name(self, channel_id: str, channel_name: str) -> tuple[bool, str]:
        """Get the members of a channel by ID and verify with name"""
        """
        Args:
            channel_id: The channel ID to get the members of
            channel_name: The channel name for verification
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # Verify channel name matches ID
            channel_info = self.client.conversations_info(channel=channel_id) # type: ignore
            if channel_info['channel']['name'] != channel_name:
                return (False, json.dumps({"error": "Channel name does not match ID"}))

            response = self.client.conversations_members(channel=channel_id) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members by ID and name: {e}")
            return (False, json.dumps({"error": str(e)}))
