import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

from app.agents.actions.response_transformer import ResponseTransformer
from app.agents.actions.slack.config import SlackResponse
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource

logger = logging.getLogger(__name__)

# Constants
MIN_SLACK_USER_ID_LENGTH = 9  # Minimum length for valid Slack user ID (starts with 'U')
MIN_PARTIAL_MATCH_LENGTH = 3  # Minimum length for partial name matching


class AmbiguousUserError(Exception):
    """Raised when multiple users match a given identifier"""
    def __init__(self, identifier: str, matches: List[Dict[str, Any]]) -> None:
        self.identifier = identifier
        self.matches = matches
        super().__init__(f"Multiple users found matching '{identifier}'. Please use email or user ID for disambiguation.")

# Pydantic schemas for Slack tools
class SendMessageInput(BaseModel):
    """Schema for sending a message"""
    channel: str = Field(description="The channel to send the message to")
    message: str = Field(description="The message to send. Must use Slack's mrkdwn format")


class GetChannelHistoryInput(BaseModel):
    """Schema for getting channel history"""
    channel: str = Field(description="The channel to get the history of")
    limit: Optional[int] = Field(default=None, description="Maximum number of messages to return")


class SearchAllInput(BaseModel):
    """Schema for searching in Slack"""
    query: str = Field(description="The search query to find messages, files, and channels")
    limit: Optional[int] = Field(default=None, description="Maximum number of results to return")


class GetUserInfoInput(BaseModel):
    """Schema for getting user info"""
    user: str = Field(description="Slack user identifier. Use email or Slack user ID (starts with 'U')")


class SendDirectMessageInput(BaseModel):
    """Schema for sending a direct message. Accepts both 'message' and 'text' fields (text is aliased to message)."""
    user: str = Field(description="User ID, email, or display name to send DM to")
    message: str = Field(description="The message to send")
    text: Optional[str] = Field(default=None, exclude=True, description="Alternative field name for message (alias) - will be converted to 'message'")

    @model_validator(mode='before')
    @classmethod
    def handle_text_alias(cls, data: Any) -> Any:  # noqa: ANN401
        """Handle both 'text' and 'message' fields - prefer 'message' but accept 'text' as fallback"""
        if isinstance(data, dict):
            # If 'text' is provided but 'message' is not, use 'text' as 'message'
            if 'text' in data and 'message' not in data:
                data['message'] = data['text']
            # If both are provided, prefer 'message'
            elif 'message' in data and 'text' in data:
                # Keep message, ignore text
                pass
        return data

class ReplyToMessageInput(BaseModel):
    """Schema for replying to a message"""
    channel: str = Field(description="The channel containing the message to reply to")
    message: str = Field(description="The reply message")
    thread_ts: Optional[str] = Field(default=None, description="Timestamp of the parent message to reply to")
    latest_message: Optional[bool] = Field(default=None, description="Whether to reply to the latest message in the channel")


class SendMessageToMultipleChannelsInput(BaseModel):
    """Schema for sending a message to multiple channels"""
    channels: List[str] = Field(description="List of channels to send the message to")
    message: str = Field(description="The message to send to all channels")

class CreateChannelInput(BaseModel):
    """Schema for creating a channel"""
    name: str = Field(description="Name of the channel to create")
    is_private: Optional[bool] = Field(default=None, description="Whether the channel should be private")
    topic: Optional[str] = Field(default=None, description="Topic for the channel")
    purpose: Optional[str] = Field(default=None, description="Purpose of the channel")


class UploadFileInput(BaseModel):
    """Schema for uploading a file"""
    channel: str = Field(description="The channel to upload the file to")
    filename: str = Field(description="Name of the file")
    file_path: Optional[str] = Field(default=None, description="Path to the file to upload")
    file_content: Optional[str] = Field(default=None, description="Content of the file to upload")
    title: Optional[str] = Field(default=None, description="Title of the file")
    initial_comment: Optional[str] = Field(default=None, description="Initial comment about the file")

class GetChannelInfoInput(BaseModel):
    """Schema for getting the info of a channel"""
    channel: str = Field(description="The channel to get the info of")

class GetChannelMembersInput(BaseModel):
    """Schema for getting the members of a channel"""
    channel: str = Field(description="The channel to get the members of")

class GetChannelMembersByIdInput(BaseModel):
    """Schema for getting the members of a channel by ID"""
    channel_id: str = Field(description="The channel ID to get the members of")

class ResolveUserInput(BaseModel):
    """Schema for resolving a user"""
    user_id: str = Field(description="The user ID to resolve")

class AddReactionInput(BaseModel):
    """Schema for adding a reaction to a message"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to add reaction to")
    name: str = Field(description="Name of the emoji reaction (e.g., 'thumbsup', '+1')")

class SearchMessagesInput(BaseModel):
    """Schema for searching messages"""
    query: str = Field(description="The search query to find messages")
    channel: Optional[str] = Field(default=None, description="The channel to search in")
    count: Optional[int] = Field(default=None, description="Maximum number of results to return")
    sort: Optional[str] = Field(default=None, description="Sort order (timestamp, score)")

class SetUserStatusInput(BaseModel):
    """Schema for setting user status"""
    status_text: str = Field(
        description="Status text to set. Pass a non-empty string to SET a status (e.g., 'In a meeting', 'Away'). "
                    "To CLEAR the status, pass an empty string: '' (you must also pass status_emoji='' to clear)."
    )
    status_emoji: Optional[str] = Field(
        default=None,
        description="OPTIONAL status emoji. Use standard Slack emoji names (with or without colons): "
                    "':calendar:' or 'calendar' for meetings, ':airplane:' or 'airplane' for travel, "
                    "':house:' or 'house' for working from home, ':palm_tree:' or 'palm_tree' for vacation. "
                    "If unsure or emoji not critical, OMIT this parameter entirely - status will work fine without it. "
                    "To CLEAR the status, pass an empty string: '' (you must also pass status_text='')."
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        description="Duration in seconds from NOW for how long the status should last. "
                    "Do NOT pass a Unix timestamp - pass only the number of seconds. "
                    "Examples: 1 hour = 3600, 30 minutes = 1800, 2 hours = 7200, 1 day = 86400. "
                    "The tool will compute the expiration time internally. "
                    "If not provided, the status will not expire. "
                    "Ignored when clearing status (pass empty strings for status_text and status_emoji)."
    )

class ScheduleMessageInput(BaseModel):
    """Schema for scheduling a message"""
    channel: str = Field(description="The channel to send the message to")
    message: str = Field(description="The message to send")
    post_at: str = Field(description="Unix timestamp for when to post the message")

class PinMessageInput(BaseModel):
    """Schema for pinning a message"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to pin")

class GetUnreadMessagesInput(BaseModel):
    """Schema for getting unread messages from a channel"""
    channel: str = Field(description="The channel to check for unread messages")

class GetScheduledMessagesInput(BaseModel):
    """Schema for getting scheduled messages"""
    channel: Optional[str] = Field(default=None, description="The channel to get scheduled messages for")

class SendMessageWithMentionsInput(BaseModel):
    """Schema for sending a message with user mentions"""
    channel: str = Field(description="The channel to send the message to")
    message: str = Field(description="The message to send with mentions")
    mentions: Optional[List[str]] = Field(default=None, description="List of users to mention")

class GetUsersListInput(BaseModel):
    """Schema for getting list of all users in the organization"""
    include_deleted: Optional[bool] = Field(default=None, description="Include deleted users in the list. Defaults to True (includes all users by default).")
    limit: Optional[int] = Field(default=None, description="Maximum number of users to return. If not specified, returns ALL users with pagination.")

class GetUserConversationsInput(BaseModel):
    """Schema for getting conversations for the authenticated user"""
    types: Optional[str] = Field(default=None, description="Comma-separated list of conversation types. Defaults to ALL types: 'public_channel,private_channel,mpim,im'. Only specify this if you want to filter to specific types.")
    exclude_archived: Optional[bool] = Field(default=None, description="Exclude archived conversations")
    limit: Optional[int] = Field(default=None, description="Maximum number of conversations to return. If not specified, returns ALL conversations with automatic pagination.")

class GetUserGroupsInput(BaseModel):
    """Schema for getting list of user groups in the organization"""
    include_users: Optional[bool] = Field(default=None, description="Include users in each user group")
    include_disabled: Optional[bool] = Field(default=None, description="Include disabled user groups")

class GetUserGroupInfoInput(BaseModel):
    """Schema for getting user group info"""
    usergroup: str = Field(description="User group ID to get info for")
    include_disabled: Optional[bool] = Field(default=None, description="Include disabled user groups")


class GetUserChannelsInput(BaseModel):
    """Schema for getting channels for the authenticated user"""
    exclude_archived: Optional[bool] = Field(default=None, description="Exclude archived channels")
    types: Optional[str] = Field(default=None, description="Comma-separated list of conversation types. Defaults to ALL types: 'public_channel,private_channel,mpim,im'. Only specify this if you want to filter to specific types. Returns ALL channels with automatic pagination.")


class DeleteMessageInput(BaseModel):
    """Schema for deleting a message"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to delete")
    as_user: Optional[bool] = Field(default=None, description="Delete the message as the authenticated user")


class UpdateMessageInput(BaseModel):
    """Schema for updating a message"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to update")
    text: str = Field(description="New text content for the message")
    blocks: Optional[List[Dict]] = Field(default=None, description="Rich message blocks for advanced formatting")
    as_user: Optional[bool] = Field(default=None, description="Update the message as the authenticated user")


class GetMessagePermalinkInput(BaseModel):
    """Schema for getting message permalink"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to get permalink for")


class GetReactionsInput(BaseModel):
    """Schema for getting reactions"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to get reactions for")
    full: Optional[bool] = Field(default=None, description="Return full reaction objects")


class RemoveReactionInput(BaseModel):
    """Schema for removing a reaction"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to remove reaction from")
    name: str = Field(description="Name of the emoji reaction to remove")


class GetPinnedMessagesInput(BaseModel):
    """Schema for getting pinned messages"""
    channel: str = Field(description="The channel to get pinned messages from")


class UnpinMessageInput(BaseModel):
    """Schema for unpinning a message"""
    channel: str = Field(description="The channel containing the message")
    timestamp: str = Field(description="Timestamp of the message to unpin")


class GetThreadRepliesInput(BaseModel):
    """Schema for getting thread replies"""
    channel: str = Field(description="The channel containing the thread")
    timestamp: str = Field(description="Timestamp of the parent message")
    limit: Optional[int] = Field(default=None, description="Maximum number of replies to return")

# Register Slack toolset
@ToolsetBuilder("Slack")\
    .in_group("Slack")\
    .with_description("Slack workspace integration for messaging, channels, file management, and collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Slack",
            authorize_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            redirect_uri="toolsets/oauth/callback/slack",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    # Messaging scopes
                    "chat:write",              # Send messages, update, delete, schedule

                    # Channel scopes
                    "channels:read",           # View basic channel info, list channels
                    "channels:history",        # Read channel message history
                    "channels:write",          # Create, archive, rename channels, set topic/purpose
                    "groups:read",             # View private channel info
                    "groups:history",          # Read private channel history
                    "groups:write",            # Create, archive, rename private channels
                    "mpim:read",               # View group DM info
                    "mpim:history",            # Read group DM history
                    "mpim:write",              # Create group DMs
                    "im:read",                 # View DM info
                    "im:history",              # Read DM history
                    "im:write",                # Open and send DMs (REQUIRED for send_direct_message)

                    # User scopes
                    "users:read",              # View user info, list users
                    "users:read.email",         # Look up users by email (REQUIRED for email-based user resolution)
                    "users.profile:read",       # Read user profiles
                    "users.profile:write",      # Write user profiles (REQUIRED for set_user_status)

                    # File scopes
                    "files:write",             # Upload files
                    "files:read",              # Read file info (if needed)

                    # Search scope
                    "search:read",             # Search messages and files

                    # Reaction scopes
                    "reactions:read",          # View reactions
                    "reactions:write",         # Add/remove reactions

                    # Pin scopes
                    "pins:read",               # View pinned messages
                    "pins:write",              # Pin/unpin messages

                    # User group scopes
                    "usergroups:read",         # View user groups (REQUIRED for get_user_groups, get_user_group_info)
        # Set DND status (not currently used)
                ]
            ),
            scope_parameter_name="user_scope",  # Slack uses user_scope for user scopes (agent scopes are user scopes)
            token_response_path="authed_user",  # Slack OAuth v2 returns user tokens in authed_user object
            fields=[
                CommonFields.client_id("Slack App Console"),
                CommonFields.client_secret("Slack App Console")
            ],
            icon_path="/assets/icons/connectors/slack.svg",
            app_group="Communication",
            app_description="Slack OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/slack.svg"))\
    .build_decorator()
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

    def _convert_markdown_to_slack_mrkdwn(self, text: str) -> str:
        """
        Convert standard markdown to Slack's mrkdwn format.

        Key conversions:
        - Headers (#, ##, ###) → *bold* text
        - Bold (**text**) → *text* (single asterisk)
        - Italic (*text* or _text_) → _text_ (underscore)
        - Strikethrough (~~text~~) → ~text~
        - Links ([text](url)) → <url|text>
        - Code blocks (```) → preserved but cleaned
        - Lists (- or *) → preserved
        - Quotes (>) → preserved

        Args:
            text: Standard markdown text

        Returns:
            Text converted to Slack mrkdwn format
        """
        if not text:
            return text

        result = text

        # First, protect code blocks, inline code, and citations from conversion
        code_blocks = []
        inline_codes = []
        citations = []

        # Extract and protect code blocks (triple backticks)
        def protect_code_block(match) -> str:
            idx = len(code_blocks)
            code_blocks.append(match.group(0))
            return f"__CODE_BLOCK_{idx}__"

        result = re.sub(r'```[\s\S]*?```', protect_code_block, result)

        # Extract and protect inline code (single backticks)
        def protect_inline_code(match) -> str:
            idx = len(inline_codes)
            inline_codes.append(match.group(0))
            return f"__INLINE_CODE_{idx}__"

        result = re.sub(r'`[^`\n]+`', protect_inline_code, result)

        # Protect citations like [R1-1], [R2-3], [1], [2] from being converted to links
        def protect_citation(match) -> str:
            idx = len(citations)
            citations.append(match.group(0))
            return f"__CITATION_{idx}__"

        # Match citations like [R1-1], [R2-3], [1], [2] (but not markdown links)
        result = re.sub(r'\[R?\d+-\d+\]|\[\d+\]', protect_citation, result)

        # Convert headers (# Header, ## Header, ### Header) to bold
        def replace_header(match) -> str:
            header_text = match.group(2).strip()
            return f"*{header_text}*\n"

        result = re.sub(r'^(\#{1,6})\s+(.+)$', replace_header, result, flags=re.MULTILINE)

        # Convert bold (**text** or __text__) to *text* (single asterisk)
        def replace_bold(match) -> str:
            content = match.group(1)
            return f"*{content}*"

        # Match **text** (double asterisk bold)
        result = re.sub(r'\*\*([^*\n]+)\*\*', replace_bold, result)
        # Match __text__ (double underscore bold) - but be careful not to match our placeholders
        result = re.sub(r'(?<!_)__(?!_)([^_\n]+)(?<!_)__(?!_)', replace_bold, result)

        # Convert strikethrough (~~text~~) → ~text~
        result = re.sub(r'~~([^~\n]+)~~', r'~\1~', result)

        # Convert links [text](url) to <url|text>
        # This should not match citations since we protected them
        def replace_link(match: re.Match[str]) -> str:
            link_text = match.group(1)
            url = match.group(2)
            # Only convert if it looks like a URL (starts with http:// or https://)
            if url.startswith(('http://', 'https://', 'mailto:')):
                return f"<{url}|{link_text}>"
            # Otherwise, might be a citation, leave as is
            return match.group(0)

        result = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', replace_link, result)

        # Normalize list markers - ensure consistent spacing
        # Slack supports both - and • for lists
        result = re.sub(r'^\s*[-*]\s+', r'• ', result, flags=re.MULTILINE)

        # Remove any remaining markdown header markers that weren't converted
        result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)

        # Restore protected citations
        for idx, citation in enumerate(citations):
            result = result.replace(f"__CITATION_{idx}__", citation)

        # Restore protected inline code
        for idx, code in enumerate(inline_codes):
            result = result.replace(f"__INLINE_CODE_{idx}__", code)

        # Restore protected code blocks
        for idx, code_block in enumerate(code_blocks):
            result = result.replace(f"__CODE_BLOCK_{idx}__", code_block)

        # Clean up excessive blank lines (more than 2 consecutive)
        result = re.sub(r'\n{3,}', '\n\n', result)

        # Trim trailing whitespace from lines
        result = '\n'.join(line.rstrip() for line in result.split('\n'))

        return result


    async def _get_authenticated_user_id(self) -> Optional[str]:
        """Get the authenticated user ID from the token.

        Returns:
            Slack user ID for the authenticated user, or None if it cannot be retrieved
        """
        try:
            response = await self.client.auth_test()
            auth_response = self._handle_slack_response(response)

            if auth_response.success and auth_response.data:
                user_id = auth_response.data.get('user_id')
                if user_id:
                    logger.debug(f"Retrieved authenticated user ID: {user_id}")
                    return user_id

            logger.error("Could not retrieve authenticated user ID from auth.test")
            return None
        except Exception as e:
            logger.error(f"Error getting authenticated user ID: {e}")
            return None

    async def _resolve_channel(self, channel: str) -> str:
        """Resolve a channel name (e.g., '#testing' or 'testing') to a channel ID (e.g., 'C1234567890').

        This method handles cases where the LLM provides channel names instead of IDs.
        If the channel is already an ID (matches Slack ID format), it returns it as-is.
        Otherwise, it looks up the channel by name from the conversations list.

        Slack channel IDs have the format:
        - Public channels: Start with 'C' followed by alphanumeric (e.g., 'C1234567890')
        - Private channels/Groups: Start with 'G' followed by alphanumeric (e.g., 'G1234567890')
        - Direct messages: Start with 'D' followed by alphanumeric (e.g., 'D1234567890')

        Args:
            channel: Channel name (with or without # prefix) or channel ID

        Returns:
            Channel ID (e.g., 'C1234567890') or original value if resolution fails
        """
        try:
            if not isinstance(channel, str):
                return channel

            # Remove # prefix if present
            name = channel[1:] if channel.startswith('#') else channel

            # Check if it's already a Slack channel ID format
            # Slack IDs start with C (public), G (private/group), or D (DM) followed by alphanumeric
            # Typically 10-11 characters, but can vary. Minimum reasonable length is 9.
            slack_id_pattern = re.compile(r'^[CGD][A-Z0-9]{8,}$', re.IGNORECASE)
            if slack_id_pattern.match(name):
                logger.debug(f"Channel '{channel}' is already a valid Slack ID")
                return channel

            # Try to find channel by name - include ALL conversation types and handle pagination
            logger.debug(f"Resolving channel name '{name}' to ID...")
            all_channels = []
            cursor = None

            # Fetch all pages of conversations the user has access to
            while True:
                kwargs = {
                    "types": "public_channel,private_channel,mpim,im",  # ALL types
                    "exclude_archived": False,  # Include archived too
                    "limit": 1000  # Max per page
                }
                if cursor:
                    kwargs["cursor"] = cursor

                clist = await self.client.conversations_list(**kwargs)
                cl_resp = self._handle_slack_response(clist)

                if not cl_resp.success or not cl_resp.data:
                    logger.warning(f"Failed to fetch conversation list: {cl_resp.error}")
                    break

                channels = cl_resp.data.get('channels', [])
                all_channels.extend(channels)

                # Check for next page
                response_metadata = cl_resp.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor
                logger.debug(f"Fetched {len(channels)} conversations, continuing pagination...")

            logger.debug(f"Fetched {len(all_channels)} total conversations for resolution")

            # Search for matching channel by name
            for c in all_channels:
                if isinstance(c, dict) and c.get('name') == name:
                    channel_id = c.get('id')
                    if channel_id:
                        logger.info(f"✅ Resolved channel '{name}' → {channel_id}")
                        return channel_id
                    break

            logger.warning(f"Could not resolve channel name '{name}' - not found in {len(all_channels)} accessible conversations")
        except Exception as e:
            # Log but don't fail - return original channel value
            logger.warning(f"Error resolving channel '{channel}': {e}")

        # Return original value if resolution fails
        logger.debug(f"Returning original channel value: '{channel}'")
        return channel

    @tool(
        app_name="slack",
        tool_name="send_message",
        description="Send a message to a Slack channel using mrkdwn format",
        args_schema=SendMessageInput,
        when_to_use=[
            "User wants to send a message to Slack",
            "User mentions 'Slack' + wants to send/post message",
            "User wants to notify someone in Slack",
            "User asks to 'post in Slack', 'send to Slack', 'message in Slack'"
        ],
        when_not_to_use=[
            "User wants to read/search messages (use get_channel_history or search_messages)",
            "User wants general information about Slack (use retrieval only if no Slack tools available)",
            "No Slack mention (use other communication tools)",
            "User asks 'what is Slack' or 'how does Slack work' (use retrieval for general knowledge)"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send a message to #general",
            "Post in Slack channel",
            "Notify the team in Slack"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description="Send a message to a Slack channel. Use this tool when user explicitly wants to send/post/write a message in Slack. The message will be automatically converted from standard markdown to Slack's mrkdwn format"
    )
    async def send_message(self, channel: str, message: str) -> Tuple[bool, str]:
        """Send a message to a channel using Slack's mrkdwn format.

        The message will be automatically converted from standard markdown to Slack's mrkdwn format.
        Supports standard markdown features which will be converted:
        - Headers (#, ##, ###) → *bold* text
        - Bold (**text**) → *text*
        - Italic (*text* or _text_) → _text_
        - Strikethrough (~~text~~) → ~text~
        - Links ([text](url)) → <url|text>
        - Code blocks and inline code are preserved
        - Lists (- or *) → • (bullet points)

        Args:
            channel: The channel to send the message to
            message: The message to send in Slack mrkdwn format (see docstring for formatting rules)
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the message details
        """
        try:
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Convert standard markdown to Slack mrkdwn format
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            # Use chat_post_message to support markdown formatting
            # mrkdwn=True enables Slack's markdown parsing (enabled by default, but explicit for clarity)
            response = await self.client.chat_post_message(
                channel=chan,
                text=slack_message,
                mrkdwn=True
            )
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
        description="Get message history from a Slack channel",
        args_schema=GetChannelHistoryInput,
        when_to_use=[
            "User wants to read messages from Slack channel",
            "User mentions 'Slack' + wants to see messages/history",
            "User asks for recent messages in a channel"
        ],
        when_not_to_use=[
            "User wants to send a message (use send_message)",
            "User wants info ABOUT Slack (use retrieval)",
            "No Slack mention (use other tools)"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me messages from #general",
            "Get Slack channel history",
            "What was said in the channel?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_channel_history(self, channel: str, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get the history of a channel"""
        """
        Args:
            channel: The channel to get the history of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the history details
        """
        try:
            # Resolve channel name like "#bugs" to channel ID
            chan = await self._resolve_channel(channel)

            # Use SlackDataSource method
            response = await self.client.conversations_history(
                channel=chan,
                limit=limit
            )
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
                        uresp = await self.client.users_info(user=uid)
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

                # Transform the enriched data to remove unnecessary fields
                transformed_data = (
                    ResponseTransformer(enriched)
                    .remove("ok", "*.blocks", "*.response_metadata",
                            # File fields to remove (keep URLs for user access)
                            "*.user_team", "*.editable", "*.mode", "*.is_external", "*.external_type",
                            "*.is_public", "*.public_url_shared", "*.display_as_bot", "*.username",
                            "*.media_display_type",
                            # Remove all thumbnail URLs and dimensions (not needed, just preview images)
                            "*.thumb_64", "*.thumb_80", "*.thumb_160", "*.thumb_360", "*.thumb_360_w",
                            "*.thumb_360_h", "*.thumb_480", "*.thumb_480_w", "*.thumb_480_h",
                            "*.thumb_720", "*.thumb_720_w", "*.thumb_720_h", "*.thumb_800",
                            "*.thumb_800_w", "*.thumb_800_h", "*.thumb_960", "*.thumb_960_w",
                            "*.thumb_960_h", "*.thumb_1024", "*.thumb_1024_w", "*.thumb_1024_h",
                            "*.original_w", "*.original_h", "*.thumb_tiny",
                            # Remove metadata flags
                            "*.is_starred", "*.skipped_shares", "*.has_rich_preview", "*.file_access")
                    .keep("messages", "id", "name", "text", "ts", "user", "channel", "team",
                          "display_name", "real_name", "email", "resolved_text", "mentions",
                          "thread_ts", "reply_count", "replies", "subscribed", "subtype",
                          "type", "attachments", "blocks", "files", "reactions", "pinned_to",
                          "permalink", "has_more", "next_cursor", "previous_cursor", "bot_profile",
                          "client_msg_id", "upload",
                          # Essential file fields to keep (including URLs for user access)
                          "created", "timestamp", "title", "mimetype", "filetype", "pretty_type",
                          "size", "url_private", "url_private_download", "permalink_public")
                    .clean()
                )

                return (True, SlackResponse(success=True, data=transformed_data).to_json())
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
        description="Get the info of a channel",
        args_schema=GetChannelInfoInput,
        when_to_use=[
            "User wants channel details/info",
            "User mentions 'Slack' + wants channel information",
            "User asks about a specific channel"
        ],
        when_not_to_use=[
            "User wants messages (use get_channel_history)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get info about #general channel",
            "What is the #engineering channel?",
            "Show channel details"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_channel_info(self, channel: str) -> Tuple[bool, str]:
        """Get the info of a channel"""
        """
        Args:
            channel: The channel to get the info of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel info
        """
        try:
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Use SlackDataSource method
            response = await self.client.conversations_info(channel=chan)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_channel_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_info",
        description="Get information about a Slack user",
        args_schema=GetUserInfoInput,
        when_to_use=[
            "User wants Slack user information",
            "User mentions 'Slack' + wants user details",
            "User asks about a Slack user"
        ],
        when_not_to_use=[
            "User wants to send message (use send_message)",
            "User wants messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get info about user@company.com in Slack",
            "Who is @username in Slack?",
            "Show Slack user details"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_info(self, user: str) -> Tuple[bool, str]:
        """Get the info of a user with transformed response for easy field extraction"""
        try:
            try:
                user_id = await self._resolve_user_identifier(user, allow_ambiguous=False)
            except AmbiguousUserError as e:
                # Build helpful error message
                matches_list = []
                for match in e.matches:
                    match_str = f"  - {match.get('real_name') or match.get('display_name') or match.get('name', 'Unknown')}"
                    if match.get('email'):
                        match_str += f" ({match['email']})"
                    match_str += f" [ID: {match.get('id', 'Unknown')}]"
                    matches_list.append(match_str)

                error_msg = (
                    f"Multiple users found matching '{user}'. Please use email or user ID for disambiguation.\n\n"
                    f"Matching users:\n" + "\n".join(matches_list) + "\n\n"
                    "Tip: Use the user's email address or Slack user ID to uniquely identify the user."
                )
                return (False, SlackResponse(success=False, error=error_msg).to_json())

            if not user_id:
                user_id = user
                logger.debug(f"Could not resolve user '{user}', trying as-is: {user_id}")

            response = await self.client.users_info(user=user_id)
            slack_response = self._handle_slack_response(response)

            # Transform response to have flat structure for easy placeholder extraction
            if slack_response.success and slack_response.data:
                data = slack_response.data if isinstance(slack_response.data, dict) else {}
                user_obj = data.get('user') or {}
                profile = user_obj.get('profile') or {}

                # Create flattened structure with commonly needed fields at top level
                transformed = {
                    'id': user_obj.get('id') or user_id,
                    'name': user_obj.get('name'),
                    'real_name': user_obj.get('real_name'),
                    'display_name': profile.get('display_name') or user_obj.get('name') or user_obj.get('real_name'),
                    'email': profile.get('email'),
                    'team_id': user_obj.get('team_id'),
                    'is_bot': user_obj.get('is_bot'),
                    'is_admin': user_obj.get('is_admin'),
                    'is_owner': user_obj.get('is_owner'),
                    'is_primary_owner': user_obj.get('is_primary_owner'),
                    'tz': user_obj.get('tz'),
                    'profile': profile,  # Keep full profile for advanced use cases
                    'raw_user': user_obj  # Keep raw data for completeness
                }
                return (True, SlackResponse(success=True, data=transformed).to_json())

            return (slack_response.success, slack_response.to_json())
        except AmbiguousUserError:
            raise
        except Exception as e:
            logger.error(f"Error in get_user_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())
    @tool(
        app_name="slack",
        tool_name="fetch_channels",
        description="Fetch all conversations in the workspace (public channels, private channels, DMs, group DMs)",
        when_to_use=[
            "User wants to list all Slack channels/conversations",
            "User mentions 'Slack' + wants to see channels/DMs",
            "User asks for available channels/conversations"
        ],
        when_not_to_use=[
            "User wants channel info (use get_channel_info)",
            "User wants messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Slack channels",
            "Show me available channels",
            "What channels are in Slack?",
            "Show all conversations"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def fetch_channels(self) -> Tuple[bool, str]:
        """Fetch all conversations (public channels, private channels, DMs, group DMs) with pagination"""
        """
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with all conversations
        """
        try:
            # Fetch ALL conversation types with pagination
            all_conversations = []
            cursor = None

            while True:
                kwargs = {
                    "types": "public_channel,private_channel,mpim,im",
                    "exclude_archived": False,
                    "limit": 1000
                }
                if cursor:
                    kwargs["cursor"] = cursor

                response = await self.client.conversations_list(**kwargs)
                slack_response = self._handle_slack_response(response)

                if not slack_response.success or not slack_response.data:
                    # If first page fails, return error
                    if not all_conversations:
                        return (slack_response.success, slack_response.to_json())
                    # If subsequent page fails, return what we have
                    break

                conversations = slack_response.data.get('channels', [])
                all_conversations.extend(conversations)

                # Check for next page
                response_metadata = slack_response.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor
                logger.debug(f"Fetched {len(conversations)} conversations, continuing pagination...")

            logger.info(f"✅ Fetched total {len(all_conversations)} conversations (all types)")
            return (True, SlackResponse(success=True, data={"channels": all_conversations, "count": len(all_conversations)}).to_json())

        except Exception as e:
            logger.error(f"Error in fetch_channels: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="search_all",
        description="Search messages, files, and channels in Slack",
        args_schema=SearchAllInput,
        when_to_use=[
            "User wants to search across Slack (messages/files/channels)",
            "User mentions 'Slack' + wants to search",
            "User asks to find something in Slack"
        ],
        when_not_to_use=[
            "User wants to search only messages (use search_messages)",
            "User wants info ABOUT Slack (use retrieval)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search Slack for 'project update'",
            "Find messages about X in Slack",
            "Search all Slack content"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def search_all(self, query: str, limit: Optional[int] = None) -> Tuple[bool, str]:
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
            response = await self.client.search_messages(
                query=query,
                count=limit
            )
            transformed_response = (
                ResponseTransformer(response)
                .remove(
                        # Remove all thumbnail URLs and dimensions (not needed, just preview images)
                        "*.thumb_64", "*.thumb_80", "*.thumb_160", "*.thumb_360", "*.thumb_360_w",
                        "*.thumb_360_h", "*.thumb_480", "*.thumb_480_w", "*.thumb_480_h",
                        "*.thumb_720", "*.thumb_720_w", "*.thumb_720_h", "*.thumb_800",
                        "*.thumb_800_w", "*.thumb_800_h", "*.thumb_960", "*.thumb_960_w",
                        "*.thumb_960_h", "*.thumb_1024", "*.thumb_1024_w", "*.thumb_1024_h",
                        "*.original_w", "*.original_h", "*.thumb_tiny",
                        )
                    .clean()
                )

            slack_response = self._handle_slack_response(transformed_response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in search_all: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_channel_members",
        description="Get the members of a channel",
        args_schema=GetChannelMembersInput,
        when_to_use=[
            "User wants to see who is in a Slack channel",
            "User mentions 'Slack' + wants channel members",
            "User asks about channel participants"
        ],
        when_not_to_use=[
            "User wants messages (use get_channel_history)",
            "User wants channel info (use get_channel_info)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Who is in #general channel?",
            "Show members of Slack channel",
            "List channel participants"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_channel_members(self, channel: str) -> Tuple[bool, str]:
        """Get the members of a channel"""
        """
        Args:
            channel: The channel to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Use SlackDataSource method
            response = await self.client.conversations_members(channel=chan)
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
        description="Get the members of a channel by ID",
        args_schema=GetChannelMembersByIdInput,
        when_to_use=[
            "User has channel ID and wants members",
            "User mentions 'Slack' + has channel ID",
            "Programmatic access with known channel ID"
        ],
        when_not_to_use=[
            "User has channel name (use get_channel_members)",
            "User wants messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get members of channel C123456",
            "Who is in channel by ID?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_channel_members_by_id(self, channel_id: str) -> Tuple[bool, str]:
        """Get the members of a channel by ID"""
        """
        Args:
            channel_id: The channel ID to get the members of
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channel members
        """
        try:
            # Use SlackDataSource method
            response = await self.client.conversations_members(channel=channel_id)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_channel_members_by_id: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="resolve_user",
        description="Resolve a Slack user ID to display name and email",
        args_schema=ResolveUserInput,
        when_to_use=[
            "User has Slack user ID and wants name/email",
            "User mentions 'Slack' + has user ID",
            "Programmatic access with known user ID"
        ],
        when_not_to_use=[
            "User has email/name (use get_user_info)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Resolve user ID U123456",
            "Get name for Slack user ID"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def resolve_user(self, user_id: str) -> Tuple[bool, str]:
        """Resolve a Slack user ID to display name and email"""
        try:
            response = await self.client.users_info(user=user_id)
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
        tool_name="check_token_info",
        description="Check Slack token information and available scopes",
        when_to_use=[
            "User wants to verify Slack connection/permissions",
            "Debugging token/scope issues",
            "Checking Slack authentication status"
        ],
        when_not_to_use=[
            "User wants to use Slack features (use other tools)",
            "Normal Slack operations",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.UTILITY,
        typical_queries=[
            "Check Slack token",
            "Verify Slack permissions",
            "What Slack scopes do I have?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def check_token_info(self) -> Tuple[bool, str]:
        """Check Slack token information and available scopes"""
        """
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with token information
        """
        try:
            # Use SlackDataSource method
            response = await self.client.check_token_scopes()
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in check_token_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_direct_message",
        description="Send a direct message to a user",
        args_schema=SendDirectMessageInput,
        when_to_use=[
            "User wants to send DM to a specific person",
            "User mentions 'Slack' + wants to DM someone",
            "User asks to message someone directly"
        ],
        when_not_to_use=[
            "User wants to send to channel (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send a DM to user@company.com",
            "Message @username in Slack",
            "Send direct message to someone"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def send_direct_message(self, user: str, message: str) -> Tuple[bool, str]:
        """Send a direct message to a user"""
        try:
            # Try to resolve the user (don't allow ambiguous matches)
            try:
                user_id = await self._resolve_user_identifier(user, allow_ambiguous=False)
            except AmbiguousUserError as e:
                # Build helpful error message with matching users
                matches_list = []
                for match in e.matches:
                    match_str = f"  - {match.get('real_name') or match.get('display_name') or match.get('name', 'Unknown')}"
                    if match.get('email'):
                        match_str += f" ({match['email']})"
                    match_str += f" [ID: {match.get('id', 'Unknown')}]"
                    matches_list.append(match_str)

                error_msg = (
                    f"Multiple users found matching '{user}'. Please use email or user ID for disambiguation.\n\n"
                    f"Matching users:\n" + "\n".join(matches_list) + "\n\n"
                    "Tip: Use the user's email address (e.g., 'user@example.com') or Slack user ID (e.g., 'U1234567890') "
                    "to uniquely identify the user."
                )
                return (False, SlackResponse(success=False, error=error_msg).to_json())

            if not user_id:
                return (False, SlackResponse(success=False, error=f"User '{user}' not found. Please use email address or Slack user ID.").to_json())

            # Open DM conversation
            response = await self.client.conversations_open(users=[user_id])
            slack_response = self._handle_slack_response(response)

            if not slack_response.success:
                return (slack_response.success, slack_response.to_json())

            # Get channel ID
            channel_id = slack_response.data.get('channel', {}).get('id') if slack_response.data else None
            if not channel_id:
                return (False, SlackResponse(success=False, error="Failed to get DM channel ID").to_json())

            # Convert markdown and send
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            message_response = await self.client.chat_post_message(
                channel=channel_id,
                text=slack_message,
                mrkdwn=True
            )
            message_slack_response = self._handle_slack_response(message_response)
            return (message_slack_response.success, message_slack_response.to_json())

        except AmbiguousUserError:
            raise  # Re-raise to be handled by the outer try-except
        except Exception as e:
            logger.error(f"Error in send_direct_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())


    @tool(
        app_name="slack",
        tool_name="reply_to_message",
        description="Reply to a specific message in a channel",
        args_schema=ReplyToMessageInput,
        when_to_use=[
            "User wants to reply to a specific message",
            "User mentions 'Slack' + wants to reply",
            "User asks to respond to a message"
        ],
        when_not_to_use=[
            "User wants to send new message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Reply to message in #general",
            "Respond to a Slack message",
            "Reply to latest message"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def reply_to_message(self, channel: str, message: str, thread_ts: Optional[str] = None, latest_message: Optional[bool] = None) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # If latest_message is True, get the latest message timestamp
            if latest_message and not thread_ts:
                history_response = await self.client.conversations_history(channel=chan, limit=1)
                history_slack_response = self._handle_slack_response(history_response)

                if not history_slack_response.success or not history_slack_response.data:
                    return (False, SlackResponse(success=False, error="Failed to get latest message").to_json())

                messages = history_slack_response.data.get('messages', [])
                if not messages:
                    return (False, SlackResponse(success=False, error="No messages found in channel").to_json())

                thread_ts = messages[0].get('ts')

            if not thread_ts:
                return (False, SlackResponse(success=False, error="No thread timestamp provided").to_json())

            # Convert standard markdown to Slack mrkdwn format
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            # Send reply
            response = await self.client.chat_post_message(
                channel=chan,
                text=slack_message,
                thread_ts=thread_ts,
                mrkdwn=True
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in reply_to_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_message_to_multiple_channels",
        description="Send a message to multiple channels",
        args_schema=SendMessageToMultipleChannelsInput,
        when_to_use=[
            "User wants to send same message to multiple channels",
            "User mentions 'Slack' + wants to broadcast",
            "User asks to post to several channels"
        ],
        when_not_to_use=[
            "User wants to send to one channel (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send message to #general and #engineering",
            "Broadcast to multiple Slack channels",
            "Post to several channels"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def send_message_to_multiple_channels(self, channels: List[str], message: str) -> Tuple[bool, str]:
        """Send a message to multiple channels. Message will be auto-converted from standard markdown to Slack mrkdwn."""
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

            # Convert standard markdown to Slack mrkdwn format once for all channels
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            for channel in channels:
                try:
                    # Resolve channel name to channel ID if needed
                    chan = await self._resolve_channel(channel)

                    response = await self.client.chat_post_message(
                        channel=chan,
                        text=slack_message,
                        mrkdwn=True
                    )
                    slack_response = self._handle_slack_response(response)
                    results.append({
                        "channel": channel,
                        "channel_id": chan,
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
        tool_name="add_reaction",
        description="Add a reaction to a message",
        args_schema=AddReactionInput,
        when_to_use=[
            "User wants to add emoji reaction to message",
            "User mentions 'Slack' + wants to react",
            "User asks to react to a message"
        ],
        when_not_to_use=[
            "User wants to send message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add thumbs up to message",
            "React with :+1: to message",
            "Add reaction in Slack"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def add_reaction(self, channel: str, timestamp: str, name: str) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            response = await self.client.reactions_add(
                channel=chan,
                timestamp=timestamp,
                name=name
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in add_reaction: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="search_messages",
        description="Search for messages in Slack",
        args_schema=SearchMessagesInput,
        when_to_use=[
            "User wants to search for specific messages",
            "User mentions 'Slack' + wants to find messages",
            "User asks to search messages by content"
        ],
        when_not_to_use=[
            "User wants to read channel history (use get_channel_history)",
            "User wants to search all content (use search_all)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for messages about 'project'",
            "Find messages in #general about X",
            "Search Slack messages"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def search_messages(self, query: str, channel: Optional[str] = None, count: Optional[int] = None, sort: Optional[str] = None) -> Tuple[bool, str]:
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

            response = await self.client.search_messages(
                query=search_query,
                count=count,
                sort=sort
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in search_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="set_user_status",
        description="Set or clear user status",
        args_schema=SetUserStatusInput,
        when_to_use=[
            "User wants to set/update their Slack status",
            "User wants to clear/remove their Slack status",
            "User mentions 'Slack' + wants to change/clear status",
            "User asks to update status or make it active/available again",
            "User wants to set status with expiration/duration"
        ],
        when_not_to_use=[
            "User wants to send message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Set my Slack status to 'In a meeting'",
            "Update my status in Slack",
            "Change Slack status",
            "Set status to Away for 1 hour",
            "Clear my Slack status",
            "Remove my status",
            "Set status to active"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description="Set, update, or CLEAR the user's Slack status. To SET a status: pass status_text with the desired text. To CLEAR the status (make user appear active/available): pass status_text='' (empty string) AND status_emoji='' (empty string). Pass duration_seconds as the number of seconds from NOW (e.g., 3600 for 1 hour, 1800 for 30 min). Do NOT pass a Unix timestamp - just the duration in seconds. The status_emoji parameter is OPTIONAL when setting a status - if you're not sure which emoji to use or the user didn't specify one, simply omit it. Common emojis: calendar (meetings), airplane (travel), house (WFH), palm_tree (vacation). The tool calculates the expiration time internally. No calculator or other tool is needed."
    )
    async def set_user_status(self, status_text: str, status_emoji: Optional[str] = None, duration_seconds: Optional[int] = None) -> Tuple[bool, str]:
        """Set user status in Slack.
        Args:
            status_text: Status text to set (e.g., "In a meeting", "Away", "agent testing").
                        To CLEAR the status, pass an empty string "" for this parameter.
            status_emoji: Optional emoji for the status (e.g., ":away:", ":clock1:", ":meeting:").
                         To CLEAR the status, pass an empty string "" for this parameter too.
            duration_seconds: Optional number of seconds from NOW for the status to last.
                      Examples: 1 hour = 3600, 30 minutes = 1800, 2 hours = 7200.
                      The tool computes the Unix expiration timestamp internally.
                      Do NOT pass a Unix timestamp here - just the duration in seconds.

        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the status details

        Example (single tool call - no calculator needed):
        ```json
        {"name": "slack.set_user_status", "args": {"status_text": "Away", "status_emoji": ":away:", "duration_seconds": 3600}}
        ```

        Example (clear status):
        ```json
        {"name": "slack.set_user_status", "args": {"status_text": "", "status_emoji": ""}}
        ```
        """
        import time as _time
        try:
            # Check if this is a "clear status" request (empty text)
            is_clearing = not status_text or status_text.strip() == ""

            if is_clearing:
                # To clear a Slack status, you MUST set both text and emoji to empty strings
                logger.debug("Clearing Slack status (setting both text and emoji to empty)")
                profile = {
                    "status_text": "",
                    "status_emoji": ""
                }
                # Expiration should be 0 when clearing
                kwargs = {"profile": profile, "status_expiration": 0}
            else:
                # Setting a status
                profile = {"status_text": status_text}

                # Validate and normalize emoji format if provided
                if status_emoji:
                    # Ensure emoji has colons - Slack API expects :emoji_name: format
                    normalized_emoji = status_emoji.strip()
                    if normalized_emoji and not normalized_emoji.startswith(':'):
                        normalized_emoji = f":{normalized_emoji}"
                    if normalized_emoji and not normalized_emoji.endswith(':'):
                        normalized_emoji = f"{normalized_emoji}:"

                    # Only add emoji if it looks valid (has both colons and content between them)
                    # Minimum valid emoji is ":x:" (3 chars with 2 colons)
                    MIN_EMOJI_LENGTH = 3
                    MIN_COLON_COUNT = 2
                    if normalized_emoji and len(normalized_emoji) >= MIN_EMOJI_LENGTH and normalized_emoji.count(':') >= MIN_COLON_COUNT:
                        profile["status_emoji"] = normalized_emoji
                        logger.debug(f"Setting status emoji: {normalized_emoji}")
                    else:
                        logger.warning(f"Invalid emoji format '{status_emoji}', skipping emoji")

                kwargs = {"profile": profile}

                if duration_seconds is not None and duration_seconds > 0:
                    expiration_ts = int(_time.time()) + duration_seconds
                    kwargs["status_expiration"] = expiration_ts
                    logger.debug(f"Status will expire in {duration_seconds} seconds (at {expiration_ts})")

            response = await self.client.users_profile_set(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in set_user_status: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="schedule_message",
        description="Schedule a message to be sent at a specific time",
        args_schema=ScheduleMessageInput,
        when_to_use=[
            "User wants to schedule message for later",
            "User mentions 'Slack' + wants to schedule",
            "User asks to send message at specific time"
        ],
        when_not_to_use=[
            "User wants to send now (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Schedule message for tomorrow",
            "Send message at 3pm in Slack",
            "Schedule Slack message"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def schedule_message(self, channel: str, message: str, post_at: str) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Convert standard markdown to Slack mrkdwn format
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            response = await self.client.chat_schedule_message(
                channel=chan,
                text=slack_message,
                post_at=int(post_at)
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in schedule_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="pin_message",
        description="Pin a message to a channel",
        args_schema=PinMessageInput,
        when_to_use=[
            "User wants to pin a message in channel",
            "User mentions 'Slack' + wants to pin message",
            "User asks to pin a message"
        ],
        when_not_to_use=[
            "User wants to send message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Pin this message in #general",
            "Pin a message in Slack",
            "Make message pinned"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def pin_message(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Pin a message to a channel"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to pin
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the pin details
        """
        try:
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            response = await self.client.pins_add(
                channel=chan,
                timestamp=timestamp
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in pin_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_unread_messages",
        description="Get unread messages from a channel",
        args_schema=GetUnreadMessagesInput,
        when_to_use=[
            "User wants to see unread/new messages",
            "User mentions 'Slack' + wants unread messages",
            "User asks for new messages in channel"
        ],
        when_not_to_use=[
            "User wants all messages (use get_channel_history)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show unread messages in #general",
            "Get new messages from Slack",
            "What's unread in channel?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_unread_messages(self, channel: str) -> Tuple[bool, str]:
        """Get unread messages from a channel"""
        """
        Args:
            channel: The channel to check for unread messages
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with unread messages
        """
        try:
            # Get channel info to check for unread count
            info_response = await self.client.conversations_info(channel=channel)
            info_slack_response = self._handle_slack_response(info_response)

            if not info_slack_response.success:
                return (info_slack_response.success, info_slack_response.to_json())

            # Get recent messages
            history_response = await self.client.conversations_history(channel=channel, limit=50)
            history_slack_response = self._handle_slack_response(history_response)

            if not history_slack_response.success:
                return (history_slack_response.success, history_slack_response.to_json())

            # Combine channel info with recent messages
            result = {
                "channel_info": info_slack_response.data,
                "recent_messages": history_slack_response.data.get('messages', []) if history_slack_response.data else []
            }

            # Transform the result to remove unnecessary fields
            transformed_result = (
                ResponseTransformer(result)
                .remove(
                        # Remove all thumbnail URLs and dimensions (not needed, just preview images)
                        "*.thumb_64", "*.thumb_80", "*.thumb_160", "*.thumb_360", "*.thumb_360_w",
                        "*.thumb_360_h", "*.thumb_480", "*.thumb_480_w", "*.thumb_480_h",
                        "*.thumb_720", "*.thumb_720_w", "*.thumb_720_h", "*.thumb_800",
                        "*.thumb_800_w", "*.thumb_800_h", "*.thumb_960", "*.thumb_960_w",
                        "*.thumb_960_h", "*.thumb_1024", "*.thumb_1024_w", "*.thumb_1024_h",
                        "*.original_w", "*.original_h", "*.thumb_tiny",
                    )
                .clean()
            )

            return (True, SlackResponse(success=True, data=transformed_result).to_json())

        except Exception as e:
            logger.error(f"Error in get_unread_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_scheduled_messages",
        description="Get scheduled messages",
        args_schema=GetScheduledMessagesInput,
        when_to_use=[
            "User wants to see scheduled/pending messages",
            "User mentions 'Slack' + wants scheduled messages",
            "User asks for pending scheduled messages"
        ],
        when_not_to_use=[
            "User wants to schedule message (use schedule_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show scheduled messages",
            "What messages are scheduled in Slack?",
            "List pending scheduled messages"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_scheduled_messages(self, channel: Optional[str] = None) -> Tuple[bool, str]:
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

            response = await self.client.chat_scheduled_messages_list(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_scheduled_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="send_message_with_mentions",
        description="Send a message with user mentions",
        args_schema=SendMessageWithMentionsInput,
        when_to_use=[
            "User wants to send message and mention users",
            "User mentions 'Slack' + wants to @mention people",
            "User asks to notify users in message"
        ],
        when_not_to_use=[
            "User wants simple message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send message and mention @user1 and @user2",
            "Notify team in Slack message",
            "Send message with mentions"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def send_message_with_mentions(self, channel: str, message: str, mentions: Optional[List[str]] = None) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Process mentions if provided
            processed_message = message
            if mentions:
                for mention in mentions:
                    # Resolve user identifier to user ID
                    try:
                        user_id = await self._resolve_user_identifier(mention, allow_ambiguous=False)
                        if user_id:
                            processed_message = processed_message.replace(f"@{mention}", f"<@{user_id}>")
                        else:
                            logger.warning(f"Could not resolve mention: {mention}")
                    except AmbiguousUserError as e:
                        logger.warning(f"Ambiguous mention '{mention}': {len(e.matches)} matches found")
                        # For mentions, we'll skip ambiguous ones rather than failing the whole message

            # Convert standard markdown to Slack mrkdwn format
            slack_message = self._convert_markdown_to_slack_mrkdwn(processed_message)

            response = await self.client.chat_post_message(
                channel=chan,
                text=slack_message,
                mrkdwn=True
            )

            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())

        except Exception as e:
            logger.error(f"Error in send_message_with_mentions: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_users_list",
        description="Get list of all users in the organization",
        args_schema=GetUsersListInput,
        when_to_use=[
            "User wants to list all Slack users",
            "User mentions 'Slack' + wants user list",
            "User asks for all users in workspace"
        ],
        when_not_to_use=[
            "User wants specific user info (use get_user_info)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Slack users",
            "Show me users in workspace",
            "Get all Slack users"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_users_list(self, include_deleted: Optional[bool] = None, limit: Optional[int] = None) -> Tuple[bool, str]:
        """Get list of all users in the organization with pagination"""
        """
        Args:
            include_deleted: Include deleted users in the list (defaults to True to get all users)
            limit: Maximum number of users to return (if None, returns all users with pagination)
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the users list
        """
        try:
            # Default to include all users (including deleted)
            if include_deleted is None:
                include_deleted = True

            # If limit is specified, do a single fetch
            if limit:
                kwargs = {
                    "include_deleted": include_deleted,
                    "limit": limit
                }
                response = await self.client.users_list(**kwargs)
                slack_response = self._handle_slack_response(response)
                return (slack_response.success, slack_response.to_json())

            # Otherwise, fetch all users with pagination
            all_users = []
            cursor = None

            while True:
                kwargs = {
                    "include_deleted": include_deleted,
                    "limit": 1000
                }
                if cursor:
                    kwargs["cursor"] = cursor

                response = await self.client.users_list(**kwargs)
                slack_response = self._handle_slack_response(response)

                if not slack_response.success or not slack_response.data:
                    # If first page fails, return error
                    if not all_users:
                        return (slack_response.success, slack_response.to_json())
                    # If subsequent page fails, return what we have
                    break

                users = slack_response.data.get('members', [])
                all_users.extend(users)

                # Check for next page
                response_metadata = slack_response.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor
                logger.debug(f"Fetched {len(users)} users, continuing pagination...")

            logger.info(f"✅ Fetched total {len(all_users)} users")
            return (True, SlackResponse(success=True, data={"members": all_users, "count": len(all_users)}).to_json())

        except Exception as e:
            logger.error(f"Error in get_users_list: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_conversations",
        description="Get ALL conversations for the authenticated user (public channels, private channels, DMs, group DMs)",
        args_schema=GetUserConversationsInput,
        when_to_use=[
            "User wants to see their own conversations/channels/DMs",
            "User mentions 'Slack' + wants their conversations",
            "User asks 'what channels am I in?', 'show my conversations'"
        ],
        when_not_to_use=[
            "User wants info about another user (use get_user_info)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What channels am I in?",
            "Show my Slack conversations",
            "List my channels and DMs",
            "Show all my conversations"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_conversations(self, types: Optional[str] = None, exclude_archived: Optional[bool] = None, limit: Optional[int] = None) -> Tuple[bool, str]:
        """
        Args:
            types: Comma-separated list of conversation types (defaults to all: public_channel,private_channel,mpim,im)
            exclude_archived: Exclude archived conversations
            limit: Maximum number of conversations to return (if None, returns all with pagination)
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the conversations
        """
        try:
            # Get authenticated user ID from token
            user_id = await self._get_authenticated_user_id()
            if not user_id:
                return (False, SlackResponse(success=False, error="Could not determine authenticated user ID from token").to_json())


            # Default to ALL conversation types if not specified
            conversation_types = types if types else "public_channel,private_channel,mpim,im"

            # If limit is specified, do a single fetch
            if limit:
                kwargs = {
                    "user": user_id,
                    "types": conversation_types
                }
                if exclude_archived is not None:
                    kwargs["exclude_archived"] = exclude_archived
                kwargs["limit"] = limit

                response = await self.client.users_conversations(**kwargs)
                slack_response = self._handle_slack_response(response)
                return (slack_response.success, slack_response.to_json())

            # Otherwise, fetch all conversations with pagination
            all_conversations = []
            cursor = None

            while True:
                kwargs = {
                    "user": user_id,
                    "types": conversation_types,
                    "limit": 1000
                }
                if exclude_archived is not None:
                    kwargs["exclude_archived"] = exclude_archived
                if cursor:
                    kwargs["cursor"] = cursor

                response = await self.client.users_conversations(**kwargs)
                slack_response = self._handle_slack_response(response)

                if not slack_response.success or not slack_response.data:
                    # If first page fails, return error
                    if not all_conversations:
                        return (slack_response.success, slack_response.to_json())
                    # If subsequent page fails, return what we have
                    break

                conversations = slack_response.data.get('channels', [])
                all_conversations.extend(conversations)

                # Check for next page
                response_metadata = slack_response.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor
                logger.debug(f"Fetched {len(conversations)} conversations for user, continuing pagination...")

            logger.info(f"✅ Fetched total {len(all_conversations)} conversations for authenticated user")
            return (True, SlackResponse(success=True, data={"channels": all_conversations, "count": len(all_conversations)}).to_json())

        except Exception as e:
            logger.error(f"Error in get_user_conversations: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_groups",
        description="Get list of user groups in the organization",
        args_schema=GetUserGroupsInput,
        when_to_use=[
            "User wants to list Slack user groups",
            "User mentions 'Slack' + wants user groups",
            "User asks for all user groups"
        ],
        when_not_to_use=[
            "User wants specific group info (use get_user_group_info)",
            "User wants user info (use get_user_info)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Slack user groups",
            "Show user groups in workspace",
            "Get all user groups"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_groups(self, include_users: Optional[bool] = None, include_disabled: Optional[bool] = None) -> Tuple[bool, str]:
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

            response = await self.client.usergroups_list(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_groups: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_group_info",
        description="Get information about a specific user group",
        args_schema=GetUserGroupInfoInput,
        when_to_use=[
            "User wants info about a specific user group",
            "User mentions 'Slack' + wants group details",
            "User asks about a user group"
        ],
        when_not_to_use=[
            "User wants all groups (use get_user_groups)",
            "User wants user info (use get_user_info)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get info about user group X",
            "Show details of Slack user group",
            "What is in user group?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_group_info(self, usergroup: str, include_disabled: Optional[bool] = None) -> Tuple[bool, str]:
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

            response = await self.client.usergroups_info(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_user_group_info: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_user_channels",
        description="Get ALL conversations that the authenticated user is a member of (public channels, private channels, DMs, group DMs)",
        args_schema=GetUserChannelsInput,
        when_to_use=[
            "User wants to see their own channels/conversations/DMs",
            "User mentions 'Slack' + wants their channels",
            "User asks 'what channels am I in?', 'show my channels'"
        ],
        when_not_to_use=[
            "User wants all channels in workspace (use fetch_channels)",
            "User wants info about another user (use get_user_info)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What channels am I in?",
            "Show my Slack channels",
            "List channels I'm a member of",
            "Show all my conversations"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_channels(self, exclude_archived: Optional[bool] = None, types: Optional[str] = None) -> Tuple[bool, str]:
        """
        Args:
            exclude_archived: Exclude archived channels
            types: Comma-separated list of channel types (defaults to all: public_channel,private_channel,mpim,im)
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the channels
        """
        try:
            # Get authenticated user ID from token
            user_id = await self._get_authenticated_user_id()
            if not user_id:
                return (False, SlackResponse(success=False, error="Could not determine authenticated user ID from token").to_json())

            # Default to ALL conversation types if not specified
            conversation_types = types if types else "public_channel,private_channel,mpim,im"

            # Fetch all channels with pagination
            all_channels = []
            cursor = None

            while True:
                kwargs = {
                    "user": user_id,
                    "types": conversation_types,
                    "limit": 1000
                }
                if exclude_archived is not None:
                    kwargs["exclude_archived"] = exclude_archived
                if cursor:
                    kwargs["cursor"] = cursor

                response = await self.client.users_conversations(**kwargs)
                slack_response = self._handle_slack_response(response)

                if not slack_response.success or not slack_response.data:
                    # If first page fails, return error
                    if not all_channels:
                        return (slack_response.success, slack_response.to_json())
                    # If subsequent page fails, return what we have
                    break

                channels = slack_response.data.get('channels', [])
                all_channels.extend(channels)

                # Check for next page
                response_metadata = slack_response.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor
                logger.debug(f"Fetched {len(channels)} channels for user, continuing pagination...")

            logger.info(f"✅ Fetched total {len(all_channels)} channels for authenticated user")
            return (True, SlackResponse(success=True, data={"channels": all_channels, "count": len(all_channels)}).to_json())

        except Exception as e:
            logger.error(f"Error in get_user_channels: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="delete_message",
        description="Delete a message from a channel",
        args_schema=DeleteMessageInput,
        when_to_use=[
            "User wants to delete a message",
            "User mentions 'Slack' + wants to delete",
            "User asks to remove a message"
        ],
        when_not_to_use=[
            "User wants to send message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Delete message in #general",
            "Remove a Slack message",
            "Delete this message"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def delete_message(self, channel: str, timestamp: str, as_user: Optional[bool] = None) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            kwargs = {
                "channel": chan,
                "ts": timestamp
            }
            if as_user is not None:
                kwargs["as_user"] = as_user

            response = await self.client.chat_delete(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in delete_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="update_message",
        description="Update an existing message in a channel",
        args_schema=UpdateMessageInput,
        when_to_use=[
            "User wants to edit/update a message",
            "User mentions 'Slack' + wants to edit message",
            "User asks to modify a message"
        ],
        when_not_to_use=[
            "User wants to send new message (use send_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Edit message in #general",
            "Update a Slack message",
            "Change message text"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def update_message(self, channel: str, timestamp: str, text: str, blocks: Optional[List[Dict]] = None, as_user: Optional[bool] = None) -> Tuple[bool, str]:
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
            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            kwargs = {
                "channel": chan,
                "ts": timestamp,
                "text": text
            }
            if blocks:
                kwargs["blocks"] = blocks
            if as_user is not None:
                kwargs["as_user"] = as_user

            response = await self.client.chat_update(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in update_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_message_permalink",
        description="Get a permalink for a specific message",
        args_schema=GetMessagePermalinkInput,
        when_to_use=[
            "User wants link to a specific message",
            "User mentions 'Slack' + wants message link",
            "User asks for message URL"
        ],
        when_not_to_use=[
            "User wants to read messages (use get_channel_history)",
            "User wants to send message (use send_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get link to message",
            "Share message permalink",
            "Get message URL"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_message_permalink(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Get a permalink for a specific message"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to get permalink for
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the permalink
        """
        try:
            response = await self.client.chat_get_permalink(
                channel=channel,
                message_ts=timestamp
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_message_permalink: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_reactions",
        description="Get reactions for a specific message",
        args_schema=GetReactionsInput,
        when_to_use=[
            "User wants to see reactions on a message",
            "User mentions 'Slack' + wants message reactions",
            "User asks for emoji reactions"
        ],
        when_not_to_use=[
            "User wants to add reaction (use add_reaction)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show reactions on message",
            "Get emoji reactions",
            "What reactions does message have?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_reactions(self, channel: str, timestamp: str, full: Optional[bool] = None) -> Tuple[bool, str]:
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

            response = await self.client.reactions_get(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_reactions: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="remove_reaction",
        description="Remove a reaction from a message",
        args_schema=RemoveReactionInput,
        when_to_use=[
            "User wants to remove emoji reaction",
            "User mentions 'Slack' + wants to remove reaction",
            "User asks to unreact"
        ],
        when_not_to_use=[
            "User wants to add reaction (use add_reaction)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Remove thumbs up from message",
            "Unreact to message",
            "Remove reaction in Slack"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def remove_reaction(self, channel: str, timestamp: str, name: str) -> Tuple[bool, str]:
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
            response = await self.client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=name
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in remove_reaction: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="get_pinned_messages",
        description="Get pinned messages from a channel",
        args_schema=GetPinnedMessagesInput,
        when_to_use=[
            "User wants to see pinned messages",
            "User mentions 'Slack' + wants pinned messages",
            "User asks for pinned content"
        ],
        when_not_to_use=[
            "User wants to pin message (use pin_message)",
            "User wants all messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show pinned messages in #general",
            "Get pinned messages from Slack",
            "What's pinned in channel?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_pinned_messages(self, channel: str) -> Tuple[bool, str]:
        """Get pinned messages from a channel"""
        """
        Args:
            channel: The channel to get pinned messages from
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the pinned messages
        """
        try:
            response = await self.client.pins_list(channel=channel)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_pinned_messages: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="unpin_message",
        description="Unpin a message from a channel",
        args_schema=UnpinMessageInput,
        when_to_use=[
            "User wants to unpin a message",
            "User mentions 'Slack' + wants to unpin",
            "User asks to remove pin"
        ],
        when_not_to_use=[
            "User wants to pin message (use pin_message)",
            "User wants to read messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Unpin message in #general",
            "Remove pin from message",
            "Unpin a Slack message"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def unpin_message(self, channel: str, timestamp: str) -> Tuple[bool, str]:
        """Unpin a message from a channel"""
        """
        Args:
            channel: The channel containing the message
            timestamp: Timestamp of the message to unpin
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the unpin details
        """
        try:
            response = await self.client.pins_remove(
                channel=channel,
                timestamp=timestamp
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in unpin_message: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())


    @tool(
        app_name="slack",
        tool_name="get_thread_replies",
        description="Get replies in a thread",
        args_schema=GetThreadRepliesInput,
        when_to_use=[
            "User wants to see thread replies",
            "User mentions 'Slack' + wants thread replies",
            "User asks for conversation thread"
        ],
        when_not_to_use=[
            "User wants to reply (use reply_to_message)",
            "User wants channel messages (use get_channel_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show replies in thread",
            "Get thread conversation",
            "What replies are in this thread?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_thread_replies(self, channel: str, timestamp: str, limit: Optional[int] = None) -> Tuple[bool, str]:
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

            response = await self.client.conversations_replies(**kwargs)
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_thread_replies: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    async def _resolve_user_identifier(self, user_identifier: str, allow_ambiguous: bool = False) -> Optional[str]:
        """Resolve user identifier (email, display name, or user ID) to user ID.

        Args:
            user_identifier: Email, display name, or user ID to resolve
            allow_ambiguous: If False, raises AmbiguousUserError when multiple matches found.
                        If True, returns the first match (legacy behavior).
        Returns:
            User ID string if unique match found, None if no match found

        Raises:
            AmbiguousUserError: When multiple users match and allow_ambiguous=False
        """
        try:
            if not user_identifier or not isinstance(user_identifier, str):
                return None

            # If it's already a user ID (starts with U), return as is
            if user_identifier.startswith('U') and len(user_identifier) >= MIN_SLACK_USER_ID_LENGTH:
                return user_identifier

            # Normalize the identifier for comparison
            target_identifier = user_identifier.lstrip('@').strip().casefold()
            if not target_identifier:
                return None

            # Try to find by email first (fastest, always unique)
            if '@' in user_identifier:
                try:
                    email = user_identifier.strip()
                    response = await self.client.users_lookup_by_email(email=email)
                    slack_response = self._handle_slack_response(response)
                    if slack_response.success and slack_response.data:
                        user_id = slack_response.data.get('user', {}).get('id')
                        if user_id:
                            logger.debug(f"Resolved user '{user_identifier}' to ID '{user_id}' via email lookup")
                            return user_id
                except Exception as e:
                    logger.debug(f"Email lookup failed for '{user_identifier}': {e}")

            # Try to find by display name or real name
            cursor = None
            exact_matches = []  # List of (user_id, name, user_info) tuples
            partial_matches = []

            while True:
                users_response = await self.client.users_list(cursor=cursor, limit=1000)
                users_slack_response = self._handle_slack_response(users_response)

                if not users_slack_response.success or not users_slack_response.data:
                    break

                users = users_slack_response.data.get('members', [])
                if not users:
                    break

                for user in users:
                    # Skip deleted/bot users
                    if user.get('deleted') or user.get('is_bot'):
                        continue

                    profile = user.get('profile', {}) or {}
                    user_id = user.get('id')

                    # Store user info for error messages
                    user_info = {
                        'id': user_id,
                        'name': user.get('name'),
                        'real_name': profile.get('real_name'),
                        'display_name': profile.get('display_name'),
                        'email': profile.get('email'),
                    }

                    # Check multiple name variations
                    names_to_match = [
                        profile.get('display_name_normalized'),
                        profile.get('real_name_normalized'),
                        profile.get('display_name'),
                        profile.get('real_name'),
                        user.get('name'),
                    ]

                    for name in names_to_match:
                        if not isinstance(name, str):
                            continue

                        name_normalized = name.casefold()

                        # Exact match
                        if name_normalized == target_identifier:
                            if not any(m[0] == user_id for m in exact_matches):
                                exact_matches.append((user_id, name, user_info))

                        # Partial match (for "Abhishek" matching "Abhishek Gupta")
                        elif target_identifier in name_normalized or name_normalized in target_identifier:
                            if len(target_identifier) >= MIN_PARTIAL_MATCH_LENGTH:
                                if not any(m[0] == user_id for m in partial_matches):
                                    partial_matches.append((user_id, name, user_info))

                # Check for next page
                response_metadata = users_slack_response.data.get('response_metadata', {})
                next_cursor = response_metadata.get('next_cursor')
                if not next_cursor:
                    break
                cursor = next_cursor

            # Handle exact matches
            if exact_matches:
                if len(exact_matches) > 1 and not allow_ambiguous:
                    # Multiple users with same name - raise error
                    matches_info = [match[2] for match in exact_matches]
                    raise AmbiguousUserError(user_identifier, matches_info)

                user_id = exact_matches[0][0]
                if len(exact_matches) > 1:
                    logger.warning(f"Multiple exact matches for '{user_identifier}', returning first: {user_id}")
                else:
                    logger.debug(f"Resolved user '{user_identifier}' to ID '{user_id}' via exact name match")
                return user_id

            # Handle partial matches
            elif partial_matches:
                # Prefer matches that start with the target identifier
                starting_matches = [m for m in partial_matches if m[1].casefold().startswith(target_identifier)]

                if starting_matches:
                    if len(starting_matches) > 1 and not allow_ambiguous:
                        matches_info = [match[2] for match in starting_matches]
                        raise AmbiguousUserError(user_identifier, matches_info)

                    user_id = starting_matches[0][0]
                    logger.debug(f"Resolved '{user_identifier}' to ID '{user_id}' via partial match")
                    return user_id
                else:
                    if len(partial_matches) > 1 and not allow_ambiguous:
                        matches_info = [match[2] for match in partial_matches]
                        raise AmbiguousUserError(user_identifier, matches_info)

                    user_id = partial_matches[0][0]
                    logger.debug(f"Resolved '{user_identifier}' to ID '{user_id}' via partial match")
                    return user_id

            logger.debug(f"Could not resolve user identifier '{user_identifier}'")
            return None

        except AmbiguousUserError:
            raise  # Re-raise ambiguous errors
        except Exception as e:
            logger.error(f"Error resolving user identifier '{user_identifier}': {e}")
            return None



    # @tool(
    #     app_name="slack",
    #     tool_name="create_poll",
    #     parameters=[
    #         ToolParameter(
    #             name="channel",
    #             type=ParameterType.STRING,
    #             description="The channel to post the poll in",
    #             required=True
    #         ),
    #         ToolParameter(
    #             name="question",
    #             type=ParameterType.STRING,
    #             description="The poll question",
    #             required=True
    #         ),
    #         ToolParameter(
    #             name="options",
    #             type=ParameterType.ARRAY,
    #             description="List of poll options",
    #             required=True,
    #             items={"type": "string"}
    #         )
    #     ]
    # )
    # def create_poll(self, channel: str, question: str, options: List[str]) -> Tuple[bool, str]:
    #     """Create an interactive poll in a channel"""
    #     """
    #     Args:
    #         channel: The channel to post the poll in
    #         question: The poll question
    #         options: List of poll options
    #     Returns:
    #         A tuple with a boolean indicating success/failure and a JSON string with the poll details
    #     """
    #     try:
    #         # Create interactive blocks for the poll
    #         blocks = [
    #             {
    #                 "type": "section",
    #                 "text": {
    #                     "type": "mrkdwn",
    #                     "text": f"*{question}*"
    #                 }
    #             },
    #             {
    #                 "type": "actions",
    #                 "elements": []
    #             }
    #         ]

    #         # Add buttons for each option
    #         for i, option in enumerate(options):
    #             blocks[1]["elements"].append({
    #                 "type": "button",
    #                 "text": {
    #                     "type": "plain_text",
    #                     "text": option
    #                 },
    #                 "action_id": f"poll_option_{i}",
    #                 "value": option
    #             })

    #         response = await self.client.chat_post_message(
    #             channel=channel,
    #             text=f"Poll: {question}",
    #             blocks=blocks
    #         ))
    #         slack_response = self._handle_slack_response(response)
    #         return (slack_response.success, slack_response.to_json())

    #     except Exception as e:
    #         logger.error(f"Error in create_poll: {e}")
    #         slack_response = self._handle_slack_error(e)
    #         return (slack_response.success, slack_response.to_json())

    # @tool(
    #     app_name="slack",
    #     tool_name="archive_channel",
    #     parameters=[
    #         ToolParameter(
    #             name="channel",
    #             type=ParameterType.STRING,
    #             description="The channel to archive",
    #             required=True
    #         )
    #     ]
    # )
    # def archive_channel(self, channel: str) -> Tuple[bool, str]:
    #     """Archive a channel"""
    #     """
    #     Args:
    #         channel: The channel to archive
    #     Returns:
    #         A tuple with a boolean indicating success/failure and a JSON string with the archive details
    #     """
    #     try:
    #         response = await self.client.conversations_archive(channel=channel))
    #         slack_response = self._handle_slack_response(response)
    #         return (slack_response.success, slack_response.to_json())
    #     except Exception as e:
    #         logger.error(f"Error in archive_channel: {e}")
    #         slack_response = self._handle_slack_error(e)
    #         return (slack_response.success, slack_response.to_json())


#    @tool(
#         app_name="slack",
#         tool_name="send_message_with_formatting",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to send the message to",
#                 required=True
#             ),
#             ToolParameter(
#                 name="message",
#                 type=ParameterType.STRING,
#                 description="The message to send with markdown formatting",
#                 required=True
#             ),
#             ToolParameter(
#                 name="blocks",
#                 type=ParameterType.ARRAY,
#                 description="Rich message blocks for advanced formatting",
#                 required=False,
#                 items={"type": "object"}
#             )
#         ]
#     )
#     def send_message_with_formatting(self, channel: str, message: str, blocks: Optional[List[Dict]] = None) -> Tuple[bool, str]:
#         """Send a message with markdown formatting or rich blocks"""
#         """
#         Args:
#             channel: The channel to send the message to
#             message: The message to send with markdown formatting
#             blocks: Rich message blocks for advanced formatting
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the message details
#         """
#         try:
#             # Resolve channel name to channel ID if needed
#             chan = self._resolve_channel(channel)

#             kwargs = {
#                 "channel": chan,
#                 "text": message,
#                 "mrkdwn": True
#             }

#             if blocks:
#                 kwargs["blocks"] = blocks

#             response = await self.client.chat_post_message(**kwargs))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())
#         except Exception as e:
#             logger.error(f"Error in send_message_with_formatting: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())


#    @tool(
#         app_name="slack",
#         tool_name="upload_file",
#         description="Upload a file to a Slack channel",
#         args_schema=UploadFileInput,
#     )
#     def upload_file(self, channel: str, filename: str, file_path: Optional[str] = None, file_content: Optional[str] = None, title: Optional[str] = None, initial_comment: Optional[str] = None) -> Tuple[bool, str]:
#         """Upload a file to a channel"""
#         """
#         Args:
#             channel: The channel to upload the file to
#             filename: Name of the file
#             file_path: Path to the file to upload
#             file_content: Content of the file to upload
#             title: Title of the file
#             initial_comment: Initial comment about the file
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the upload details
#         """
#         try:
#             kwargs = {
#                 "channels": channel,
#                 "filename": filename
#             }

#             if file_path:
#                 kwargs["file"] = file_path
#             elif file_content:
#                 kwargs["content"] = file_content

#             if title:
#                 kwargs["title"] = title
#             if initial_comment:
#                 kwargs["initial_comment"] = initial_comment

#             response = await self.client.files_upload(**kwargs))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())

#         except Exception as e:
#             logger.error(f"Error in upload_file: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="create_channel",
#         description="Create a new Slack channel",
#         args_schema=CreateChannelInput,
#     )
#     def create_channel(self, name: str, is_private: Optional[bool] = None, topic: Optional[str] = None, purpose: Optional[str] = None) -> Tuple[bool, str]:
#         """Create a new channel"""
#         """
#         Args:
#             name: Name of the channel to create
#             is_private: Whether the channel should be private
#             topic: Topic for the channel
#             purpose: Purpose of the channel
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the channel details
#         """
#         try:
#             kwargs = {"name": name}

#             if is_private is not None:
#                 kwargs["is_private"] = is_private

#             response = await self.client.conversations_create(**kwargs))
#             slack_response = self._handle_slack_response(response)

#             # Set topic and purpose if provided and channel was created successfully
#             if slack_response.success and slack_response.data:
#                 channel_id = slack_response.data.get('channel', {}).get('id')

#                 if topic and channel_id:
#                     try:
#                         await self.client.conversations_set_topic(channel=channel_id, topic=topic))
#                     except Exception as e:
#                         logger.warning(f"Failed to set topic: {e}")

#                 if purpose and channel_id:
#                     try:
#                         await self.client.conversations_set_purpose(channel=channel_id, purpose=purpose))
#                     except Exception as e:
#                         logger.warning(f"Failed to set purpose: {e}")

#             return (slack_response.success, slack_response.to_json())

#         except Exception as e:
#             logger.error(f"Error in create_channel: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="invite_users_to_channel",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to invite users to",
#                 required=True
#             ),
#             ToolParameter(
#                 name="users",
#                 type=ParameterType.ARRAY,
#                 description="List of user IDs or emails to invite",
#                 required=True,
#                 items={"type": "string"}
#             )
#         ]
#     )
#     def invite_users_to_channel(self, channel: str, users: List[str]) -> Tuple[bool, str]:
#         """Invite users to a channel"""
#         """
#         Args:
#             channel: The channel to invite users to
#             users: List of user IDs or emails to invite
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the invitation details
#         """
#         try:
#             # Resolve user identifiers to user IDs
#             user_ids = []
#             unresolved_users = []
#             ambiguous_users = []

#             for user in users:
#                 try:
#                     user_id = await self._resolve_user_identifier(user, allow_ambiguous=False)
#                     if user_id:
#                         user_ids.append(user_id)
#                     else:
#                         unresolved_users.append(user)
#                         logger.warning(f"Could not resolve user: {user}")
#                 except AmbiguousUserError as e:
#                     ambiguous_users.append((user, e.matches))
#                     logger.warning(f"Ambiguous user match for: {user}")

#             # Build error message if there are issues
#             error_parts = []
#             if ambiguous_users:
#                 for user, matches in ambiguous_users:
#                     matches_list = []
#                     for match in matches:
#                         match_str = f"  - {match.get('real_name') or match.get('display_name') or match.get('name', 'Unknown')}"
#                         if match.get('email'):
#                             match_str += f" ({match['email']})"
#                         match_str += f" [ID: {match.get('id', 'Unknown')}]"
#                         matches_list.append(match_str)

#                     error_parts.append(
#                         f"Multiple users found matching '{user}':\n" + "\n".join(matches_list)
#                     )

#             if unresolved_users:
#                 error_parts.append(f"Could not resolve users: {', '.join(unresolved_users)}")

#             if error_parts:
#                 error_msg = "Some users could not be resolved:\n\n" + "\n\n".join(error_parts)
#                 error_msg += "\n\nTip: Use email addresses (e.g., 'user@example.com') or Slack user IDs (e.g., 'U1234567890') for unambiguous identification."
#                 return (False, SlackResponse(success=False, error=error_msg).to_json())

#             if not user_ids:
#                 return (False, SlackResponse(success=False, error="No valid users found to invite").to_json())

#             # Resolve channel name to channel ID if needed
#             chan = self._resolve_channel(channel)

#             response = await self.client.conversations_invite(
#                 channel=chan,
#                 users=user_ids
#             ))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())

#         except Exception as e:
#             logger.error(f"Error in invite_users_to_channel: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="rename_channel",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to rename",
#                 required=True
#             ),
#             ToolParameter(
#                 name="name",
#                 type=ParameterType.STRING,
#                 description="New name for the channel",
#                 required=True
#             )
#         ]
#     )
#     def rename_channel(self, channel: str, name: str) -> Tuple[bool, str]:
#         """Rename a channel"""
#         """
#         Args:
#             channel: The channel to rename
#             name: New name for the channel
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the rename details
#         """
#         try:
#             response = await self.client.conversations_rename(
#                 channel=channel,
#                 name=name
#             ))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())
#         except Exception as e:
#             logger.error(f"Error in rename_channel: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="set_channel_topic",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to set topic for",
#                 required=True
#             ),
#             ToolParameter(
#                 name="topic",
#                 type=ParameterType.STRING,
#                 description="New topic for the channel",
#                 required=True
#             )
#         ]
#     )
#     def set_channel_topic(self, channel: str, topic: str) -> Tuple[bool, str]:
#         """Set the topic for a channel"""
#         """
#         Args:
#             channel: The channel to set topic for
#             topic: New topic for the channel
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the topic details
#         """
#         try:
#             response = await self.client.conversations_set_topic(
#                 channel=channel,
#                 topic=topic
#             ))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())
#         except Exception as e:
#             logger.error(f"Error in set_channel_topic: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="set_channel_purpose",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to set purpose for",
#                 required=True
#             ),
#             ToolParameter(
#                 name="purpose",
#                 type=ParameterType.STRING,
#                 description="New purpose for the channel",
#                 required=True
#             )
#         ]
#     )
#     def set_channel_purpose(self, channel: str, purpose: str) -> Tuple[bool, str]:
#         """Set the purpose for a channel"""
#         """
#         Args:
#             channel: The channel to set purpose for
#             purpose: New purpose for the channel
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the purpose details
#         """
#         try:
#             response = await self.client.conversations_set_purpose(
#                 channel=channel,
#                 purpose=purpose
#             ))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())
#         except Exception as e:
#             logger.error(f"Error in set_channel_purpose: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())

#     @tool(
#         app_name="slack",
#         tool_name="mark_channel_read",
#         parameters=[
#             ToolParameter(
#                 name="channel",
#                 type=ParameterType.STRING,
#                 description="The channel to mark as read",
#                 required=True
#             ),
#             ToolParameter(
#                 name="timestamp",
#                 type=ParameterType.STRING,
#                 description="Timestamp of the last message read",
#                 required=False
#             )
#         ]
#     )
#     def mark_channel_read(self, channel: str, timestamp: Optional[str] = None) -> Tuple[bool, str]:
#         """Mark a channel as read"""
#         """
#         Args:
#             channel: The channel to mark as read
#             timestamp: Timestamp of the last message read
#         Returns:
#             A tuple with a boolean indicating success/failure and a JSON string with the mark details
#         """
#         try:
#             kwargs = {"channel": channel}
#             if timestamp:
#                 kwargs["ts"] = timestamp

#             response = await self.client.conversations_mark(**kwargs))
#             slack_response = self._handle_slack_response(response)
#             return (slack_response.success, slack_response.to_json())
#         except Exception as e:
#             logger.error(f"Error in mark_channel_read: {e}")
#             slack_response = self._handle_slack_error(e)
#             return (slack_response.success, slack_response.to_json())
