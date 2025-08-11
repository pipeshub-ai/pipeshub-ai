import json
import logging
from typing import Optional

from app.agents.actions.slack.config import SlackTokenConfig
from app.agents.tool.decorator import tool

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

    @tool(app_name="slack", tool_name="send_message")
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

    @tool(app_name="slack", tool_name="get_channel_history")
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

    @tool(app_name="slack", tool_name="get_channel_info")
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

    @tool(app_name="slack", tool_name="get_user_info")
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

    @tool(app_name="slack", tool_name="fetch_channels")
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

    @tool(app_name="slack", tool_name="search_messages")
    def search_messages(self, query: str, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search for messages in a channel"""
        """
        Args:
            query: The query to search for
            limit: The limit of the search results
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the messages
        """
        try:
            response = self.client.search_messages(query=query, limit=limit) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to search messages: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_user_presence")
    def get_user_presence(self, user: str) -> tuple[bool, str]:
        """Get the presence of a user"""
        """
        Args:
            user: The user to get the presence of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the presence
        """
        try:
            response = self.client.users_presence(user=user) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get user presence: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_channel_users")
    def get_channel_users(self, channel: str) -> tuple[bool, str]:
        """Get the list of users in a channel"""
        """
        Args:
            channel: The channel to get the list of users in
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the list of users
        """
        try:
            response = self.client.conversations_members(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel users: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_channel_members")
    def get_channel_members(self, channel: str) -> tuple[bool, str]:
        """Get the list of members in a channel"""
        """
        Args:
            channel: The channel to get the list of members in
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the list of members
        """
        try:
            response = self.client.conversations_members(channel=channel) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_channel_members_by_id")
    def get_channel_members_by_id(self, channel_id: str) -> tuple[bool, str]:
        """Get the list of members in a channel using the conversations.members method"""
        """
        Args:
            channel_id: The channel to get the list of members in
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the list of members
        """
        try:
            response = self.client.conversations_members(channel=channel_id) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members using conversations.members: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_channel_members_by_name")
    def get_channel_members_by_name(self, channel_name: str) -> tuple[bool, str]:
        """Get the list of members in a channel using the conversations.members method"""
        """
        Args:
            channel_name: The channel to get the list of members in
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the list of members
        """
        try:
            response = self.client.conversations_members(channel=channel_name) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members using conversations.members: {e}")
            return (False, json.dumps({"error": str(e)}))

    @tool(app_name="slack", tool_name="get_channel_members_by_id_and_name")
    def get_channel_members_by_id_and_name(self, channel_id: str, channel_name: str) -> tuple[bool, str]:
        """Get the list of members in a channel using the conversations.members method"""
        """
        Args:
            channel_id: The channel to get the list of members in
            channel_name: The channel name
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the list of members
        """
        try:
            response = self.client.conversations_members(channel=channel_id, name=channel_name) # type: ignore
            return (True, json.dumps(response))
        except Exception as e:
            logger.error(f"Failed to get channel members using conversations.members: {e}")
            return (False, json.dumps({"error": str(e)}))
