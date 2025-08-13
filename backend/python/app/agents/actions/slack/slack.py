
import logging
from typing import Any, Optional

from app.agents.actions.slack.config import SlackResponse, SlackTokenConfig
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

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

    def _handle_slack_response(self, response: Any) -> SlackResponse:  # noqa: ANN401
        """Handle Slack API response and convert to standardized format"""
        try:
            if not response:
                return SlackResponse(success=False, error="Empty response from Slack API")
            # Extract data from SlackResponse object
            if hasattr(response, 'data'):
                data = response.data
            elif hasattr(response, 'get'):
                data = dict(response)
            else:
                data = {"raw_response": str(response)}

            return SlackResponse(success=True, data=data)
        except Exception as e:
            logger.error(f"Error handling Slack response: {e}")
            return SlackResponse(success=False, error=str(e))

    def _handle_slack_error(self, error: Exception) -> SlackResponse:
        """Handle Slack API errors and convert to standardized format"""
        error_msg = str(error)
        logger.error(f"Slack API error: {error_msg}")
        return SlackResponse(success=False, error=error_msg)

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_channel_history",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get the history of",
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
    def get_channel_history(self, channel: str, limit: Optional[int] = None) -> tuple[bool, str]:
        """Get the history of a channel"""
        """
        Args:
            channel: The channel to get the history of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the history details
        """
        try:
            response = self.client.conversations_history(channel=channel, limit=limit) # type: ignore
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="search_all",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query to find messages, files, and channels",
                required=True
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            )
        ]
    )
    def search_all(self, query: str, limit: Optional[int] = None) -> tuple[bool, str]:
        """Search messages, files, and channels in Slack"""
        """
        Args:
            query: The search query to find messages, files, and channels
            limit: Maximum number of results to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            response = self.client.search_all(query=query, count=limit) # type: ignore
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

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
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())
