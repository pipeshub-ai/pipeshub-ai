
import asyncio
import logging
from typing import Any, Optional, Tuple

from app.agents.actions.slack.config import SlackResponse
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.slack.slack import SlackDataSource

logger = logging.getLogger(__name__)

class Slack:
    """Slack tool exposed to the agents using SlackDataSource"""

    def __init__(self, client: object) -> None:
        """Initialize the Slack tool"""
        """
        Args:
            client: Slack client object
        Returns:
            None
        """
        self.client = SlackDataSource(client)

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

    def _handle_slack_response(self, response: Any) -> SlackResponse:  # noqa: ANN401
        """Handle Slack API response and convert to standardized format.
        - If response already is a SlackResponse (has 'success'), pass it through
        - If it's a dict with 'ok'==False, return error
        - Otherwise treat as success and wrap data
        """
        try:
            if response is None:
                return SlackResponse(success=False, error="Empty response from Slack API")

            # Pass-through if already normalized
            if hasattr(response, 'success') and hasattr(response, 'data'):
                return response  # type: ignore[return-value]

            # Dict-like payload from WebClient
            if isinstance(response, dict):
                if response.get('ok') is False:
                    return SlackResponse(success=False, error=response.get('error', 'unknown_error'))
                return SlackResponse(success=True, data=response)

            # Fallback: wrap arbitrary payload
            return SlackResponse(success=True, data={"raw_response": str(response)})
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
    def send_message(self, channel: str, message: str) -> Tuple[bool, str]:
        """Send a message to a channel"""
        """
        Args:
            channel: The channel to send the message to
            message: The message to send
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.chat_me_message(
                channel=channel,
                text=message
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            # Explicitly surface membership errors without side-effects
            if "not_in_channel" in str(e):
                err = SlackResponse(success=False, error="not_in_channel")
                return (err.success, err.to_json())
            logger.error(f"Error in send_message: {e}")
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
    def get_channel_history(self, channel: str, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get the history of a channel"""
        """
        Args:
            channel: The channel to get the history of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the history details
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_history(
                channel=channel,
                limit=limit
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            if "not_in_channel" in str(e):
                err = SlackResponse(success=False, error="not_in_channel")
                return (err.success, err.to_json())
            logger.error(f"Error in get_channel_history: {e}")
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
    def get_channel_info(self, channel: str) -> Tuple[bool, str]:
        """Get the info of a channel"""
        """
        Args:
            channel: The channel to get the info of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel info
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_info(channel=channel))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_channel_info: {e}")
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
    def get_user_info(self, user: str) -> Tuple[bool, str]:
        """Get the info of a user"""
        """
        Args:
            user: The user to get the info of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the user info
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.users_info(user=user))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="fetch_channels"
    )
    def fetch_channels(self) -> Tuple[bool, str]:
        """Fetch all channels"""
        """
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channels
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_list())
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in fetch_channels: {e}")
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
    def search_all(self, query: str, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Search messages, files, and channels in Slack"""
        """
        Args:
            query: The search query to find messages, files, and channels
            limit: Maximum number of results to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.search_messages(
                query=query,
                count=limit
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in search_all: {e}")
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
    def get_channel_members(self, channel: str) -> Tuple[bool, str]:
        """Get the members of a channel"""
        """
        Args:
            channel: The channel to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_members(channel=channel))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            if "not_in_channel" in str(e):
                err = SlackResponse(success=False, error="not_in_channel")
                return (err.success, err.to_json())
            logger.error(f"Error in get_channel_members: {e}")
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
    def get_channel_members_by_id(self, channel_id: str) -> Tuple[bool, str]:
        """Get the members of a channel by ID"""
        """
        Args:
            channel_id: The channel ID to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_members(channel=channel_id))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_channel_members_by_id: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())
