import asyncio
import logging
import re
from datetime import datetime, timezone
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
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource

logger = logging.getLogger(__name__)

# Constants
MIN_SLACK_USER_ID_LENGTH = 9  # Minimum length for valid Slack user ID (starts with 'U' or 'W')
MIN_PARTIAL_MATCH_LENGTH = 3  # Minimum length for partial name matching
USER_LOOKUP_CONCURRENCY = 20  # Max parallel users.info calls when resolving IDs to names
# Slack user-ID prefixes: 'U' (regular) and 'W' (Enterprise Grid workspace user).
# See https://docs.slack.dev/reference/methods/reactions.get for an example W-prefixed ID.
USER_ID_PREFIXES = ('U', 'W')

# Captures the user ID inside a Slack mention like <@U0ABC1234> or <@W0ABC1234>.
# Used by enrichment to substitute @display_name into message text.
SLACK_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)>")


def _is_user_id(value: Any) -> bool:
    """True iff value looks like a Slack user ID ('U…' or 'W…' of plausible length).

    The Slack docs describe user IDs as upper-case letter+digits starting with U or W;
    bot IDs ('B…') and channel IDs ('C…'/'G…'/'D…') deliberately do NOT match here so
    we never feed them to users.info.
    """
    return (
        isinstance(value, str)
        and len(value) >= MIN_SLACK_USER_ID_LENGTH
        and value.startswith(USER_ID_PREFIXES)
    )


class AmbiguousUserError(Exception):
    """Raised when multiple users match a given identifier"""
    def __init__(self, identifier: str, matches: List[Dict[str, Any]]) -> None:
        self.identifier = identifier
        self.matches = matches
        super().__init__(f"Multiple users found matching '{identifier}'. Please use email or user ID for disambiguation.")

class SlackDateError(ValueError):
    """Raised when an LLM-supplied date string can't be parsed."""


def _iso_to_slack_ts(date_str: Optional[str]) -> Optional[str]:
    """Parse an ISO 8601 date/datetime and return Slack's `oldest`/`latest` format.

    Accepts:
      - 'YYYY-MM-DD' (treated as 00:00:00 UTC)
      - 'YYYY-MM-DDTHH:MM:SS' (naive — treated as UTC)
      - 'YYYY-MM-DDTHH:MM:SS+HH:MM' or '...Z' (timezone-aware)

    Returns None for None / empty input. Raises :class:`SlackDateError` on
    unparseable input — surfaced to the LLM so it can correct the format
    rather than silently fetching the wrong window.
    """
    if date_str is None:
        return None
    s = str(date_str).strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise SlackDateError(
            f"Invalid date '{date_str}'. Expected ISO 8601 like 'YYYY-MM-DD' "
            f"or 'YYYY-MM-DDTHH:MM:SSZ' (UTC assumed when no timezone given)."
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.timestamp():.6f}"


def _iso_to_slack_post_at(date_str: Optional[str]) -> Optional[int]:
    """Like :func:`_iso_to_slack_ts` but returns integer epoch seconds.

    Used for `chat.scheduleMessage`'s `post_at`, which is an int-seconds field
    (Slack rejects fractional seconds here).
    """
    ts = _iso_to_slack_ts(date_str)
    return int(float(ts)) if ts is not None else None


def _slack_ts_to_iso(ts: Any) -> Optional[str]:  # noqa: ANN401
    """Convert a Slack epoch value to an ISO 8601 UTC string.

    Accepts string ('1700000000.000000'), int seconds, or float seconds.
    Returns None when the value is missing or can't be parsed — callers should
    treat that as "no enrichment available" and leave the original field as is.
    """
    if ts is None:
        return None
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.isoformat().replace("+00:00", "Z")

_EPOCH_FIELDS_ON_MESSAGE = ("ts", "thread_ts", "latest_reply", "last_read")
_EPOCH_FIELDS_ON_CONVERSATION = ("created", "last_read")
_EPOCH_FIELDS_ON_FILE = ("created", "timestamp", "updated")
_EPOCH_FIELDS_ON_PIN_ITEM = ("created",)
_EPOCH_FIELDS_ON_SCHEDULED = ("post_at", "date_created")
_EPOCH_FIELDS_ON_ROOM = ("date_start", "date_end", "canvas_thread_ts", "thread_root_ts")


def _add_iso_date_sibling(d: Dict[str, Any], field: str) -> None:
    """If `d[field]` is a Slack epoch, set `d[f'{field}_date']` to the ISO form.

    No-op if the field is absent or unparseable. Never overwrites an existing
    `<field>_date` key (so re-running enrichment on already-enriched data is
    idempotent).
    """
    if field not in d:
        return
    iso = _slack_ts_to_iso(d.get(field))
    if iso is None:
        return
    sibling = f"{field}_date"
    if sibling not in d:
        d[sibling] = iso

# Pydantic schemas for Slack tools
class SendMessageInput(BaseModel):
    """Schema for sending a message"""
    channel: str = Field(description="The channel to send the message to")
    message: str = Field(description="The message to send. Must use Slack's mrkdwn format")


class GetChannelHistoryInput(BaseModel):
    """Schema for getting channel history"""
    channel: str = Field(description="The channel to get the history of")
    limit: Optional[int] = Field(default=None, description="Maximum number of messages to return")
    oldest: Optional[str] = Field(
        default=None,
        description=(
            "Start of time range as an ISO 8601 date or datetime string. "
            "Examples: '2026-04-30' (midnight UTC), '2026-04-30T09:00:00Z', "
            "'2026-04-30T14:30:00+05:30'. Naive datetimes are interpreted as UTC. "
            "Pair with 'latest' for an explicit end bound, "
            "otherwise omit 'latest' to mean 'up to now'."
        ),
    )
    latest: Optional[str] = Field(
        default=None,
        description=(
            "End of time range as an ISO 8601 date or datetime string (same "
            "format as 'oldest'). Omit to fetch up to the current time. "
        ),
    )
    inclusive: Optional[bool] = Field(
        default=None,
        description="Include messages with the exact 'oldest' or 'latest' timestamp. Only relevant when those are set.",
    )


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

class GetDmHistoryInput(BaseModel):
    """Schema for getting DM history with a specific user"""
    user: str = Field(description="DM partner identifier. Accepts email, display name, real name, or Slack user ID (starts with 'U').")
    limit: Optional[int] = Field(default=None, description="Maximum number of messages to return")
    oldest: Optional[str] = Field(
        default=None,
        description=(
            "Start of time range as an ISO 8601 date or datetime string. "
            "Examples: '2026-04-30' (midnight UTC), '2026-04-30T09:00:00Z', "
            "'2026-04-30T14:30:00+05:30'. Naive datetimes are interpreted as UTC. "
            "Pair with 'latest' for an explicit end bound, "
            "otherwise omit 'latest' to mean 'up to now'."
        ),
    )
    latest: Optional[str] = Field(
        default=None,
        description=(
            "End of time range as an ISO 8601 date or datetime string (same "
            "format as 'oldest'). Omit to fetch up to the current time. "
        ),
    )
    inclusive: Optional[bool] = Field(
        default=None,
        description="Include messages with the exact 'oldest' or 'latest' timestamp. Only relevant when those are set.",
    )


class ReplyToMessageInput(BaseModel):
    """Schema for replying to a message"""
    channel: str = Field(description="The channel id containing the message (e.g 'C1234567890','G1234567890', 'D1234567890') to reply to")
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
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to add reaction to")
    name: str = Field(description="Name of the emoji reaction (e.g., 'thumbsup', '+1')")

class SearchMessagesInput(BaseModel):
    """Schema for searching messages"""
    query: str = Field(description="The search query to find messages")
    channel: Optional[str] = Field(default=None, description="The channel to search in")
    with_user: Optional[str] = Field(
        default=None,
        description="Filter to messages exchanged with this user, including DMs. "
                    "Accepts email, display name, real name, or Slack user ID. "
                    "Adds Slack's 'with:@user' search modifier.",
    )
    from_user: Optional[str] = Field(
        default=None,
        description="Filter to messages authored by this user. "
                    "Accepts email, display name, real name, or Slack user ID. "
                    "Adds Slack's 'from:@user' search modifier.",
    )
    count: Optional[int] = Field(default=None, description="Maximum number of results to return")
    sort: Optional[str] = Field(default=None, description="Sort order (timestamp, score)")
    sort_dir: Optional[str] = Field(
        default=None,
        description="Sort direction: 'asc' or 'desc'. Defaults to Slack's default ('desc') when omitted.",
    )
    after: Optional[str] = Field(
        default=None,
        description=(
            "Restrict to messages after this date (YYYY-MM-DD). Adds Slack's "
            "'after:<date>' modifier. WARNING: this is EXCLUSIVE — 'after:2025-01-15' "
            "matches messages from 2025-01-16 onward, NOT 2025-01-15. For narrow "
            "windows like 'today' or 'last 24 hours', use get_channel_history with "
            "'oldest'/'latest' instead — search has indexing lag and exclusive bounds."
        ),
    )
    before: Optional[str] = Field(
        default=None,
        description=(
            "Restrict to messages before this date (YYYY-MM-DD). Adds Slack's "
            "'before:<date>' modifier. WARNING: this is EXCLUSIVE — 'before:2025-01-15' "
            "matches messages up to 2025-01-14, NOT including 2025-01-15."
        ),
    )

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
    post_at: str = Field(
        description=(
            "When to post the message, as an ISO 8601 datetime string. "
            "Examples: '2026-05-01T09:00:00Z' (UTC), "
            "'2026-05-01T14:30:00+05:30' (with timezone offset). "
            "Naive datetimes (no timezone) are interpreted as UTC. "
            "Date-only ('2026-05-01') is accepted and means midnight UTC. "
        )
    )

class PinMessageInput(BaseModel):
    """Schema for pinning a message"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
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
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to delete")
    as_user: Optional[bool] = Field(default=None, description="Delete the message as the authenticated user")


class UpdateMessageInput(BaseModel):
    """Schema for updating a message"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to update")
    text: str = Field(description="New text content for the message")
    blocks: Optional[List[Dict]] = Field(default=None, description="Rich message blocks for advanced formatting")
    as_user: Optional[bool] = Field(default=None, description="Update the message as the authenticated user")


class GetMessagePermalinkInput(BaseModel):
    """Schema for getting message permalink"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to get permalink for")


class GetReactionsInput(BaseModel):
    """Schema for getting reactions"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to get reactions for")
    full: Optional[bool] = Field(default=None, description="Return full reaction objects")


class RemoveReactionInput(BaseModel):
    """Schema for removing a reaction"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to remove reaction from")
    name: str = Field(description="Name of the emoji reaction to remove")


class GetPinnedMessagesInput(BaseModel):
    """Schema for getting pinned messages"""
    channel: str = Field(description="The channel to get pinned messages from")


class UnpinMessageInput(BaseModel):
    """Schema for unpinning a message"""
    channel: str = Field(description="The channel id containing the message e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the message to unpin")


class GetThreadRepliesInput(BaseModel):
    """Schema for getting thread replies"""
    channel: str = Field(description="The channel id containing the thread e.g 'C1234567890','G1234567890', 'D1234567890'")
    timestamp: str = Field(description="Timestamp of the parent message")
    limit: Optional[int] = Field(default=None, description="Maximum number of replies to return")
    oldest: Optional[str] = Field(
        default=None,
        description=(
            "Start of time range (inclusive of older replies) as an ISO 8601 "
            "date or datetime string, e.g. '2026-04-30' or "
            "'2026-04-30T09:00:00Z'. Naive datetimes are treated as UTC. "
        ),
    )
    latest: Optional[str] = Field(
        default=None,
        description=(
            "End of time range (inclusive of newer replies) as an ISO 8601 "
            "date or datetime string. Same format as 'oldest'. "
            "Omit to fetch up to the current time."
        ),
    )
    inclusive: Optional[bool] = Field(
        default=None,
        description="Include messages with the exact 'oldest' or 'latest' timestamp. Only relevant when those are set.",
    )

class UploadFileToChannelInput(BaseModel):
    """Schema for uploading a file to a Slack channel"""
    channel: str = Field(description="The channel to share the file in")
    filename: str = Field(
        description="Name of the file with extension, e.g. 'transcript.txt'. "
                    "Slack uses the extension for syntax highlighting."
    )
    file_content: str = Field(
        description="The full text content of the file to upload. "
                    "For transcripts, pass the entire text — not a summary."
    )
    title: Optional[str] = Field(
        default=None,
        description="Display title for the file in Slack"
    )
    initial_comment: Optional[str] = Field(
        default=None,
        description="Message posted alongside the file in the channel"
    )

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
            icon_path=IconPaths.connector_icon("slack"),
            app_group="Communication",
            app_description="Slack OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("slack"))
        .add_documentation_link(DocumentationLink(
            title="Slack Bot Token Setup",
            url="https://docs.slack.dev/authentication/tokens#bot",
            doc_type="setup",
        ))
        .add_documentation_link(DocumentationLink(
            title="Pipeshub Documentation",
            url="https://docs.pipeshub.com/toolsets/slack/slack",
            doc_type="pipeshub",
        )))\
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

        self._user_cache: Dict[str, Dict[str, Optional[str]]] = {}
        self._channel_cache: Dict[str, str] = {}
        # Shared across user + channel resolvers so a parallel
        # _enrich_messages gather can't double the effective burst against
        # Slack's per-method tier limits.
        self._lookup_sem = asyncio.Semaphore(USER_LOOKUP_CONCURRENCY)

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
        description="Get message history from a Slack channel, optionally filtered by a time window",
        args_schema=GetChannelHistoryInput,
        when_to_use=[
            "User wants to read messages from a Slack channel",
            "User wants messages from a channel within a time window (e.g. 'last 24 hours', 'last week', 'today', 'since Monday')",
            "User mentions 'Slack' + wants to see messages/history",
            "User asks for recent messages in a channel"
        ],
        when_not_to_use=[
            "User wants to send a message (use send_message)",
            "User wants to keyword-search across messages (use search_messages)",
            "User wants info ABOUT Slack (use retrieval)",
            "No Slack mention (use other tools)"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me messages from #general",
            "Get Slack channel history",
            "What was said in #tech in the last 24 hours?",
            "Messages in #engineering since yesterday",
            "Today's messages in #general",
            "What was said in the channel?"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description=(
            "Fetch messages from a Slack channel. For time-windowed requests "
            "(e.g. 'last 24 hours', 'today', 'since yesterday'), set 'oldest' "
            "(and optionally 'latest') to ISO 8601 dates or datetimes — for "
            "example '2026-04-30' or '2026-04-30T09:00:00Z'. Naive datetimes "
            "are treated as UTC. Do NOT pass Unix epoch values; the tool "
            "converts dates to Slack's internal format. PREFER this tool over "
            "search_messages whenever the user wants channel messages from a "
            "time window without a keyword."
        ),
    )
    async def get_channel_history(
        self,
        channel: str,
        limit: Optional[int] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get the history of a channel"""
        """
        Args:
            channel: The channel to get the history of
            limit: Maximum number of messages to return
            oldest: Start of time range (ISO 8601 date or datetime string)
            latest: End of time range (ISO 8601 date or datetime string)
            inclusive: Include messages with exact oldest/latest timestamps
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the history details
        """
        try:
            try:
                oldest_ts = _iso_to_slack_ts(oldest)
                latest_ts = _iso_to_slack_ts(latest)
            except SlackDateError as date_err:
                return (False, SlackResponse(success=False, error=str(date_err)).to_json())

            # Resolve channel name like "#bugs" to channel ID
            chan = await self._resolve_channel(channel)

            # Use SlackDataSource method. SDS already drops None-valued kwargs,
            # so passing all params unconditionally is safe.
            response = await self.client.conversations_history(
                channel=chan,
                limit=limit,
                oldest=oldest_ts,
                latest=latest_ts,
                inclusive=inclusive,
            )
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())

            # Resolve all Slack user IDs that appear in the messages —
            # text mentions, authors, parent_user_id, reply authors, and
            # reactions[].users — via the shared enrichment helper.
            try:
                data = slack_response.data
                messages = data.get('messages', []) if isinstance(data, dict) else []
                resolved_messages = await self._enrich_messages(messages)
                enriched = dict(data) if isinstance(data, dict) else {}
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
                          "user_display_name", "user_email", "parent_user_id",
                          "parent_user_display_name", "user_display_names", "edited",
                          "thread_ts", "reply_count", "replies", "subscribed", "subtype",
                          "type", "attachments", "blocks", "files", "reactions", "pinned_to",
                          "permalink", "has_more", "next_cursor", "previous_cursor", "bot_profile",
                          "client_msg_id", "upload",
                          # Essential file fields to keep (including URLs for user access)
                          "created", "timestamp", "title", "mimetype", "filetype", "pretty_type",
                          "size", "url_private", "url_private_download", "permalink_public",
                          # ISO date siblings injected by _apply_resolution_to_message
                          # so the LLM never needs to interpret raw Slack epoch values.
                          "ts_date", "thread_ts_date", "latest_reply_date",
                          "created_date", "timestamp_date", "updated_date",
                          "last_read_date", "post_at_date", "date_created_date")
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
            "User mentions 'Slack' + wants to see channels/DMs/conversations",
            "User asks for available channels/conversations",
            "User wants to enumerate their DMs (returned as 'im' entries with id starting 'D' and a 'user' field for the partner)"
        ],
        when_not_to_use=[
            "User wants channel info (use get_channel_info)",
            "User wants messages (use get_channel_history; for DMs with a named person use get_dm_history)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Slack channels",
            "Show me available channels",
            "What channels are in Slack?",
            "Show all conversations including DMs",
            "List my DMs"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description=(
            "Lists all conversations the user can access. Differentiate by 'is_im'/'is_mpim'/'is_private' "
            "or id prefix (D/G/C). DM entries have no 'name' — only 'id' (D…) and 'user' (U…). "
            "The DM partner's display name is pre-resolved into 'user_display_name' on each IM entry "
            "(and 'creator_display_name' for channels), so no extra users_info call is needed."
        ),
    )
    async def fetch_channels(
        self,
        types: Optional[str] = None,
        exclude_archived: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Fetch all conversations (public channels, private channels, DMs, group DMs) with pagination"""
        """
        Args:
            types: Comma-separated channel types to include. Any combination of
                'public_channel', 'private_channel', 'mpim', 'im'. Defaults to all four.
            exclude_archived: When True, archived channels are omitted. Defaults to False
                (archived channels are included) to preserve prior behavior.
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with all conversations
        """
        try:
            effective_types = types or "public_channel,private_channel,mpim,im"
            effective_exclude_archived = (
                False if exclude_archived is None else exclude_archived
            )

            # Fetch ALL conversation types with pagination
            all_conversations = []
            cursor = None

            while True:
                kwargs: Dict[str, Any] = {
                    "types": effective_types,
                    "exclude_archived": effective_exclude_archived,
                    "limit": 1000,
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

            # IM entries carry only `id` + `user` (partner ID, no `name`); channels
            # carry `creator`. Resolve both so the LLM never sees a bare U…/W… ID.
            try:
                all_conversations = await self._enrich_conversations(all_conversations)
            except Exception as enrichment_err:
                logger.debug(f"fetch_channels enrichment failed: {enrichment_err}")

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

            # Enrich messages.matches[] (user, mentions, reactions) and
            # files.matches[].user (uploader). Slack returns these alongside
            # one another for search.all; for search.messages only messages.
            try:
                if isinstance(response, dict):
                    response = await self._enrich_search_response(response)
            except Exception as enrichment_err:
                logger.debug(f"search_all enrichment failed: {enrichment_err}")

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

            # conversations.members returns {members: ["U…", …]} — bare IDs.
            # Resolve to {id, display_name, real_name, email} so the LLM has
            # names without an extra round-trip.
            response = await self.client.conversations_members(channel=chan)
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            try:
                data = slack_response.data
                member_ids = data.get('members', [])
                resolved_members = await self._resolve_user_id_list(member_ids)
                enriched = dict(data)
                enriched['resolved_members'] = resolved_members
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Member enrichment failed: {enrichment_err}")
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
            response = await self.client.conversations_members(channel=channel_id)
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            try:
                data = slack_response.data
                member_ids = data.get('members', [])
                resolved_members = await self._resolve_user_id_list(member_ids)
                enriched = dict(data)
                enriched['resolved_members'] = resolved_members
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Member enrichment failed: {enrichment_err}")
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
        tool_name="get_dm_history",
        description="Get direct message (DM) history with a specific user, optionally filtered by a time window",
        args_schema=GetDmHistoryInput,
        when_to_use=[
            "User wants to read their personal/direct chat with a specific person",
            "User asks to summarize DMs / personal chats / 1:1 conversation with someone",
            "User wants their DM thread with a person within a time window (e.g. 'last 24 hours', 'last week', 'today', 'since Monday')",
            "User mentions 'Slack' + 'DM' / 'direct message' / 'personal chat' with a named person",
            "User wants the conversation between themselves and another user (not a channel)"
        ],
        when_not_to_use=[
            "User wants channel messages (use get_channel_history)",
            "User wants to keyword-search across Slack (use search_messages)",
            "User wants to send a DM (use send_direct_message)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Summarize my DMs with John Doe",
            "Show my personal chat with user@company.com",
            "What did I talk about with @john in Slack DMs?",
            "Get my direct messages with Alice",
            "Last 24 hours of my DMs with Vishwjeet",
            "My DMs with bob@company.com since yesterday",
            "Today's DMs with @alice"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description=(
            "Fetch the 1:1 DM history with a user. Pass the user (email, display name, "
            "real name, or Slack user ID) — NOT a channel ID. For time-windowed requests "
            "(e.g. 'last 24 hours', 'today', 'since yesterday'), set 'oldest' (and "
            "optionally 'latest') to ISO 8601 dates or datetimes — for example "
            "'2026-04-30' or '2026-04-30T09:00:00Z'. Naive datetimes are treated as "
            "UTC. "
            "Use for any 'DM' / 'personal chat' / '1:1 with X' request;"
        ),
    )
    async def get_dm_history(
        self,
        user: str,
        limit: Optional[int] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get DM history with a specific user.

        Args:
            user: User identifier (email, display name, real name, or Slack user ID)
            limit: Maximum number of messages to return
            oldest: Start of time range (ISO 8601 date or datetime string)
            latest: End of time range (ISO 8601 date or datetime string)
            inclusive: Include messages with exact oldest/latest timestamps
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the DM history
        """
        try:
            # Resolve identifier to a unique user ID
            try:
                user_id = await self._resolve_user_identifier(user, allow_ambiguous=False)
            except AmbiguousUserError as e:
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
                return (
                    False,
                    SlackResponse(
                        success=False,
                        error=f"User '{user}' not found. Please use email address or Slack user ID.",
                    ).to_json(),
                )

            # Open (or fetch existing) DM channel for the resolved user
            open_response = await self.client.conversations_open(users=[user_id])
            open_slack_response = self._handle_slack_response(open_response)
            if not open_slack_response.success:
                return (open_slack_response.success, open_slack_response.to_json())

            channel_id = (
                open_slack_response.data.get('channel', {}).get('id')
                if open_slack_response.data
                else None
            )
            if not channel_id:
                return (
                    False,
                    SlackResponse(success=False, error="Failed to open DM channel").to_json(),
                )
            return await self.get_channel_history(
                channel_id,
                limit=limit,
                oldest=oldest,
                latest=latest,
                inclusive=inclusive,
            )
        except AmbiguousUserError:
            raise
        except Exception as e:
            logger.error(f"Error in get_dm_history: {e}")
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
        description="Keyword-search messages in Slack (requires a query term)",
        args_schema=SearchMessagesInput,
        when_to_use=[
            "User wants to find messages containing specific keywords or phrases",
            "User mentions 'Slack' + wants to find messages by content",
            "User wants keyword search constrained to messages with/from a specific person (use with_user / from_user)"
        ],
        when_not_to_use=[
            "User wants to browse a channel's recent messages or a time window of a channel — use get_channel_history with oldest/latest instead (search has indexing lag and 'before/after' are exclusive day boundaries — bad for narrow windows)",
            "User wants the full DM thread with someone (use get_dm_history)",
            "User wants to search all content types — files, channels (use search_all)",
            "No Slack mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for messages about 'project'",
            "Find messages in #general about X",
            "Find messages from @alice about deploys",
            "Search my conversation with bob@company.com for 'roadmap'"
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description=(
            "Keyword search across Slack messages. Requires a real keyword/phrase "
            "in 'query'. Do NOT use this for 'all messages in #channel in the last N "
            "hours/days' — use get_channel_history with oldest/latest instead. "
            "Slack search has indexing lag (recent messages may not appear) and "
            "the 'before:'/'after:' modifiers are EXCLUSIVE day boundaries: "
            "'after:2025-01-01 before:2025-01-02' returns NOTHING (no day strictly "
            "between them). For 'today's' or 'last 24h' messages, use "
            "get_channel_history."
        ),
    )
    async def search_messages(
        self,
        query: str,
        channel: Optional[str] = None,
        with_user: Optional[str] = None,
        from_user: Optional[str] = None,
        count: Optional[int] = None,
        sort: Optional[str] = None,
        sort_dir: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Search for messages in Slack.

        Args:
            query: Search query (keywords / phrase)
            channel: Channel to search in (optional). Adds an 'in:#channel' modifier.
            with_user: Restrict to messages exchanged with this user (any identifier:
                email, display name, real name, or user ID). Adds 'with:@handle'.
            from_user: Restrict to messages authored by this user. Adds 'from:@handle'.
            count: Maximum number of results to return
            sort: Sort order (timestamp, score)
            sort_dir: Sort direction ('asc' or 'desc')
            after: Restrict to messages after this date (YYYY-MM-DD). Adds 'after:<date>'.
            before: Restrict to messages before this date (YYYY-MM-DD). Adds 'before:<date>'.
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the search results
        """
        try:
            modifiers: List[str] = []

            if channel:
                channel_name = channel[1:] if channel.startswith('#') else channel
                modifiers.append(f"in:{channel_name}")

            if after:
                modifiers.append(f"after:{after}")
            if before:
                modifiers.append(f"before:{before}")

            # Resolve with_user / from_user to Slack handles for use in search modifiers.
            # Slack's search syntax requires the username (e.g. 'with:@john'), not user IDs,
            # so we must look up the handle.
            for modifier_name, identifier in (("with", with_user), ("from", from_user)):
                if not identifier:
                    continue
                try:
                    handle = await self._resolve_user_handle(identifier)
                except AmbiguousUserError as e:
                    matches_list = []
                    for match in e.matches:
                        match_str = f"  - {match.get('real_name') or match.get('display_name') or match.get('name', 'Unknown')}"
                        if match.get('email'):
                            match_str += f" ({match['email']})"
                        match_str += f" [ID: {match.get('id', 'Unknown')}]"
                        matches_list.append(match_str)
                    error_msg = (
                        f"Multiple users found matching '{identifier}' for '{modifier_name}_user'. "
                        "Please use email or user ID for disambiguation.\n\n"
                        "Matching users:\n" + "\n".join(matches_list)
                    )
                    return (False, SlackResponse(success=False, error=error_msg).to_json())

                if not handle:
                    return (
                        False,
                        SlackResponse(
                            success=False,
                            error=f"Could not resolve '{identifier}' for '{modifier_name}_user' to a Slack user. "
                                  "Please use email address or Slack user ID.",
                        ).to_json(),
                    )
                modifiers.append(f"{modifier_name}:@{handle}")

            search_query = " ".join([*modifiers, query]).strip() if modifiers else query

            response = await self.client.search_messages(
                query=search_query,
                count=count,
                sort=sort,
                sort_dir=sort_dir,
            )
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            # Resolve user IDs in messages.matches[] (and files.matches[] if
            # present from a search.all-style call) so the LLM sees names.
            try:
                enriched = await self._enrich_search_response(slack_response.data)
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"search_messages enrichment failed: {enrichment_err}")
                return (slack_response.success, slack_response.to_json())

        except AmbiguousUserError:
            raise
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
            post_at: ISO 8601 date or datetime for when to post the message
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the scheduled message details
        """
        try:
            # Convert LLM-supplied ISO datetime -> integer epoch seconds.
            # Slack rejects fractional seconds for `post_at`.
            try:
                post_at_epoch = _iso_to_slack_post_at(post_at)
            except SlackDateError as date_err:
                return (False, SlackResponse(success=False, error=str(date_err)).to_json())
            if post_at_epoch is None:
                return (
                    False,
                    SlackResponse(success=False, error="post_at is required").to_json(),
                )

            # Resolve channel name to channel ID if needed
            chan = await self._resolve_channel(channel)

            # Convert standard markdown to Slack mrkdwn format
            slack_message = self._convert_markdown_to_slack_mrkdwn(message)

            response = await self.client.chat_schedule_message(
                channel=chan,
                text=slack_message,
                post_at=post_at_epoch,
            )
            slack_response = self._handle_slack_response(response)
            # Surface a human-readable date alongside the epoch in the response,
            # so the LLM can confirm the scheduled time without re-converting.
            if slack_response.success and isinstance(slack_response.data, dict):
                enriched = dict(slack_response.data)
                _add_iso_date_sibling(enriched, "post_at")
                return (True, SlackResponse(success=True, data=enriched).to_json())
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
            # Resolve channel name like '#general' to ID
            chan = await self._resolve_channel(channel)

            # Get channel info to check for unread count
            info_response = await self.client.conversations_info(channel=chan)
            info_slack_response = self._handle_slack_response(info_response)

            if not info_slack_response.success:
                return (info_slack_response.success, info_slack_response.to_json())

            # Get recent messages
            history_response = await self.client.conversations_history(channel=chan, limit=50)
            history_slack_response = self._handle_slack_response(history_response)

            if not history_slack_response.success:
                return (history_slack_response.success, history_slack_response.to_json())

            # Resolve user IDs in messages, and resolve `creator`/`user` (DM
            # partner) on the channel_info object via _enrich_conversations.
            recent_messages = (
                history_slack_response.data.get('messages', [])
                if history_slack_response.data else []
            )
            enriched_messages = await self._enrich_messages(recent_messages)

            channel_info = info_slack_response.data or {}
            channel_obj = channel_info.get('channel') if isinstance(channel_info, dict) else None
            if isinstance(channel_obj, dict):
                enriched_channel_list = await self._enrich_conversations([channel_obj])
                channel_info = dict(channel_info)
                channel_info['channel'] = enriched_channel_list[0] if enriched_channel_list else channel_obj

            # Combine channel info with recent messages
            result = {
                "channel_info": channel_info,
                "recent_messages": enriched_messages,
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
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            # Each scheduled item carries `post_at` and `date_created` as int
            # epoch seconds — surface ISO date siblings so the LLM doesn't
            # have to interpret raw epochs.
            try:
                data = dict(slack_response.data)
                items = data.get('scheduled_messages')
                if isinstance(items, list):
                    new_items = []
                    for it in items:
                        if isinstance(it, dict):
                            new_it = dict(it)
                            for f in _EPOCH_FIELDS_ON_SCHEDULED:
                                _add_iso_date_sibling(new_it, f)
                            new_items.append(new_it)
                        else:
                            new_items.append(it)
                    data['scheduled_messages'] = new_items
                return (True, SlackResponse(success=True, data=data).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Scheduled-message enrichment failed: {enrichment_err}")
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
                if not slack_response.success or not isinstance(slack_response.data, dict):
                    return (slack_response.success, slack_response.to_json())
                try:
                    channels = slack_response.data.get('channels', [])
                    enriched_channels = await self._enrich_conversations(channels)
                    enriched_data = dict(slack_response.data)
                    enriched_data['channels'] = enriched_channels
                    return (True, SlackResponse(success=True, data=enriched_data).to_json())
                except Exception as enrichment_err:
                    logger.debug(f"users_conversations enrichment failed: {enrichment_err}")
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

            # Resolve DM partner (`user`) and channel `creator` for every entry
            try:
                all_conversations = await self._enrich_conversations(all_conversations)
            except Exception as enrichment_err:
                logger.debug(f"users_conversations enrichment failed: {enrichment_err}")

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
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            # Resolve created_by/updated_by/deleted_by + users[] per usergroup
            try:
                data = slack_response.data
                groups = data.get('usergroups', [])
                resolved_groups = await self._enrich_usergroups(groups)
                enriched = dict(data)
                enriched['usergroups'] = resolved_groups
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Usergroup enrichment failed: {enrichment_err}")
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
            if not slack_response.success or not isinstance(slack_response.data, dict):
                return (slack_response.success, slack_response.to_json())

            # usergroups.info returns the group at top-level `usergroup` (singular).
            # Some Slack helpers return `usergroups: [...]` instead — handle both.
            try:
                data = slack_response.data
                enriched = dict(data)
                single = data.get('usergroup')
                if isinstance(single, dict):
                    resolved_list = await self._enrich_usergroups([single])
                    if resolved_list:
                        enriched['usergroup'] = resolved_list[0]
                groups_list = data.get('usergroups')
                if isinstance(groups_list, list):
                    enriched['usergroups'] = await self._enrich_usergroups(groups_list)
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Usergroup-info enrichment failed: {enrichment_err}")
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

            # Resolve DM partner (`user`) on IM entries and `creator` on channels
            try:
                all_channels = await self._enrich_conversations(all_channels)
            except Exception as enrichment_err:
                logger.debug(f"get_user_channels enrichment failed: {enrichment_err}")

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
            chan = await self._resolve_channel(channel)
            kwargs = {
                "channel": chan,
                "timestamp": timestamp
            }
            if full is not None:
                kwargs["full"] = full

            response = await self.client.reactions_get(**kwargs)
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())

            # reactions.get returns the item under one of: 'message', 'file',
            # or 'file_comment' depending on `type`. The reactions[] array
            # (each with .users[]) lives on whichever item is present, plus
            # the message itself has 'user'. We enrich whichever item is set.
            try:
                data = slack_response.data
                if not isinstance(data, dict):
                    return (slack_response.success, slack_response.to_json())
                enriched = dict(data)
                # Message-typed reaction: enrich as a normal message
                msg = enriched.get('message')
                if isinstance(msg, dict):
                    enriched['message'] = (await self._enrich_messages([msg]))[0]
                # File-typed reaction: collect uploader + reaction users
                file_obj = enriched.get('file')
                if isinstance(file_obj, dict):
                    enriched['file'] = await self._enrich_reactable(file_obj)
                # File-comment reaction: comment has user + reactions
                comment = enriched.get('file_comment') or enriched.get('comment')
                if isinstance(comment, dict):
                    new_comment = await self._enrich_reactable(comment)
                    # Write back under whichever key it was loaded from
                    if 'file_comment' in enriched:
                        enriched['file_comment'] = new_comment
                    else:
                        enriched['comment'] = new_comment
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Reactions enrichment failed: {enrichment_err}")
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
            chan = await self._resolve_channel(channel)
            response = await self.client.pins_list(channel=chan)
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())

            # Each pin item is type=message|file|file_comment with user IDs at
            # items[].created_by (pinner), items[].message.user (author),
            # items[].file.user (uploader), items[].comment.user.
            try:
                data = slack_response.data
                items = data.get('items', []) if isinstance(data, dict) else []
                resolved_items = await self._enrich_pin_items(items)
                enriched = dict(data) if isinstance(data, dict) else {}
                enriched['items'] = resolved_items
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Pinned-message enrichment failed: {enrichment_err}")
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
    async def get_thread_replies(
        self,
        channel: str,
        timestamp: str,
        limit: Optional[int] = None,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        inclusive: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get replies in a thread"""
        """
        Args:
            channel: The channel containing the thread
            timestamp: Timestamp of the parent message
            limit: Maximum number of replies to return
            oldest: Start of time range (ISO 8601 date or datetime string)
            latest: End of time range (ISO 8601 date or datetime string)
            inclusive: Include messages with exact oldest/latest timestamps
        Returns:
            A tuple with a boolean indicating success/failure and a JSON string with the thread replies
        """
        try:
            # Convert LLM-supplied ISO dates -> Slack epoch-string format.
            try:
                oldest_ts = _iso_to_slack_ts(oldest)
                latest_ts = _iso_to_slack_ts(latest)
            except SlackDateError as date_err:
                return (False, SlackResponse(success=False, error=str(date_err)).to_json())

            # Resolve channel name like '#bugs' to a channel ID — the LLM
            # sometimes passes a name even though the schema asks for an ID.
            chan = await self._resolve_channel(channel)

            kwargs: Dict[str, Any] = {
                "channel": chan,
                "ts": timestamp,
            }
            if limit is not None:
                kwargs["limit"] = limit
            if oldest_ts is not None:
                kwargs["oldest"] = oldest_ts
            if latest_ts is not None:
                kwargs["latest"] = latest_ts
            if inclusive is not None:
                kwargs["inclusive"] = inclusive

            response = await self.client.conversations_replies(**kwargs)
            slack_response = self._handle_slack_response(response)
            if not slack_response.success or not slack_response.data:
                return (slack_response.success, slack_response.to_json())

            # Resolve user IDs in every thread message: text mentions, author,
            # parent_user_id, reactions[].users. Fall back to raw payload on error.
            try:
                data = slack_response.data
                messages = data.get('messages', []) if isinstance(data, dict) else []
                resolved_messages = await self._enrich_messages(messages)
                enriched = dict(data) if isinstance(data, dict) else {}
                enriched['messages'] = resolved_messages
                return (True, SlackResponse(success=True, data=enriched).to_json())
            except Exception as enrichment_err:
                logger.debug(f"Thread reply enrichment failed: {enrichment_err}")
                return (slack_response.success, slack_response.to_json())
        except Exception as e:
            logger.error(f"Error in get_thread_replies: {e}")
            slack_response = self._handle_slack_error(e)
            return (slack_response.success, slack_response.to_json())

    @tool(
        app_name="slack",
        tool_name="upload_file_to_channel",
        description="Upload a text file to a Slack channel as a snippet",
        args_schema=UploadFileToChannelInput,
        when_to_use=[
            "User wants to upload a file or transcript to Slack",
            "User wants to share a meeting transcript in a Slack channel",
            "User wants to post a text snippet or log to Slack",
        ],
        when_not_to_use=[
            "User wants to send a plain text message (use send_message)",
            "User wants to search for files (use search_all)",
            "No Slack mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Upload the meeting transcript to #general",
            "Share the Teams call transcript in Slack",
            "Post this file to the engineering channel",
        ],
        category=ToolCategory.COMMUNICATION,
        llm_description=(
            "Upload a text file (transcript, log, snippet) to a Slack channel. "
            "Pass the full file content in file_content. The file is hosted in "
            "Slack and rendered as a collapsible snippet with syntax highlighting."
        ),
    )
    async def upload_file_to_channel(
        self,
        channel: str,
        filename: str,
        file_content: str,
        title: Optional[str] = None,
        initial_comment: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Upload a text file to a Slack channel.

        Uses the Slack SDK's files_upload_v2 which handles the 3-step upload
        API internally (getUploadURLExternal -> POST -> completeUploadExternal).

        Channel is resolved from name to ID before calling the SDK because
        files_upload_v2 requires a channel ID — channel names will fail.

        Args:
            channel: Channel name (with or without #) or channel ID.
            filename: File name with extension (e.g. 'transcript.txt').
            file_content: Full text content to upload.
            title: Optional display title in Slack.
            initial_comment: Optional message alongside the file.

        Returns:
            Tuple (success: bool, json_response: str)
        """
        try:
            # Resolve channel name -> channel ID
            # CRITICAL: files_upload_v2 requires channel ID, not name.
            # Passing '#general' or 'general' returns 'invalid_channel'.
            chan = await self._resolve_channel(channel)

            # SDK param is 'content' for string data (not 'file_content')
            response = await self.client.files_upload_v2(
                filename=filename,
                content=file_content,
                channel=chan,
                title=title,
                initial_comment=initial_comment,
            )
            slack_response = self._handle_slack_response(response)
            return (slack_response.success, slack_response.to_json())
        except Exception as e:
            if "not_in_channel" in str(e):
                err = SlackResponse(success=False, error="not_in_channel")
                return (err.success, err.to_json())
            logger.error(f"Error in upload_file_to_channel: {e}")
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

    async def _resolve_user_ids(
        self,
        user_ids: set,
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """Bulk-resolve a set of Slack user IDs to {display_name, real_name, email}.

        Hits the per-instance cache first and only fetches misses, fanning out
        users_info calls in parallel with a concurrency cap so a busy channel
        with many distinct authors doesn't serialize on network round-trips.

        Returns a dict keyed by the user IDs that were requested. Failures
        for a given ID fall back to {display_name: id, real_name: None, email: None}
        so callers can always look up the input ID without a None-check.
        """
        if not user_ids:
            return {}

        missing = [uid for uid in user_ids if uid not in self._user_cache]

        if missing:
            async def _fetch_one(uid: str) -> Tuple[str, Optional[Dict[str, Optional[str]]]]:
                async with self._lookup_sem:
                    try:
                        uresp = await self.client.users_info(user=uid)
                        u = self._handle_slack_response(uresp)
                        if u.success and u.data and isinstance(u.data, dict):
                            user_obj = u.data.get('user') or {}
                            profile = user_obj.get('profile') or {}
                            display = (
                                profile.get('display_name')
                                or user_obj.get('real_name')
                                or user_obj.get('name')
                                or uid
                            )
                            return uid, {
                                'display_name': display,
                                'real_name': user_obj.get('real_name'),
                                'email': profile.get('email'),
                            }
                    except Exception as e:
                        logger.debug(f"users_info failed for {uid}: {e}")

                return uid, None

            results = await asyncio.gather(*(_fetch_one(uid) for uid in missing))
            for uid, info in results:
                if info is not None:
                    self._user_cache[uid] = info

        # Fall back at read time so callers always get a usable label,
        # but the next call gets a chance to actually resolve the ID.
        fallback = {'display_name': None, 'real_name': None, 'email': None}
        return {
            uid: self._user_cache.get(uid, {**fallback, 'display_name': uid})
            for uid in user_ids
        }

    async def _resolve_channel_ids(self, channel_ids: set) -> Dict[str, str]:
        """Bulk-resolve Slack channel IDs (C…/G…/D…) to display names.

        Mirrors `_resolve_user_ids` — cache-first, parallel `conversations.info`
        for misses. DM (`is_im`) channels lack `name`, so we synthesize
        `DM:<partner_uid>` from the channel's `user` field; falls back to the
        raw ID on any failure so callers always get a usable label.
        """
        if not channel_ids:
            return {}
        missing = [cid for cid in channel_ids if cid not in self._channel_cache]
        if missing:
            async def _fetch_one(cid: str) -> Tuple[str, Optional[str]]:
                async with self._lookup_sem:
                    try:
                        cresp = await self.client.conversations_info(channel=cid)
                        c = self._handle_slack_response(cresp)
                        if c.success and c.data and isinstance(c.data, dict):
                            chan = c.data.get('channel') or {}
                            name = chan.get('name')
                            if not name and chan.get('is_im'):
                                partner = chan.get('user')
                                name = f"DM:{partner}" if partner else cid
                            return cid, name or cid
                    except Exception as e:
                        logger.debug(f"conversations_info failed for {cid}: {e}")
                return cid, None

            results = await asyncio.gather(*(_fetch_one(cid) for cid in missing))
            for cid, name in results:
                if name is not None:
                    self._channel_cache[cid] = name
        return {cid: self._channel_cache.get(cid, cid) for cid in channel_ids}

    # ------------------------------------------------------------------
    # Enrichment helpers — collect + resolve + apply, by response shape
    # ------------------------------------------------------------------
    #
    # Slack tools historically returned raw 'U…'/'W…' user IDs in tool
    # responses, forcing the LLM to follow up with users_info calls just
    # to print a name. The helpers below take a Slack response fragment
    # (a message, list of messages, list of pinned items, etc.), collect
    # every user-ID-bearing field, bulk-resolve via _resolve_user_ids,
    # and inject *_display_name / *_email fields without removing the
    # original IDs (so existing consumers keep working).

    def _collect_user_ids_from_message(self, msg: Any, ids: set) -> None:  # noqa: ANN401
        """Add every Slack user ID found in a single message dict to `ids`.

        Looks at: inline <@U…> mentions in `text`, the message author (`user`),
        the thread parent (`parent_user_id`), reply summaries (`replies[].user`),
        the thread-parent reply roster (`reply_users[]` — per
        https://docs.slack.dev/reference/methods/conversations.replies; may
        contain bot IDs which `_is_user_id` filters out), reactions
        (`reactions[].users[]`), file attachments (`files[].user` — the
        uploader, per https://docs.slack.dev/reference/objects/file-object),
        edits (`edited.user` — the editor, per the message event docs), and
        the huddle `room` block on `subtype=huddle_thread` messages
        (`room.created_by`, `room.participant_history[]`).
        """
        if not isinstance(msg, dict):
            return
        text = msg.get('text')
        if isinstance(text, str):
            ids.update(SLACK_MENTION_RE.findall(text))
        for field in ('user', 'parent_user_id'):
            v = msg.get(field)
            if _is_user_id(v):
                ids.add(v)
        for rep in (msg.get('replies') or []):
            if isinstance(rep, dict):
                v = rep.get('user')
                if _is_user_id(v):
                    ids.add(v)
        for u in (msg.get('reply_users') or []):
            if _is_user_id(u):
                ids.add(u)
        for r in (msg.get('reactions') or []):
            if isinstance(r, dict):
                for u in (r.get('users') or []):
                    if _is_user_id(u):
                        ids.add(u)
        for f in (msg.get('files') or []):
            if isinstance(f, dict):
                fu = f.get('user')
                if _is_user_id(fu):
                    ids.add(fu)
        edited = msg.get('edited')
        if isinstance(edited, dict):
            eu = edited.get('user')
            if _is_user_id(eu):
                ids.add(eu)
        room = msg.get('room')
        if isinstance(room, dict):
            cb = room.get('created_by')
            if _is_user_id(cb):
                ids.add(cb)
            for u in (room.get('participant_history') or []):
                if _is_user_id(u):
                    ids.add(u)

    def _collect_channel_ids_from_message(self, msg: Any, channel_ids: set) -> None:  # noqa: ANN401
        """Collect Slack channel IDs (C…/G…/D…) from a single message dict.

        Looks at the top-level `channel` field and `room.channels[]` on
        huddle_thread messages.
        """
        if not isinstance(msg, dict):
            return
        ch = msg.get('channel')
        if isinstance(ch, str) and len(ch) >= MIN_SLACK_USER_ID_LENGTH and ch[0] in ('C', 'G', 'D'):
            channel_ids.add(ch)
        room = msg.get('room')
        if isinstance(room, dict):
            for c in (room.get('channels') or []):
                if isinstance(c, str) and len(c) >= MIN_SLACK_USER_ID_LENGTH and c[0] in ('C', 'G', 'D'):
                    channel_ids.add(c)

    def _apply_resolution_to_message(
        self,
        msg: Any,  # noqa: ANN401
        id_to_name: Dict[str, str],
        id_to_email: Dict[str, str],
        channel_id_to_name: Optional[Dict[str, str]] = None,
    ) -> Any:  # noqa: ANN401
        """Return a shallow-copied message with resolved fields injected.

        Adds: `resolved_text`, `mentions[]`, `user_display_name`, `user_email`,
        `parent_user_display_name`, `replies[].user_display_name`,
        `reply_users_display_names[]` (parent thread roster),
        per-reaction `user_display_names`, `files[].user_display_name`/`user_email`
        (file uploader), `edited.user_display_name` (editor), `channel_name`
        (top-level), and `room.created_by_display_name` /
        `room.participant_history_display_names[]` / `room.channel_names[]` on
        huddle_thread messages.
        """
        channel_id_to_name = channel_id_to_name or {}
        if not isinstance(msg, dict):
            return msg
        new_msg = dict(msg)

        # Top-level message epoch fields (ts, thread_ts, latest_reply).
        for field in _EPOCH_FIELDS_ON_MESSAGE:
            _add_iso_date_sibling(new_msg, field)

        text = new_msg.get('text')
        if isinstance(text, str):
            def _rep(m: re.Match) -> str:
                return f"@{id_to_name.get(m.group(1), m.group(1))}"
            new_msg['resolved_text'] = SLACK_MENTION_RE.sub(_rep, text)
            mentions_meta = []
            for uid in SLACK_MENTION_RE.findall(text):
                mentions_meta.append({
                    'id': uid,
                    'display_name': id_to_name.get(uid),
                    'email': id_to_email.get(uid),
                })
            if mentions_meta:
                new_msg['mentions'] = mentions_meta

        author = new_msg.get('user')
        if isinstance(author, str) and author in id_to_name:
            new_msg['user_display_name'] = id_to_name[author]
            if author in id_to_email:
                new_msg['user_email'] = id_to_email[author]

        parent_uid = new_msg.get('parent_user_id')
        if isinstance(parent_uid, str) and parent_uid in id_to_name:
            new_msg['parent_user_display_name'] = id_to_name[parent_uid]

        replies = new_msg.get('replies')
        if isinstance(replies, list):
            new_replies = []
            for rep in replies:
                if isinstance(rep, dict):
                    new_rep = dict(rep)
                    ru = new_rep.get('user')
                    if isinstance(ru, str) and ru in id_to_name:
                        new_rep['user_display_name'] = id_to_name[ru]
                    # Reply-summary entries carry their own `ts` epoch.
                    _add_iso_date_sibling(new_rep, 'ts')
                    new_replies.append(new_rep)
                else:
                    new_replies.append(rep)
            new_msg['replies'] = new_replies

        reply_users = new_msg.get('reply_users')
        if isinstance(reply_users, list):
            new_msg['reply_users_display_names'] = [
                id_to_name.get(u, u) for u in reply_users if isinstance(u, str)
            ]

        ch = new_msg.get('channel')
        if isinstance(ch, str) and ch in channel_id_to_name:
            new_msg['channel_name'] = channel_id_to_name[ch]

        reactions = new_msg.get('reactions')
        if isinstance(reactions, list):
            new_reactions = []
            for r in reactions:
                if isinstance(r, dict):
                    new_r = dict(r)
                    user_list = new_r.get('users')
                    if isinstance(user_list, list):
                        new_r['user_display_names'] = [
                            id_to_name.get(u, u) for u in user_list if isinstance(u, str)
                        ]
                    new_reactions.append(new_r)
                else:
                    new_reactions.append(r)
            new_msg['reactions'] = new_reactions

        files = new_msg.get('files')
        if isinstance(files, list):
            new_files = []
            for f in files:
                if isinstance(f, dict):
                    new_f = dict(f)
                    fu = new_f.get('user')
                    if isinstance(fu, str) and fu in id_to_name:
                        new_f['user_display_name'] = id_to_name[fu]
                        if fu in id_to_email:
                            new_f['user_email'] = id_to_email[fu]
                    # Files carry epoch fields: `created`, `timestamp`, `updated`.
                    for f_field in _EPOCH_FIELDS_ON_FILE:
                        _add_iso_date_sibling(new_f, f_field)
                    new_files.append(new_f)
                else:
                    new_files.append(f)
            new_msg['files'] = new_files

        edited = new_msg.get('edited')
        if isinstance(edited, dict):
            new_edited = dict(edited)
            eu = new_edited.get('user')
            if isinstance(eu, str) and eu in id_to_name:
                new_edited['user_display_name'] = id_to_name[eu]
            # `edited.ts` is the edit timestamp.
            _add_iso_date_sibling(new_edited, 'ts')
            new_msg['edited'] = new_edited

        # Huddle/call rooms (subtype=huddle_thread) carry their own epoch fields,
        # plus user IDs (`created_by`, `participant_history[]`) and channel IDs
        # (`channels[]`).
        room = new_msg.get('room')
        if isinstance(room, dict):
            new_room = dict(room)
            for r_field in _EPOCH_FIELDS_ON_ROOM:
                _add_iso_date_sibling(new_room, r_field)
            cb = new_room.get('created_by')
            if isinstance(cb, str) and cb in id_to_name:
                new_room['created_by_display_name'] = id_to_name[cb]
            phist = new_room.get('participant_history')
            if isinstance(phist, list):
                new_room['participant_history_display_names'] = [
                    id_to_name.get(u, u) for u in phist if isinstance(u, str)
                ]
            rch = new_room.get('channels')
            if isinstance(rch, list):
                new_room['channel_names'] = [
                    channel_id_to_name.get(c, c) for c in rch if isinstance(c, str)
                ]
            new_msg['room'] = new_room

        return new_msg

    async def _enrich_reactable(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a `{user, reactions[].users[]}`-shaped item (file / file_comment).

        Returns a copy with `user_display_name` injected on the top-level user
        and `user_display_names` arrays added to each reaction. If the item
        carries no resolvable user IDs, returns it unchanged.
        """
        ids: set = set()
        u = obj.get('user')
        if _is_user_id(u):
            ids.add(u)
        for r in (obj.get('reactions') or []):
            if isinstance(r, dict):
                for ru in (r.get('users') or []):
                    if _is_user_id(ru):
                        ids.add(ru)
        if not ids:
            return obj
        resolved = await self._resolve_user_ids(ids)
        id_to_name = {
            uid: info['display_name']
            for uid, info in resolved.items()
            if info.get('display_name')
        }
        new_obj = dict(obj)
        if isinstance(u, str) and u in id_to_name:
            new_obj['user_display_name'] = id_to_name[u]
        new_reactions = []
        for r in (new_obj.get('reactions') or []):
            if isinstance(r, dict):
                new_r = dict(r)
                user_list = new_r.get('users')
                if isinstance(user_list, list):
                    new_r['user_display_names'] = [
                        id_to_name.get(ru, ru) for ru in user_list if isinstance(ru, str)
                    ]
                new_reactions.append(new_r)
            else:
                new_reactions.append(r)
        if new_obj.get('reactions') is not None:
            new_obj['reactions'] = new_reactions
        return new_obj

    async def _enrich_messages(self, messages: Optional[List[Any]]) -> List[Any]:
        """Resolve user + channel IDs across a list of Slack messages and return enriched copies."""
        if not messages:
            return messages or []
        ids: set = set()
        channel_ids: set = set()
        for msg in messages:
            self._collect_user_ids_from_message(msg, ids)
            self._collect_channel_ids_from_message(msg, channel_ids)
        resolved, channel_id_to_name = await asyncio.gather(
            self._resolve_user_ids(ids),
            self._resolve_channel_ids(channel_ids),
        )
        id_to_name = {
            uid: info['display_name']
            for uid, info in resolved.items()
            if info.get('display_name')
        }
        id_to_email = {
            uid: info['email']
            for uid, info in resolved.items()
            if info.get('email')
        }
        return [
            self._apply_resolution_to_message(m, id_to_name, id_to_email, channel_id_to_name)
            for m in messages
        ]

    async def _resolve_user_id_list(self, ids: Optional[List[Any]]) -> List[Dict[str, Optional[str]]]:
        """Resolve a flat list of user IDs into a list of `{id, display_name, real_name, email}`.

        Used for `conversations.members`, `usergroup.users[]`, `reactions[].users[]`.
        Preserves order; silently skips non-string entries.
        """
        if not ids:
            return []
        deduped = {uid for uid in ids if _is_user_id(uid)}
        resolved = await self._resolve_user_ids(deduped)
        out: List[Dict[str, Optional[str]]] = []
        for uid in ids:
            if not isinstance(uid, str):
                continue
            info = resolved.get(uid) or {}
            out.append({
                'id': uid,
                'display_name': info.get('display_name') or uid,
                'real_name': info.get('real_name'),
                'email': info.get('email'),
            })
        return out

    async def _enrich_pin_items(self, items: Optional[List[Any]]) -> List[Any]:
        """Resolve user IDs in a list of pins.list items.

        Resolves: `created_by` (pinner), `message.user` + nested message fields,
        `file.user` (uploader for file pins), `comment.user` (file_comment pins).
        """
        if not items:
            return items or []
        ids: set = set()
        for it in items:
            if not isinstance(it, dict):
                continue
            cb = it.get('created_by')
            if _is_user_id(cb):
                ids.add(cb)
            msg = it.get('message')
            if isinstance(msg, dict):
                self._collect_user_ids_from_message(msg, ids)
            f = it.get('file')
            if isinstance(f, dict):
                fu = f.get('user')
                if _is_user_id(fu):
                    ids.add(fu)
            c = it.get('comment')
            if isinstance(c, dict):
                cu = c.get('user')
                if _is_user_id(cu):
                    ids.add(cu)
        resolved = await self._resolve_user_ids(ids)
        id_to_name = {
            uid: info['display_name']
            for uid, info in resolved.items()
            if info.get('display_name')
        }
        id_to_email = {
            uid: info['email']
            for uid, info in resolved.items()
            if info.get('email')
        }

        out = []
        for it in items:
            if not isinstance(it, dict):
                out.append(it)
                continue
            new_it = dict(it)
            cb = new_it.get('created_by')
            if isinstance(cb, str) and cb in id_to_name:
                new_it['created_by_display_name'] = id_to_name[cb]
            # Pin items themselves carry a `created` epoch (when the pin
            # was added). The nested message / file have their own epoch
            # fields handled in their respective enrichment branches below.
            for it_field in _EPOCH_FIELDS_ON_PIN_ITEM:
                _add_iso_date_sibling(new_it, it_field)
            msg = new_it.get('message')
            if isinstance(msg, dict):
                new_it['message'] = self._apply_resolution_to_message(msg, id_to_name, id_to_email)
            f = new_it.get('file')
            if isinstance(f, dict):
                new_f = dict(f)
                fu = new_f.get('user')
                if isinstance(fu, str) and fu in id_to_name:
                    new_f['user_display_name'] = id_to_name[fu]
                    if fu in id_to_email:
                        new_f['user_email'] = id_to_email[fu]
                for f_field in _EPOCH_FIELDS_ON_FILE:
                    _add_iso_date_sibling(new_f, f_field)
                new_it['file'] = new_f
            c = new_it.get('comment')
            if isinstance(c, dict):
                new_c = dict(c)
                cu = new_c.get('user')
                if isinstance(cu, str) and cu in id_to_name:
                    new_c['user_display_name'] = id_to_name[cu]
                # File-comment pins carry a `created`/`timestamp` epoch.
                _add_iso_date_sibling(new_c, 'created')
                _add_iso_date_sibling(new_c, 'timestamp')
                new_it['comment'] = new_c
            out.append(new_it)
        return out

    async def _enrich_conversations(self, conversations: Optional[List[Any]]) -> List[Any]:
        """Add user_display_name (DM partner) and creator_display_name to channel entries.

        IM (DM) entries carry `user` (the partner's ID); regular channels carry `creator`.
        """
        if not conversations:
            return conversations or []
        ids: set = set()
        for c in conversations:
            if not isinstance(c, dict):
                continue
            for field in ('user', 'creator'):
                v = c.get(field)
                if _is_user_id(v):
                    ids.add(v)
        # Resolve user IDs only if there are any. Date enrichment runs
        # unconditionally below — every channel has at least `created`.
        if ids:
            resolved = await self._resolve_user_ids(ids)
            id_to_name = {
                uid: info['display_name']
                for uid, info in resolved.items()
                if info.get('display_name')
            }
            id_to_email = {
                uid: info['email']
                for uid, info in resolved.items()
                if info.get('email')
            }
        else:
            id_to_name = {}
            id_to_email = {}

        out = []
        for c in conversations:
            if not isinstance(c, dict):
                out.append(c)
                continue
            new_c = dict(c)
            partner = new_c.get('user')
            if isinstance(partner, str) and partner in id_to_name:
                new_c['user_display_name'] = id_to_name[partner]
                if partner in id_to_email:
                    new_c['user_email'] = id_to_email[partner]
            creator = new_c.get('creator')
            if isinstance(creator, str) and creator in id_to_name:
                new_c['creator_display_name'] = id_to_name[creator]
            # Channel-level epoch fields (`created`, `last_read`).
            for c_field in _EPOCH_FIELDS_ON_CONVERSATION:
                _add_iso_date_sibling(new_c, c_field)
            # `latest` is a sub-message preview with its own `ts`.
            latest = new_c.get('latest')
            if isinstance(latest, dict):
                new_latest = dict(latest)
                _add_iso_date_sibling(new_latest, 'ts')
                new_c['latest'] = new_latest
            out.append(new_c)
        return out

    async def _enrich_usergroups(self, usergroups: Optional[List[Any]]) -> List[Any]:
        """Resolve `created_by`, `updated_by`, `deleted_by`, and `users[]` per usergroup.

        Adds *_display_name siblings for the by-fields and a `resolved_users[]` array
        of `{id, display_name, real_name, email}` next to the original `users[]`.
        """
        if not usergroups:
            return usergroups or []
        ids: set = set()
        for g in usergroups:
            if not isinstance(g, dict):
                continue
            for field in ('created_by', 'updated_by', 'deleted_by'):
                v = g.get(field)
                if _is_user_id(v):
                    ids.add(v)
            for u in (g.get('users') or []):
                if _is_user_id(u):
                    ids.add(u)
        if not ids:
            return usergroups
        resolved = await self._resolve_user_ids(ids)
        id_to_name = {
            uid: info['display_name']
            for uid, info in resolved.items()
            if info.get('display_name')
        }
        id_to_email = {
            uid: info['email']
            for uid, info in resolved.items()
            if info.get('email')
        }

        out = []
        for g in usergroups:
            if not isinstance(g, dict):
                out.append(g)
                continue
            new_g = dict(g)
            for field, label in (
                ('created_by', 'created_by_display_name'),
                ('updated_by', 'updated_by_display_name'),
                ('deleted_by', 'deleted_by_display_name'),
            ):
                v = new_g.get(field)
                if isinstance(v, str) and v in id_to_name:
                    new_g[label] = id_to_name[v]
            users = new_g.get('users')
            if isinstance(users, list):
                new_g['resolved_users'] = [
                    {
                        'id': u,
                        'display_name': id_to_name.get(u, u),
                        'email': id_to_email.get(u),
                    }
                    for u in users if isinstance(u, str)
                ]
            out.append(new_g)
        return out

    async def _enrich_search_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve user IDs inside a Slack search response.

        Handles both `search.messages` and `search.all` shapes:
          - `messages.matches[]` — message dicts with `user`, `text` (mentions),
            and `channel.{id,name}`. Enriched via `_enrich_messages`.
          - `files.matches[]` — file dicts with `user` (uploader). Enriched
            with `user_display_name` / `user_email` siblings.
          - `posts.matches[]` — currently undocumented user fields; passed through.
        """
        if not isinstance(response, dict):
            return response
        new_resp = dict(response)

        msgs_section = new_resp.get('messages')
        if isinstance(msgs_section, dict):
            matches = msgs_section.get('matches')
            if isinstance(matches, list):
                new_section = dict(msgs_section)
                new_section['matches'] = await self._enrich_messages(matches)
                new_resp['messages'] = new_section

        files_section = new_resp.get('files')
        if isinstance(files_section, dict):
            matches = files_section.get('matches')
            if isinstance(matches, list):
                ids: set = set()
                for f in matches:
                    if isinstance(f, dict):
                        fu = f.get('user')
                        if _is_user_id(fu):
                            ids.add(fu)
                if ids:
                    resolved = await self._resolve_user_ids(ids)
                    id_to_name = {
                        uid: info['display_name']
                        for uid, info in resolved.items()
                        if info.get('display_name')
                    }
                    id_to_email = {
                        uid: info['email']
                        for uid, info in resolved.items()
                        if info.get('email')
                    }
                    new_matches = []
                    for f in matches:
                        if isinstance(f, dict):
                            new_f = dict(f)
                            fu = new_f.get('user')
                            if isinstance(fu, str) and fu in id_to_name:
                                new_f['user_display_name'] = id_to_name[fu]
                                if fu in id_to_email:
                                    new_f['user_email'] = id_to_email[fu]
                            new_matches.append(new_f)
                        else:
                            new_matches.append(f)
                    new_section = dict(files_section)
                    new_section['matches'] = new_matches
                    new_resp['files'] = new_section

        return new_resp

    async def _resolve_user_handle(self, user_identifier: str) -> Optional[str]:
        """Resolve a user identifier to the Slack username/handle.

        Slack search modifiers (`from:@handle`, `with:@handle`) require the username,
        not the user ID. This helper resolves any identifier (email, display name,
        real name, or user ID) to the underlying handle via users_info.

        Returns the handle string on success, or None if resolution fails.
        Raises AmbiguousUserError when the identifier matches multiple users.
        """
        user_id = await self._resolve_user_identifier(user_identifier, allow_ambiguous=False)
        if not user_id:
            return None
        try:
            response = await self.client.users_info(user=user_id)
            slack_response = self._handle_slack_response(response)
            if slack_response.success and slack_response.data:
                user_obj = slack_response.data.get('user') or {}
                handle = user_obj.get('name')
                if isinstance(handle, str) and handle:
                    return handle
        except Exception as e:
            logger.debug(f"Failed to fetch handle for '{user_identifier}' (id={user_id}): {e}")
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
