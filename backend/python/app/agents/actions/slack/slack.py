
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

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

    @tool(
        app_name="slack",
        tool_name="send_direct_message",
        parameters=[
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="User ID, email, or display name to send DM to",
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
    def send_direct_message(self, user: str, message: str) -> Tuple[bool, str]:
        """Send a direct message to a user"""
        """
        Args:
            user: User ID, email, or display name to send DM to
            message: The message to send
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            # First, try to resolve the user
            user_id = self._resolve_user_identifier(user)
            if not user_id:
                return (False, SlackResponse(success=False, error=f"User '{user}' not found").to_json())

            # Open DM conversation
            response = self._run_async(self.client.conversations_open(users=[user_id]))
            slack_response = self._handle_slack_response(response)

            if not slack_response.success:
                return (slack_response.success, slack_response.to_json())

            # Get channel ID from the opened conversation
            channel_id = slack_response.data.get('channel', {}).get('id') if slack_response.data else None
            if not channel_id:
                return (False, SlackResponse(success=False, error="Failed to get DM channel ID").to_json())

            # Send message to DM channel
            message_response = self._run_async(self.client.chat_post_message(
                channel=channel_id,
                text=message
            ))
            message_slack_response = self._handle_slack_response(message_response)
            return (message_slack_response.success, message_slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in send_direct_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_message_with_formatting",
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
                description="The message to send with markdown formatting",
                required=True
            ),
            ToolParameter(
                name="blocks",
                type=ParameterType.ARRAY,
                description="Rich message blocks for advanced formatting",
                required=False,
                items={"type": "object"}
            )
        ]
    )
    def send_message_with_formatting(self, channel: str, message: str, blocks: Optional[List[Dict]] = None) -> Tuple[bool, str]:
        """Send a message with markdown formatting or rich blocks"""
        """
        Args:
            channel: The channel to send the message to
            message: The message to send with markdown formatting
            blocks: Rich message blocks for advanced formatting
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            kwargs = {
                "channel": channel,
                "text": message,
                "mrkdwn": True
            }

            if blocks:
                kwargs["blocks"] = blocks

            response = self._run_async(self.client.chat_post_message(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in send_message_with_formatting: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="reply_to_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message to reply to",
                required=True
            ),
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="The reply message",
                required=True
            ),
            ToolParameter(
                name="thread_ts",
                type=ParameterType.STRING,
                description="Timestamp of the parent message to reply to",
                required=False
            ),
            ToolParameter(
                name="latest_message",
                type=ParameterType.BOOLEAN,
                description="Whether to reply to the latest message in the channel",
                required=False
            )
        ]
    )
    def reply_to_message(self, channel: str, message: str, thread_ts: Optional[str] = None, latest_message: Optional[bool] = None) -> Tuple[bool, str]:
        """Reply to a specific message in a channel"""
        """
        Args:
            channel: The channel containing the message to reply to
            message: The reply message
            thread_ts: Timestamp of the parent message to reply to
            latest_message: Whether to reply to the latest message in the channel
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the reply details
        """
        try:
            # If latest_message is True, get the latest message timestamp
            if latest_message and not thread_ts:
                history_response = self._run_async(self.client.conversations_history(channel=channel, limit=1))
                history_slack_response = self._handle_slack_response(history_response)

                if not history_slack_response.success or not history_slack_response.data:
                    return (False, SlackResponse(success=False, error="Failed to get latest message").to_json())

                messages = history_slack_response.data.get('messages', [])
                if not messages:
                    return (False, SlackResponse(success=False, error="No messages found in channel").to_json())

                thread_ts = messages[0].get('ts')

            if not thread_ts:
                return (False, SlackResponse(success=False, error="No thread timestamp provided").to_json())

            # Send reply
            response = self._run_async(self.client.chat_post_message(
                channel=channel,
                text=message,
                thread_ts=thread_ts
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in reply_to_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_message_to_multiple_channels",
        parameters=[
            ToolParameter(
                name="channels",
                type=ParameterType.ARRAY,
                description="List of channels to send the message to",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="message",
                type=ParameterType.STRING,
                description="The message to send to all channels",
                required=True
            )
        ]
    )
    def send_message_to_multiple_channels(self, channels: List[str], message: str) -> Tuple[bool, str]:
        """Send the same message to multiple channels"""
        """
        Args:
            channels: List of channels to send the message to
            message: The message to send to all channels
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the results
        """
        try:
            results = []
            all_success = True

            for channel in channels:
                try:
                    response = self._run_async(self.client.chat_post_message(
                        channel=channel,
                        text=message
                    ))
                    slack_response = self._handle_slack_response(response)
                    results.append({
                        "channel": channel,
                        "success": slack_response.success,
                        "data": slack_response.data if slack_response.success else None,
                        "error": slack_response.error if not slack_response.success else None
                    })
                    if not slack_response.success:
                        all_success = False
                except Exception as e:
                    results.append({
                        "channel": channel,
                        "success": False,
                        "error": str(e)
                    })
                    all_success = False

            return (all_success, SlackResponse(success=all_success, data={"results": results}).to_json())

        except Exception as e:
            logger.error(f"Error in send_message_to_multiple_channels: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="upload_file",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to upload the file to",
                required=True
            ),
            ToolParameter(
                name="file_path",
                type=ParameterType.STRING,
                description="Path to the file to upload",
                required=False
            ),
            ToolParameter(
                name="file_content",
                type=ParameterType.STRING,
                description="Content of the file to upload",
                required=False
            ),
            ToolParameter(
                name="filename",
                type=ParameterType.STRING,
                description="Name of the file",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the file",
                required=False
            ),
            ToolParameter(
                name="initial_comment",
                type=ParameterType.STRING,
                description="Initial comment about the file",
                required=False
            )
        ]
    )
    def upload_file(self, channel: str, filename: str, file_path: Optional[str] = None, file_content: Optional[str] = None, title: Optional[str] = None, initial_comment: Optional[str] = None) -> Tuple[bool, str]:
        """Upload a file to a channel"""
        """
        Args:
            channel: The channel to upload the file to
            filename: Name of the file
            file_path: Path to the file to upload
            file_content: Content of the file to upload
            title: Title of the file
            initial_comment: Initial comment about the file
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the upload details
        """
        try:
            kwargs = {
                "channels": channel,
                "filename": filename
            }

            if file_path:
                kwargs["file"] = file_path
            elif file_content:
                kwargs["content"] = file_content

            if title:
                kwargs["title"] = title
            if initial_comment:
                kwargs["initial_comment"] = initial_comment

            response = self._run_async(self.client.files_upload(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in upload_file: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="add_reaction",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to add reaction to",
                required=True
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="Name of the emoji reaction (e.g., 'thumbsup', '+1')",
                required=True
            )
        ]
    )
    def add_reaction(self, channel: str, timestamp: str, name: str) -> Tuple[bool, str]:
        """Add a reaction to a message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to add reaction to
            name: Name of the emoji reaction
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the reaction details
        """
        try:
            response = self._run_async(self.client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=name
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in add_reaction: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="create_channel",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="Name of the channel to create",
                required=True
            ),
            ToolParameter(
                name="is_private",
                type=ParameterType.BOOLEAN,
                description="Whether the channel should be private",
                required=False
            ),
            ToolParameter(
                name="topic",
                type=ParameterType.STRING,
                description="Topic for the channel",
                required=False
            ),
            ToolParameter(
                name="purpose",
                type=ParameterType.STRING,
                description="Purpose of the channel",
                required=False
            )
        ]
    )
    def create_channel(self, name: str, is_private: Optional[bool] = None, topic: Optional[str] = None, purpose: Optional[str] = None) -> Tuple[bool, str]:
        """Create a new channel"""
        """
        Args:
            name: Name of the channel to create
            is_private: Whether the channel should be private
            topic: Topic for the channel
            purpose: Purpose of the channel
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel details
        """
        try:
            kwargs = {"name": name}

            if is_private is not None:
                kwargs["is_private"] = is_private

            response = self._run_async(self.client.conversations_create(**kwargs))
            slack_response = self._handle_slack_response(response)

            # Set topic and purpose if provided and channel was created successfully
            if slack_response.success and slack_response.data:
                channel_id = slack_response.data.get('channel', {}).get('id')

                if topic and channel_id:
                    try:
                        self._run_async(self.client.conversations_set_topic(channel=channel_id, topic=topic))
                    except Exception as e:
                        logger.warning(f"Failed to set topic: {e}")

                if purpose and channel_id:
                    try:
                        self._run_async(self.client.conversations_set_purpose(channel=channel_id, purpose=purpose))
                    except Exception as e:
                        logger.warning(f"Failed to set purpose: {e}")

            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in create_channel: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="invite_users_to_channel",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to invite users to",
                required=True
            ),
            ToolParameter(
                name="users",
                type=ParameterType.ARRAY,
                description="List of user IDs or emails to invite",
                required=True,
                items={"type": "string"}
            )
        ]
    )
    def invite_users_to_channel(self, channel: str, users: List[str]) -> Tuple[bool, str]:
        """Invite users to a channel"""
        """
        Args:
            channel: The channel to invite users to
            users: List of user IDs or emails to invite
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the invitation details
        """
        try:
            # Resolve user identifiers to user IDs
            user_ids = []
            for user in users:
                user_id = self._resolve_user_identifier(user)
                if user_id:
                    user_ids.append(user_id)
                else:
                    logger.warning(f"Could not resolve user: {user}")

            if not user_ids:
                return (False, SlackResponse(success=False, error="No valid users found to invite").to_json())

            response = self._run_async(self.client.conversations_invite(
                channel=channel,
                users=user_ids
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in invite_users_to_channel: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="search_messages",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True
            ),
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="Channel to search in (optional)",
                required=False
            ),
            ToolParameter(
                name="count",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            ),
            ToolParameter(
                name="sort",
                type=ParameterType.STRING,
                description="Sort order (timestamp, score)",
                required=False
            )
        ]
    )
    def search_messages(self, query: str, channel: Optional[str] = None, count: Optional[int] = None, sort: Optional[str] = None) -> Tuple[bool, str]:
        """Search for messages in Slack"""
        """
        Args:
            query: Search query
            channel: Channel to search in (optional)
            count: Maximum number of results to return
            sort: Sort order
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            # Build search query with channel filter if provided
            search_query = query
            if channel:
                # Remove # if present
                channel_name = channel[1:] if channel.startswith('#') else channel
                search_query = f"in:{channel_name} {query}"

            response = self._run_async(self.client.search_messages(
                query=search_query,
                count=count,
                sort=sort
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in search_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="set_user_status",
        parameters=[
            ToolParameter(
                name="status_text",
                type=ParameterType.STRING,
                description="Status text to set",
                required=True
            ),
            ToolParameter(
                name="status_emoji",
                type=ParameterType.STRING,
                description="Status emoji to set",
                required=False
            ),
            ToolParameter(
                name="expiration",
                type=ParameterType.STRING,
                description="Expiration time for the status (Unix timestamp)",
                required=False
            )
        ]
    )
    def set_user_status(self, status_text: str, status_emoji: Optional[str] = None, expiration: Optional[str] = None) -> Tuple[bool, str]:
        """Set user status"""
        """
        Args:
            status_text: Status text to set
            status_emoji: Status emoji to set
            expiration: Expiration time for the status
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the status details
        """
        try:
            profile = {"status_text": status_text}

            if status_emoji:
                profile["status_emoji"] = status_emoji

            kwargs = {"profile": profile}

            if expiration:
                kwargs["status_expiration"] = int(expiration)

            response = self._run_async(self.client.users_profile_set(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in set_user_status: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="schedule_message",
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
            ),
            ToolParameter(
                name="post_at",
                type=ParameterType.STRING,
                description="Unix timestamp for when to post the message",
                required=True
            )
        ]
    )
    def schedule_message(self, channel: str, message: str, post_at: str) -> Tuple[bool, str]:
        """Schedule a message to be sent at a specific time"""
        """
        Args:
            channel: The channel to send the message to
            message: The message to send
            post_at: Unix timestamp for when to post the message
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the scheduled message details
        """
        try:
            response = self._run_async(self.client.chat_schedule_message(
                channel=channel,
                text=message,
                post_at=int(post_at)
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in schedule_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="create_poll",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to post the poll in",
                required=True
            ),
            ToolParameter(
                name="question",
                type=ParameterType.STRING,
                description="The poll question",
                required=True
            ),
            ToolParameter(
                name="options",
                type=ParameterType.ARRAY,
                description="List of poll options",
                required=True,
                items={"type": "string"}
            )
        ]
    )
    def create_poll(self, channel: str, question: str, options: List[str]) -> Tuple[bool, str]:
        """Create an interactive poll in a channel"""
        """
        Args:
            channel: The channel to post the poll in
            question: The poll question
            options: List of poll options
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the poll details
        """
        try:
            # Create interactive blocks for the poll
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{question}*"
                    }
                },
                {
                    "type": "actions",
                    "elements": []
                }
            ]

            # Add buttons for each option
            for i, option in enumerate(options):
                blocks[1]["elements"].append({
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": option
                    },
                    "action_id": f"poll_option_{i}",
                    "value": option
                })

            response = self._run_async(self.client.chat_post_message(
                channel=channel,
                text=f"Poll: {question}",
                blocks=blocks
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in create_poll: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="archive_channel",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to archive",
                required=True
            )
        ]
    )
    def archive_channel(self, channel: str) -> Tuple[bool, str]:
        """Archive a channel"""
        """
        Args:
            channel: The channel to archive
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the archive details
        """
        try:
            response = self._run_async(self.client.conversations_archive(channel=channel))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in archive_channel: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="pin_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to pin",
                required=True
            )
        ]
    )
    def pin_message(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Pin a message to a channel"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to pin
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the pin details
        """
        try:
            response = self._run_async(self.client.pins_add(
                channel=channel,
                timestamp=timestamp
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in pin_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_unread_messages",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to check for unread messages",
                required=True
            )
        ]
    )
    def get_unread_messages(self, channel: str) -> Tuple[bool, str]:
        """Get unread messages from a channel"""
        """
        Args:
            channel: The channel to check for unread messages
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with unread messages
        """
        try:
            # Get channel info to check for unread count
            info_response = self._run_async(self.client.conversations_info(channel=channel))
            info_slack_response = self._handle_slack_response(info_response)

            if not info_slack_response.success:
                return (info_slack_response.success, info_slack_response.to_json())

            # Get recent messages
            history_response = self._run_async(self.client.conversations_history(channel=channel, limit=50))
            history_slack_response = self._handle_slack_response(history_response)

            if not history_slack_response.success:
                return (history_slack_response.success, history_slack_response.to_json())

            # Combine channel info with recent messages
            result = {
                "channel_info": info_slack_response.data,
                "recent_messages": history_slack_response.data.get('messages', []) if history_slack_response.data else []
            }

            return (True, SlackResponse(success=True, data=result).to_json())

        except Exception as e:
            logger.error(f"Error in get_unread_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_scheduled_messages",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get scheduled messages for",
                required=False
            )
        ]
    )
    def get_scheduled_messages(self, channel: Optional[str] = None) -> Tuple[bool, str]:
        """Get scheduled messages"""
        """
        Args:
            channel: The channel to get scheduled messages for (optional)
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with scheduled messages
        """
        try:
            kwargs = {}
            if channel:
                kwargs["channel"] = channel

            response = self._run_async(self.client.chat_scheduled_messages_list(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_scheduled_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_message_with_mentions",
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
                description="The message to send with mentions",
                required=True
            ),
            ToolParameter(
                name="mentions",
                type=ParameterType.ARRAY,
                description="List of users to mention",
                required=False,
                items={"type": "string"}
            )
        ]
    )
    def send_message_with_mentions(self, channel: str, message: str, mentions: Optional[List[str]] = None) -> Tuple[bool, str]:
        """Send a message with user mentions"""
        """
        Args:
            channel: The channel to send the message to
            message: The message to send with mentions
            mentions: List of users to mention
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            # Process mentions if provided
            if mentions:
                processed_message = message
                for mention in mentions:
                    # Resolve user identifier to user ID
                    user_id = self._resolve_user_identifier(mention)
                    if user_id:
                        processed_message = processed_message.replace(f"@{mention}", f"<@{user_id}>")

                response = self._run_async(self.client.chat_post_message(
                    channel=channel,
                    text=processed_message
                ))
            else:
                response = self._run_async(self.client.chat_post_message(
                    channel=channel,
                    text=message
                ))

            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in send_message_with_mentions: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_users_list",
        parameters=[
            ToolParameter(
                name="include_deleted",
                type=ParameterType.BOOLEAN,
                description="Include deleted users in the list",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of users to return",
                required=False
            )
        ]
    )
    def get_users_list(self, include_deleted: Optional[bool] = None, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get list of all users in the organization"""
        """
        Args:
            include_deleted: Include deleted users in the list
            limit: Maximum number of users to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the users list
        """
        try:
            kwargs = {}
            if include_deleted is not None:
                kwargs["include_deleted"] = include_deleted
            if limit:
                kwargs["limit"] = limit

            response = self._run_async(self.client.users_list(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_users_list: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_conversations",
        parameters=[
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="User ID to get conversations for",
                required=True
            ),
            ToolParameter(
                name="types",
                type=ParameterType.STRING,
                description="Comma-separated list of conversation types (public_channel, private_channel, mpim, im)",
                required=False
            ),
            ToolParameter(
                name="exclude_archived",
                type=ParameterType.BOOLEAN,
                description="Exclude archived conversations",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of conversations to return",
                required=False
            )
        ]
    )
    def get_user_conversations(self, user: str, types: Optional[str] = None, exclude_archived: Optional[bool] = None, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get conversations for a specific user"""
        """
        Args:
            user: User ID to get conversations for
            types: Comma-separated list of conversation types
            exclude_archived: Exclude archived conversations
            limit: Maximum number of conversations to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the conversations
        """
        try:
            kwargs = {"user": user}
            if types:
                kwargs["types"] = types
            if exclude_archived is not None:
                kwargs["exclude_archived"] = exclude_archived
            if limit:
                kwargs["limit"] = limit

            response = self._run_async(self.client.users_conversations(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_conversations: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_groups",
        parameters=[
            ToolParameter(
                name="include_users",
                type=ParameterType.BOOLEAN,
                description="Include users in each user group",
                required=False
            ),
            ToolParameter(
                name="include_disabled",
                type=ParameterType.BOOLEAN,
                description="Include disabled user groups",
                required=False
            )
        ]
    )
    def get_user_groups(self, include_users: Optional[bool] = None, include_disabled: Optional[bool] = None) -> Tuple[bool, str]:
        """Get list of user groups in the organization"""
        """
        Args:
            include_users: Include users in each user group
            include_disabled: Include disabled user groups
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the user groups
        """
        try:
            kwargs = {}
            if include_users is not None:
                kwargs["include_users"] = include_users
            if include_disabled is not None:
                kwargs["include_disabled"] = include_disabled

            response = self._run_async(self.client.usergroups_list(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_groups: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_group_info",
        parameters=[
            ToolParameter(
                name="usergroup",
                type=ParameterType.STRING,
                description="User group ID to get info for",
                required=True
            ),
            ToolParameter(
                name="include_disabled",
                type=ParameterType.BOOLEAN,
                description="Include disabled user groups",
                required=False
            )
        ]
    )
    def get_user_group_info(self, usergroup: str, include_disabled: Optional[bool] = None) -> Tuple[bool, str]:
        """Get information about a specific user group"""
        """
        Args:
            usergroup: User group ID to get info for
            include_disabled: Include disabled user groups
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the user group info
        """
        try:
            kwargs = {"usergroup": usergroup}
            if include_disabled is not None:
                kwargs["include_disabled"] = include_disabled

            response = self._run_async(self.client.usergroups_info(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_group_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_channels",
        parameters=[
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="User ID to get channels for",
                required=True
            ),
            ToolParameter(
                name="exclude_archived",
                type=ParameterType.BOOLEAN,
                description="Exclude archived channels",
                required=False
            ),
            ToolParameter(
                name="types",
                type=ParameterType.STRING,
                description="Comma-separated list of channel types (public_channel, private_channel)",
                required=False
            )
        ]
    )
    def get_user_channels(self, user: str, exclude_archived: Optional[bool] = None, types: Optional[str] = None) -> Tuple[bool, str]:
        """Get channels that a specific user is a member of"""
        """
        Args:
            user: User ID to get channels for
            exclude_archived: Exclude archived channels
            types: Comma-separated list of channel types
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channels
        """
        try:
            kwargs = {"user": user}
            if exclude_archived is not None:
                kwargs["exclude_archived"] = exclude_archived
            if types:
                kwargs["types"] = types

            response = self._run_async(self.client.users_conversations(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_channels: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="delete_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to delete",
                required=True
            ),
            ToolParameter(
                name="as_user",
                type=ParameterType.BOOLEAN,
                description="Delete the message as the authenticated user",
                required=False
            )
        ]
    )
    def delete_message(self, channel: str, timestamp: str, as_user: Optional[bool] = None) -> Tuple[bool, str]:
        """Delete a message from a channel"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to delete
            as_user: Delete the message as the authenticated user
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the deletion details
        """
        try:
            kwargs = {
                "channel": channel,
                "ts": timestamp
            }
            if as_user is not None:
                kwargs["as_user"] = as_user

            response = self._run_async(self.client.chat_delete(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in delete_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="update_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to update",
                required=True
            ),
            ToolParameter(
                name="text",
                type=ParameterType.STRING,
                description="New text content for the message",
                required=True
            ),
            ToolParameter(
                name="blocks",
                type=ParameterType.ARRAY,
                description="Rich message blocks for advanced formatting",
                required=False,
                items={"type": "object"}
            ),
            ToolParameter(
                name="as_user",
                type=ParameterType.BOOLEAN,
                description="Update the message as the authenticated user",
                required=False
            )
        ]
    )
    def update_message(self, channel: str, timestamp: str, text: str, blocks: Optional[List[Dict]] = None, as_user: Optional[bool] = None) -> Tuple[bool, str]:
        """Update an existing message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to update
            text: New text content for the message
            blocks: Rich message blocks for advanced formatting
            as_user: Update the message as the authenticated user
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the update details
        """
        try:
            kwargs = {
                "channel": channel,
                "ts": timestamp,
                "text": text
            }
            if blocks:
                kwargs["blocks"] = blocks
            if as_user is not None:
                kwargs["as_user"] = as_user

            response = self._run_async(self.client.chat_update(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in update_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_message_permalink",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to get permalink for",
                required=True
            )
        ]
    )
    def get_message_permalink(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Get a permalink for a specific message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to get permalink for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the permalink
        """
        try:
            response = self._run_async(self.client.chat_get_permalink(
                channel=channel,
                message_ts=timestamp
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_message_permalink: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_reactions",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to get reactions for",
                required=True
            ),
            ToolParameter(
                name="full",
                type=ParameterType.BOOLEAN,
                description="Return full reaction objects",
                required=False
            )
        ]
    )
    def get_reactions(self, channel: str, timestamp: str, full: Optional[bool] = None) -> Tuple[bool, str]:
        """Get reactions for a specific message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to get reactions for
            full: Return full reaction objects
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the reactions
        """
        try:
            kwargs = {
                "channel": channel,
                "timestamp": timestamp
            }
            if full is not None:
                kwargs["full"] = full

            response = self._run_async(self.client.reactions_get(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_reactions: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="remove_reaction",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to remove reaction from",
                required=True
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="Name of the emoji reaction to remove",
                required=True
            )
        ]
    )
    def remove_reaction(self, channel: str, timestamp: str, name: str) -> Tuple[bool, str]:
        """Remove a reaction from a message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to remove reaction from
            name: Name of the emoji reaction to remove
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the removal details
        """
        try:
            response = self._run_async(self.client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=name
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in remove_reaction: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_pinned_messages",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to get pinned messages from",
                required=True
            )
        ]
    )
    def get_pinned_messages(self, channel: str) -> Tuple[bool, str]:
        """Get pinned messages from a channel"""
        """
        Args:
            channel: The channel to get pinned messages from
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the pinned messages
        """
        try:
            response = self._run_async(self.client.pins_list(channel=channel))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_pinned_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="unpin_message",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the message",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the message to unpin",
                required=True
            )
        ]
    )
    def unpin_message(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Unpin a message from a channel"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to unpin
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the unpin details
        """
        try:
            response = self._run_async(self.client.pins_remove(
                channel=channel,
                timestamp=timestamp
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in unpin_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="rename_channel",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to rename",
                required=True
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="New name for the channel",
                required=True
            )
        ]
    )
    def rename_channel(self, channel: str, name: str) -> Tuple[bool, str]:
        """Rename a channel"""
        """
        Args:
            channel: The channel to rename
            name: New name for the channel
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the rename details
        """
        try:
            response = self._run_async(self.client.conversations_rename(
                channel=channel,
                name=name
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in rename_channel: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="set_channel_topic",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to set topic for",
                required=True
            ),
            ToolParameter(
                name="topic",
                type=ParameterType.STRING,
                description="New topic for the channel",
                required=True
            )
        ]
    )
    def set_channel_topic(self, channel: str, topic: str) -> Tuple[bool, str]:
        """Set the topic for a channel"""
        """
        Args:
            channel: The channel to set topic for
            topic: New topic for the channel
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the topic details
        """
        try:
            response = self._run_async(self.client.conversations_set_topic(
                channel=channel,
                topic=topic
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in set_channel_topic: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="set_channel_purpose",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to set purpose for",
                required=True
            ),
            ToolParameter(
                name="purpose",
                type=ParameterType.STRING,
                description="New purpose for the channel",
                required=True
            )
        ]
    )
    def set_channel_purpose(self, channel: str, purpose: str) -> Tuple[bool, str]:
        """Set the purpose for a channel"""
        """
        Args:
            channel: The channel to set purpose for
            purpose: New purpose for the channel
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the purpose details
        """
        try:
            response = self._run_async(self.client.conversations_set_purpose(
                channel=channel,
                purpose=purpose
            ))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in set_channel_purpose: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="mark_channel_read",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel to mark as read",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the last message read",
                required=False
            )
        ]
    )
    def mark_channel_read(self, channel: str, timestamp: Optional[str] = None) -> Tuple[bool, str]:
        """Mark a channel as read"""
        """
        Args:
            channel: The channel to mark as read
            timestamp: Timestamp of the last message read
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the mark details
        """
        try:
            kwargs = {"channel": channel}
            if timestamp:
                kwargs["ts"] = timestamp

            response = self._run_async(self.client.conversations_mark(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in mark_channel_read: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_thread_replies",
        parameters=[
            ToolParameter(
                name="channel",
                type=ParameterType.STRING,
                description="The channel containing the thread",
                required=True
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Timestamp of the parent message",
                required=True
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of replies to return",
                required=False
            )
        ]
    )
    def get_thread_replies(self, channel: str, timestamp: str, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get replies in a thread"""
        """
        Args:
            channel: The channel containing the thread
            timestamp: Timestamp of the parent message
            limit: Maximum number of replies to return
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the thread replies
        """
        try:
            kwargs = {
                "channel": channel,
                "ts": timestamp
            }
            if limit:
                kwargs["limit"] = limit

            response = self._run_async(self.client.conversations_replies(**kwargs))
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_thread_replies: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    def _resolve_user_identifier(self, user_identifier: str) -> Optional[str]:
        """Resolve user identifier (email, display name, or user ID) to user ID.

        Optimized for large workspaces:
        - Uses normalized name fields when available for more reliable matching
        - Early termination when user is found
        - Handles pagination efficiently
        """
        try:
            # If it's already a user ID (starts with U), return as is
            if user_identifier.startswith('U'):
                return user_identifier

            # Normalize the identifier for comparison (remove @ prefix if present)
            target_identifier = user_identifier.lstrip('@').casefold()

            # Try to find by email first (fastest, O(1) API call)
            if '@' in user_identifier:
                try:
                    response = self._run_async(self.client.users_lookup_by_email(email=user_identifier))
                    slack_response = self._handle_slack_response(response)
                    if slack_response.success and slack_response.data:
                        return slack_response.data.get('user', {}).get('id')
                except Exception:
                    pass

            # Try to find by display name or real name
            # Use pagination to search through all users, but stop early when found
            try:
                cursor = None
                while True:
                    # Request larger page size for fewer API calls (Slack max is typically 1000)
                    users_response = self._run_async(self.client.users_list(cursor=cursor, limit=1000))
                    users_slack_response = self._handle_slack_response(users_response)

                    if not users_slack_response.success or not users_slack_response.data:
                        break

                    users = users_slack_response.data.get('members', [])
                    if not users:
                        break

                    # Search through users in this page
                    for user in users:
                        profile = user.get('profile', {}) or {}

                        # Prefer normalized fields (more reliable), fallback to regular fields
                        # Check multiple name variations for better matching
                        names_to_match = [
                            profile.get('display_name_normalized'),
                            profile.get('real_name_normalized'),
                            profile.get('display_name'),
                            profile.get('real_name'),
                            user.get('name'),
                        ]

                        for name in names_to_match:
                            if isinstance(name, str) and name.casefold() == target_identifier:
                                # Early return when match is found
                                return user.get('id')

                    # Check for next page
                    response_metadata = users_slack_response.data.get('response_metadata', {})
                    next_cursor = response_metadata.get('next_cursor')
                    if not next_cursor:
                        # No more pages
                        break
                    cursor = next_cursor
            except Exception:
                pass

            return None

        except Exception as e:
            logger.error(f"Error resolving user identifier '{user_identifier}': {e}")
            return None
