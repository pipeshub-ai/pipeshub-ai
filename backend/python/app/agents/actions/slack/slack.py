
import asyncio
import logging
from typing import Any, Optional, Tuple

from app.agents.actions.slack.config import SlackResponse
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource

logger = logging.getLogger(__name__)

class Slack:
    """Slack tool exposed to the agents using SlackDataSource"""

    def __init__(self, client: SlackClient) -> None:
        """Initialize the Slack tool"""
        """
        Args:
            client: Slack client object
        Returns:
            None
        """
        self.client = SlackDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
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
            # Resolve channel name like "#bugs" to channel ID
            chan = channel
            try:
                if isinstance(chan, str):
                    name = chan[1:] if chan.startswith('#') else chan
                    # If it doesn't look like a channel ID (C...), try to find by name
                    if not name.startswith('C'):
                        clist = self._run_async(self.client.conversations_list())
                        cl_resp = self._handle_slack_response(clist)
                        if cl_resp.success and cl_resp.data and isinstance(cl_resp.data, dict):
                            for c in (cl_resp.data.get('channels') or []):
                                if isinstance(c, dict) and c.get('name') == name:
                                    chan = c.get('id') or chan
                                    break
            except Exception:
                pass

            # Use SlackDataSource method
            response = self._run_async(self.client.conversations_history(
                channel=chan,
                limit=limit
            ))
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())

            # Resolve Slack mentions in message text: <@UXXXXXXXX> -> @display_name
            try:
                data = slack_response.data
                messages = data.get('messages', []) if isinstance(data, dict) else []
                import re
                mention_re = re.compile(r"<@([A-Z0-9]+)>")
                user_ids: set[str] = set()
                for msg in messages:
                    if isinstance(msg, dict) and isinstance(msg.get('text'), str):
                        for m in mention_re.findall(msg['text']):
                            user_ids.add(m)
                id_to_name: dict[str, str] = {}
                id_to_email: dict[str, str] = {}
                for uid in user_ids:
                    try:
                        uresp = self._run_async(self.client.users_info(user=uid))
                        u = self._handle_slack_response(uresp)
                        if u.success and u.data and isinstance(u.data, dict):
                            user_obj = u.data.get('user') or {}
                            profile = user_obj.get('profile') or {}
                            display = profile.get('display_name') or user_obj.get('real_name') or user_obj.get('name') or uid
                            email = profile.get('email')
                            id_to_name[uid] = display
                            if email:
                                id_to_email[uid] = email
                    except Exception:
                        # Best-effort; skip failures
                        pass
                # Inject resolved fields without mutating originals
                resolved_messages = []
                for msg in messages:
                    if isinstance(msg, dict):
                        new_msg = dict(msg)
                        text = new_msg.get('text')
                        if isinstance(text, str):
                            def _rep(m) -> str:
                                return f"@{id_to_name.get(m.group(1), m.group(1))}"
                            new_msg['resolved_text'] = mention_re.sub(_rep, text)
                        mentions_meta = []
                        for uid in mention_re.findall(text or ""):
                            mentions_meta.append({
                                'id': uid,
                                'display_name': id_to_name.get(uid),
                                'email': id_to_email.get(uid),
                            })
                        if mentions_meta:
                            new_msg['mentions'] = mentions_meta
                        resolved_messages.append(new_msg)
                    else:
                        resolved_messages.append(msg)
                enriched = dict(data)
                enriched['messages'] = resolved_messages
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception:
                # If enrichment fails, return original
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

    @tool(
        app_name="slack",
        tool_name="resolve_user",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="Slack user ID (e.g., U123ABC45) to resolve to name/email",
                required=True
            )
        ]
    )
    def resolve_user(self, user_id: str) -> Tuple[bool, str]:
        """Resolve a Slack user ID to display name and email"""
        try:
            response = self._run_async(self.client.users_info(user=user_id))
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())
            data = slack_response.data if isinstance(slack_response.data, dict) else {}
            user = data.get('user') or {}
            profile = user.get('profile') or {}
            result = {
                'id': user.get('id') or user_id,
                'real_name': user.get('real_name'),
                'display_name': profile.get('display_name') or user.get('name') or user.get('real_name'),
                'email': profile.get('email'),
            }
            return (True, SlackResponse(success=True, data=result).to_json())
        except Exception as e:
            logger.error(f"Error in resolve_user: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="check_token_info"
    )
    def check_token_info(self) -> Tuple[bool, str]:
        """Check Slack token information and available scopes"""
        """
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with token information
        """
        try:
            # Use SlackDataSource method
            response = self._run_async(self.client.check_token_scopes())
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in check_token_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())
