import json
import logging
import asyncio
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.teams.teams import TeamsDataSource

from msgraph.generated.models.patterned_recurrence import PatternedRecurrence
from msgraph.generated.models.recurrence_pattern import RecurrencePattern
from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
from msgraph.generated.models.recurrence_range import RecurrenceRange
from msgraph.generated.models.recurrence_range_type import RecurrenceRangeType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SendChannelMessageInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message: str = Field(description="Message content to send")


class SendUserMessageInput(BaseModel):
    user_identifier: str = Field(
        description="User display name, email, user principal name, or user id to send message to"
    )
    message: str = Field(description="Message content to send")


class GetTeamsInput(BaseModel):
    top: Optional[int] = Field(default=20, description="Maximum number of teams to return (default 20, max 100)")


class GetTeamInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")


class GetChannelsInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    top: Optional[int] = Field(default=50, description="Maximum number of channels to return (default 50, max 200)")


class GetChannelMessagesInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    top: Optional[int] = Field(default=20, description="Maximum number of messages to return (default 20, max 100)")


class AddReactionInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message_id: str = Field(description="ID of the Teams message to react to")
    reaction_type: str = Field(
        description="Reaction type to add (for example: like, heart, laugh, surprised, sad, angry)"
    )
class SearchCalendarEventsInRangeInput(BaseModel):
    """Schema for searching calendar events by name within a time frame."""
    keyword: str = Field(
        description=(
            "Partial or full name to search for in event subjects. "
            "Case-insensitive. E.g. 'standup', 'catchup', 'sprint'."
        ),
    )
    start_datetime: str = Field(
        description="Start of time range (ISO 8601, e.g. '2026-03-01T00:00:00Z').",
    )
    end_datetime: str = Field(
        description="End of time range (ISO 8601, e.g. '2026-03-31T23:59:59Z').",
    )
    timezone: str = Field(
        default="UTC",
        description="Windows timezone name for returned datetimes. E.g. 'India Standard Time'.",
    )
    top: int = Field(
        default=10,
        description="Maximum number of results to return (1–50).",
        ge=1,
        le=50,
    )

class ReplyToMessageInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    parent_message_id: str = Field(description="ID of the parent message to reply to")
    message: str = Field(description="Reply text to post in the message thread")


class SendMessageToMultipleChannelsInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_ids: List[str] = Field(description="List of Teams channel IDs to send the same message to")
    message: str = Field(description="Message text to send to all channels")


class SearchMessagesInput(BaseModel):
    query: str = Field(description="Search text to match in message content")
    team_id: Optional[str] = Field(default=None, description="Optional Team ID to scope search")
    channel_id: Optional[str] = Field(default=None, description="Optional Channel ID to scope search")
    top_per_channel: Optional[int] = Field(default=25, description="Max recent messages scanned per channel (default 25)")


# class GetUserConversationsInput(BaseModel):
#     top: Optional[int] = Field(default=100, description="Maximum number of conversations to return (default 100)")

class GetUserConversationsInput(BaseModel):

    user_identifier: Optional[str] = Field(
        default=None,
        description="User display name, email, or user id whose conversation should be fetched"
    )

    minutes: Optional[int] = Field(
        default=None,
        description="Fetch messages from last N minutes"
    )

    hours: Optional[int] = Field(
        default=None,
        description="Fetch messages from last N hours"
    )

    days: Optional[int] = Field(
        default=None,
        description="Fetch messages from last N days"
    )

    top: Optional[int] = Field(
        default=50,
        description="Maximum number of messages to return"
    )

class GetUserChannelsInput(BaseModel):
    team_id: Optional[str] = Field(default=None, description="Optional Team ID to scope channels to one team")
    top: Optional[int] = Field(default=200, description="Maximum number of channels to return (default 200)")


class UpdateMessageInput(BaseModel):
    team_id: Optional[str] = Field(
        default=None,
        description="ID of the Microsoft Team (required when updating a channel message)",
    )
    channel_id: Optional[str] = Field(
        default=None,
        description="ID of the Microsoft Teams channel (required when updating a channel message)",
    )
    chat_id: Optional[str] = Field(
        default=None,
        description="ID of the Teams chat (required when updating a direct chat message)",
    )
    message_id: str = Field(description="ID of the Teams message to update")
    message: str = Field(description="Updated message text")


class GetMessagePermalinkInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message_id: str = Field(description="ID of the Teams message")


class GetReactionsInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message_id: str = Field(description="ID of the Teams message")


class RemoveReactionInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message_id: str = Field(description="ID of the Teams message")
    reaction_type: str = Field(description="Reaction type to remove")


class GetThreadRepliesInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the Microsoft Teams channel")
    message_id: str = Field(description="ID of the parent Teams message")
    top: Optional[int] = Field(default=50, description="Maximum number of replies to return (default 50, max 200)")


class GetMyMeetingsInput(BaseModel):
    top: Optional[int] = Field(default=50, description="Maximum number of meetings to return (default 50, max 500)")


class GetMyRecurringMeetingsInput(BaseModel):
    top: Optional[int] = Field(default=50, description="Maximum number of recurring meetings to return (default 50, max 500)")


class GetMyMeetingsForGivenPeriodInput(BaseModel):
    start_datetime: str = Field(description="Start datetime (ISO 8601)")
    end_datetime: str = Field(description="End datetime (ISO 8601)")
    top: Optional[int] = Field(default=100, description="Maximum number of meetings to return (default 100, max 1000)")


class GetMyRecurringMeetingsForGivenPeriodInput(BaseModel):
    start_datetime: str = Field(description="Start datetime (ISO 8601)")
    end_datetime: str = Field(description="End datetime (ISO 8601)")
    top: Optional[int] = Field(default=100, description="Maximum number of recurring meetings to return (default 100, max 1000)")


class GetMeetingsInput(BaseModel):
    start_datetime: Optional[str] = Field(
        default=None,
        description="Optional start datetime (ISO 8601). Must be provided together with end_datetime.",
    )
    end_datetime: Optional[str] = Field(
        default=None,
        description="Optional end datetime (ISO 8601). Must be provided together with start_datetime.",
    )
    is_deleted: Optional[bool] = Field(
        default=None,
        description="Strict filter for deleted meetings. true=only deleted, false=only non-deleted, null=do not filter.",
    )
    is_cancelled: Optional[bool] = Field(
        default=None,
        description="Strict filter for cancelled meetings. true=only cancelled, false=only non-cancelled, null=do not filter.",
    )
    meeting_type: Optional[str] = Field(
        default=None,
        description="Optional strict meeting type filter. "
        "Allowed values: 'recurring' or 'one_time'. "
        "Only include this field if the user explicitly asks for recurring meetings or one-time meetings. "
        "Otherwise omit this field.")
    top: Optional[int] = Field(
        default=100,
        description="Maximum number of meetings to return (default 100, max 1000).",
    )


class GetMeetingTranscriptsInput(BaseModel):
    """Schema for fetching transcripts of an online meeting.

    Provide meeting_id directly when available.
    Otherwise provide EITHER join_url (preferred, skips one API call) OR event_id.
    join_url is available as event.onlineMeeting.joinUrl on any calendar
    event returned by get_calendar_events or search_calendar_events.
    """
    meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "The online meeting ID. If provided, transcript APIs are called directly "
            "without resolving from join_url/event_id."
        ),
    )
    join_url: Optional[str] = Field(
        default=None,
        description=(
            "The Teams join URL (joinUrl from event.onlineMeeting.joinUrl). "
            "Preferred over event_id — skips one API call."
        ),
    )
    event_id: Optional[str] = Field(
        default=None,
        description=(
            "The calendar event ID. Used as fallback when join_url is not available. "
            "The tool will fetch the event to extract joinUrl automatically."
        ),
    )

class GetPeopleAttendedInput(BaseModel):
    meeting_id: Optional[str] = Field(
        default=None,
        description=(
            "The online meeting ID. If provided, attendance API is called directly "
            "without resolving from join_url/event_id."
        ),
    )
    join_url: Optional[str] = Field(
        default=None,
        description=(
            "The Teams join URL (joinUrl from event.onlineMeeting.joinUrl). "
            "Preferred over event_id because it skips one API call."
        ),
    )
    event_id: Optional[str] = Field(
        default=None,
        description=(
            "The calendar event ID. Used as fallback when join_url is unavailable. "
            "The tool fetches event details to extract joinUrl and resolve meeting_id."
        ),
    )


class GetPeopleInvitedInput(BaseModel):
    meeting_id: str = Field(description="ID of the meeting")



class CreateEventInput(BaseModel):
    """Schema for creating a calendar event"""
    subject: str = Field(description="Title/subject of the event")
    start_datetime: str = Field(description="Start datetime in ISO 8601 format (e.g. 2024-01-15T10:00:00)")
    end_datetime: str = Field(description="End datetime in ISO 8601 format (e.g. 2024-01-15T11:00:00)")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the event (e.g. 'UTC', 'America/New_York', 'India Standard Time')")
    body: Optional[str] = Field(default=None, description="Body/description of the event")
    location: Optional[str] = Field(default=None, description="Location of the event")
    attendees: Optional[List[str]] = Field(default=None, description="List of attendee email addresses")
    is_online_meeting: Optional[bool] = Field(default=False, description="Whether to create an online meeting link")
    recurrence: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional recurrence dict to make this a repeating event."
            " Must have two keys: 'pattern' (how often) and 'range' (when it ends)."
            " All keys are camelCase matching the MS Graph API."
            " PATTERN keys:"
            " type (required): 'daily' | 'weekly' | 'absoluteMonthly' | 'relativeMonthly' | 'absoluteYearly' | 'relativeYearly'."
            " interval (int, default 1): repeat every N units."
            " daysOfWeek (list[str]): required for weekly/relativeMonthly/relativeYearly."
            "   Valid: Sunday Monday Tuesday Wednesday Thursday Friday Saturday."
            " dayOfMonth (int 1-31): required for absoluteMonthly/absoluteYearly."
            " month (int 1-12): required for absoluteYearly/relativeYearly."
            " index (str): required for relativeMonthly/relativeYearly."
            "   Valid: first second third fourth last."
            " RANGE keys:"
            " type (required): 'endDate' (needs startDate+endDate) | 'noEnd' (needs startDate) | 'numbered' (needs startDate+numberOfOccurrences)."
            " startDate (YYYY-MM-DD, required): MUST match the date portion of start_datetime."
            " endDate (YYYY-MM-DD): required when type='endDate'."
            " numberOfOccurrences (int): required when type='numbered'."
            " EXAMPLES:"
            " daily 30x: {'pattern':{'type':'daily','interval':1},'range':{'type':'numbered','startDate':'2026-03-01','numberOfOccurrences':30}}."
            " weekly Mon+Wed until Dec: {'pattern':{'type':'weekly','interval':1,'daysOfWeek':['Monday','Wednesday']},'range':{'type':'endDate','startDate':'2026-03-02','endDate':'2026-12-31'}}."
            " monthly 15th forever: {'pattern':{'type':'absoluteMonthly','interval':1,'dayOfMonth':15},'range':{'type':'noEnd','startDate':'2026-03-15'}}."
            " first Monday each month: {'pattern':{'type':'relativeMonthly','interval':1,'daysOfWeek':['Monday'],'index':'first'},'range':{'type':'noEnd','startDate':'2026-03-02'}}."
            " yearly Mar 15: {'pattern':{'type':'absoluteYearly','interval':1,'dayOfMonth':15,'month':3},'range':{'type':'noEnd','startDate':'2026-03-15'}}."
            " last Friday of March each year: {'pattern':{'type':'relativeYearly','interval':1,'daysOfWeek':['Friday'],'index':'last','month':3},'range':{'type':'noEnd','startDate':'2026-03-27'}}."
        ),
    )


class CreateChannelMeetingInput(BaseModel):
    """Schema for creating a Teams channel meeting event."""
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_name: str = Field(description="Display name of the Teams channel")
    subject: str = Field(description="Meeting title/subject")
    start_datetime: str = Field(description="Start datetime in ISO 8601 format (e.g. 2024-01-15T10:00:00)")
    end_datetime: str = Field(description="End datetime in ISO 8601 format (e.g. 2024-01-15T11:00:00)")
    timezone: Optional[str] = Field(
        default="Asia/Kolkata",
        description="Timezone for the meeting (e.g. 'Asia/Kolkata', 'UTC')",
    )


class EditEventInput(BaseModel):
    event_id: str = Field(description="ID of the event to update")
    subject: Optional[str] = Field(default=None, description="Updated event title")
    start_datetime: Optional[str] = Field(default=None, description="Updated start datetime in ISO 8601 format")
    end_datetime: Optional[str] = Field(default=None, description="Updated end datetime in ISO 8601 format")
    timezone: Optional[str] = Field(default="UTC", description="Timezone label (default UTC)")
    description: Optional[str] = Field(default=None, description="Updated event description")
    is_online_meeting: Optional[bool] = Field(default=None, description="Whether event should be online")


class CreateTeamInput(BaseModel):
    display_name: str = Field(description="Display name of the team")
    description: Optional[str] = Field(default=None, description="Description of the team")


class DeleteTeamInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team to delete")


class AddMemberInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    user_id: str = Field(description="User ID or Azure AD object ID to add")
    role: Optional[str] = Field(default="member", description="Role for the new member: 'member' or 'owner'")
    channel_id: Optional[str] = Field(
        default=None,
        description="Optional channel ID. If omitted, adds user to team. For private channel, user is added directly to channel.",
    )


class GetMembersInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: Optional[str] = Field(
        default=None,
        description="Optional channel ID. If provided, returns members for that channel.",
    )
    top: Optional[int] = Field(default=100, description="Maximum number of members to return (default 100, max 500)")


class RemoveMemberInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    membership_id: str = Field(description="Membership ID of the member to remove (conversationMember ID, not user ID)")


class CreateChannelInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    display_name: str = Field(description="Display name of the channel")
    description: Optional[str] = Field(default=None, description="Description of the channel")
    channel_type: Optional[str] = Field(
        default="standard",
        description="Type of channel: 'standard' (visible to all team members) or 'private' (invite-only)",
    )


class DeleteChannelInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the channel to delete")


class UpdateChannelInput(BaseModel):
    team_id: str = Field(description="ID of the Microsoft Team")
    channel_id: str = Field(description="ID of the channel to update")
    display_name: Optional[str] = Field(default=None, description="New display name for the channel")
    description: Optional[str] = Field(default=None, description="New description for the channel")


class CreateChatInput(BaseModel):
    chat_type: str = Field(
        description="Type of chat: 'oneOnOne' for 1:1 direct message or 'group' for group chat"
    )
    member_user_ids: List[str] = Field(
        description="List of Azure AD user object IDs (or UPNs) to include in the chat (including yourself for group chats)"
    )
    topic: Optional[str] = Field(default=None, description="Topic/title for group chats (optional for 1:1 chats)")


class GetChatInput(BaseModel):
    chat_id: str = Field(description="ID of the chat to retrieve")


class GetUserInfoInput(BaseModel):
    user: str = Field(description="User ID, UPN/email, or display name")


class GetUsersListInput(BaseModel):
    limit: Optional[int] = Field(
        default=None,
        description="Maximum users to return. If omitted, fetches all users with pagination.",
    )


class TeamsAmbiguousUserError(Exception):
    """Raised when multiple Teams users match a provided identifier."""

    def __init__(self, query: str, matches: List[Dict[str, Any]]) -> None:
        self.query = query
        self.matches = matches
        super().__init__(f"Multiple users found matching '{query}'")


def _build_recurrence_body(recurrence: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a recurrence dict into the MS Graph API format.

    Accepts either:
    1) Nested Graph shape:
       {"pattern": {...}, "range": {...}}
    2) Flattened convenience shape (top-level recurrence fields), e.g.:
       {
         "type": "weekly",
         "interval": 1,
         "daysOfWeek": ["Monday"],
         "rangeType": "endDate",
         "startDate": "2026-03-02",
         "endDate": "2026-12-28"
       }
    """
    if not isinstance(recurrence, dict):
        raise ValueError("recurrence must be a dict")

    def _normalize_range_type(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace("_", "")
        mapping = {
            "enddate": "endDate",
            "noend": "noEnd",
            "numbered": "numbered",
        }
        return mapping.get(normalized)

    # Already in Graph-native nested format.
    if "pattern" in recurrence and "range" in recurrence:
        if not isinstance(recurrence["pattern"], dict) or not isinstance(recurrence["range"], dict):
            raise ValueError("recurrence.pattern and recurrence.range must be dicts")
        pattern = dict(recurrence["pattern"])
        range_obj = dict(recurrence["range"])
        # Allow rangeType as alias in nested payloads.
        if "type" not in range_obj and "rangeType" in range_obj:
            normalized_type = _normalize_range_type(range_obj.get("rangeType"))
            if normalized_type:
                range_obj["type"] = normalized_type
        elif "type" in range_obj:
            normalized_type = _normalize_range_type(range_obj.get("type"))
            if normalized_type:
                range_obj["type"] = normalized_type

        # Derive a sensible default if omitted.
        if "type" not in range_obj:
            if "numberOfOccurrences" in range_obj:
                range_obj["type"] = "numbered"
            elif "endDate" in range_obj:
                range_obj["type"] = "endDate"
            else:
                range_obj["type"] = "noEnd"

        return {
            "pattern": pattern,
            "range": range_obj,
        }

    # Build Graph-native structure from flattened keys.
    pattern_keys = {
        "type",
        "interval",
        "daysOfWeek",
        "firstDayOfWeek",
        "index",
        "dayOfMonth",
        "month",
    }
    range_keys = {
        "rangeType",
        "startDate",
        "endDate",
        "numberOfOccurrences",
        "recurrenceTimeZone",
    }

    pattern: Dict[str, Any] = {}
    for key in pattern_keys:
        if key in recurrence:
            pattern[key] = recurrence[key]

    range_obj: Dict[str, Any] = {}
    if "rangeType" in recurrence:
        normalized_type = _normalize_range_type(recurrence["rangeType"])
        range_obj["type"] = normalized_type or recurrence["rangeType"]
    elif "range_type" in recurrence:
        normalized_type = _normalize_range_type(recurrence["range_type"])
        range_obj["type"] = normalized_type or recurrence["range_type"]
    for key in ("startDate", "endDate", "numberOfOccurrences", "recurrenceTimeZone"):
        if key in recurrence:
            range_obj[key] = recurrence[key]

    # If user passed one nested key, reuse it and fill the missing one from flat keys.
    if "pattern" in recurrence and isinstance(recurrence["pattern"], dict):
        pattern = recurrence["pattern"]
    if "range" in recurrence and isinstance(recurrence["range"], dict):
        range_obj = recurrence["range"]

    if "type" in range_obj:
        normalized_type = _normalize_range_type(range_obj.get("type"))
        if normalized_type:
            range_obj["type"] = normalized_type
    elif "rangeType" in range_obj:
        normalized_type = _normalize_range_type(range_obj.get("rangeType"))
        if normalized_type:
            range_obj["type"] = normalized_type

    # Auto-derive range type when missing.
    if "type" not in range_obj:
        if "numberOfOccurrences" in range_obj:
            range_obj["type"] = "numbered"
        elif "endDate" in range_obj:
            range_obj["type"] = "endDate"
        else:
            range_obj["type"] = "noEnd"

    if not pattern:
        raise ValueError(
            "recurrence is missing pattern data. Provide recurrence.pattern or flat keys like type/interval/daysOfWeek."
        )
    if not range_obj:
        raise ValueError(
            "recurrence is missing range data. Provide recurrence.range or flat keys like startDate/endDate/numberOfOccurrences."
        )
    if "startDate" not in range_obj:
        raise ValueError("recurrence range is missing startDate.")

    return {
        "pattern": pattern,
        "range": range_obj,
    }
# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("Teams")\
    .in_group("Microsoft 365")\
    .with_description("Microsoft Teams integration for messaging and collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Teams",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="toolsets/oauth/callback/teams",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "Team.ReadBasic.All",
                    "Channel.ReadBasic.All",
                    "Channel.Create",
                    "Channel.Delete.All",
                    "ChannelMessage.Read.All",
                    "ChannelMessage.Send",
                    "TeamMember.ReadWrite.All",
                    "Group.ReadWrite.All",
                    "Chat.ReadWrite",
                    "Chat.Create",
                    "offline_access",
                    "User.Read",
                    "User.ReadBasic.All",
                    "Calendars.Read",
                    "Calendars.Read.Shared",
                    "Calendars.ReadBasic",
                    "Calendars.ReadWrite",
                    "OnlineMeetings.Read",
                    "OnlineMeetingTranscript.Read.All",
                    "OnlineMeetingArtifact.Read.All",
                    "ChannelMember.ReadWrite.All",
                ],
            ),
            additional_params={
                "prompt": "consent",
                "response_mode": "query",
            },
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration"),
                AuthField(
                    name="tenantId",
                    display_name="Tenant ID",
                    field_type="TEXT",
                    placeholder="common  (or your Azure AD tenant ID / domain)",
                    description=(
                        "Your Azure Active Directory tenant ID (for example "
                        "'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx') or domain "
                        "(for example 'contoso.onmicrosoft.com'). "
                        "Leave blank or enter 'common' to allow both personal Microsoft "
                        "accounts and any Azure AD tenant."
                    ),
                    required=False,
                    default_value="common",
                    min_length=0,
                    max_length=500,
                    is_secret=False,
                ),
            ],
            icon_path="/assets/icons/connectors/teams.svg",
            app_group="Microsoft 365",
            app_description="Microsoft Teams OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/teams.svg")
        .add_documentation_link(DocumentationLink(
                    title="Create an Azure App Registration",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
                    doc_type="setup",
        ))
        .add_documentation_link(DocumentationLink(
                    title="Microsoft Graph Teams permissions reference",
                    url="https://learn.microsoft.com/en-us/graph/permissions-reference",
                    doc_type="reference",
        ))
        .add_documentation_link(DocumentationLink(
                    title="Configure OAuth 2.0 redirect URIs",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/reply-url",
                    doc_type="setup",
        ))
        .add_documentation_link(DocumentationLink(
            title="Pipeshub Documentation",
            url="https://docs.pipeshub.com/toolsets/overview",
            doc_type="pipeshub",
    )))\
    .build_decorator()
class Teams:
    """Microsoft Teams toolset for messaging and team collaboration operations."""

    def __init__(
        self,
        client: Optional[MSGraphClient] = None,
        state: Optional[dict] = None,
    ) -> None:
        self.client: Optional[TeamsDataSource] = None
        self.state = state
        logger.debug(client)
        logger.debug(
            "Teams.__init__ called with client_type=%s has_get_client=%s",
            type(client).__name__ if client is not None else "None",
            hasattr(client, "get_client") if client is not None else False,
        )
        # Some wrapper fallback paths pass state positionally as dict.
        if isinstance(client, dict) and state is None:
            self.state = client
            client = None
        if client is None:
            logger.warning("Teams.__init__ received client=None; datasource initialization skipped")
            return
        if isinstance(client, dict):
            logger.error(
                "Teams.__init__ received dict instead of MSGraphClient; keys=%s",
                list(client.keys())[:20],
            )
        try:
            logger.debug("Attempting TeamsDataSource initialization")
            self.client = TeamsDataSource(client)
            logger.debug("TeamsDataSource initialization successful")
        except Exception as e:
            logger.exception(
                "Teams client initialization failed; client_type=%s has_get_client=%s error=%s",
                type(client).__name__,
                hasattr(client, "get_client"),
                str(e),
            )
            self.client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_error(self, error: Exception, operation: str = "operation") -> tuple[bool, str]:
        error_msg = str(error).lower()

        if isinstance(error, AttributeError):
            if (
                "client" in error_msg
                or "me" in error_msg
                or "nonetype" in error_msg
                or "has no attribute" in error_msg
            ):
                logger.error(
                    f"Teams client not properly initialised - authentication may be required: {error}"
                )
                return False, json.dumps({
                    "error": (
                        "Teams toolset is not authenticated. "
                        "Please complete the OAuth flow first. "
                        "Go to Settings > Toolsets to authenticate your Teams account."
                    )
                })

        if (
            isinstance(error, ValueError)
            or "not authenticated" in error_msg
            or "oauth" in error_msg
            or "authentication" in error_msg
            or "unauthorized" in error_msg
            or "get_client" in error_msg
        ):
            logger.error(f"Teams authentication error during {operation}: {error}")
            return False, json.dumps({
                "error": (
                    "Teams toolset is not authenticated. "
                    "Please complete the OAuth flow first. "
                    "Go to Settings > Toolsets to authenticate your Teams account."
                )
            })

        logger.error(f"Failed to {operation}: {error}")
        return False, json.dumps({"error": str(error)})

    @staticmethod
    def _serialize_response(response_obj: Any) -> Any:
        if response_obj is None:
            return None
        if isinstance(response_obj, (str, int, float, bool)):
            return response_obj
        if isinstance(response_obj, list):
            return [Teams._serialize_response(item) for item in response_obj]
        if isinstance(response_obj, dict):
            return {k: Teams._serialize_response(v) for k, v in response_obj.items()}

        if hasattr(response_obj, "get_field_deserializers"):
            try:
                from kiota_serialization_json.json_serialization_writer import (  # type: ignore
                    JsonSerializationWriter,
                )
                import json as _json

                writer = JsonSerializationWriter()
                writer.write_object_value(None, response_obj)
                content = writer.get_serialized_content()
                if content:
                    raw = content.decode("utf-8") if isinstance(content, bytes) else content
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
            except Exception:
                pass

        try:
            obj_dict = vars(response_obj)
        except TypeError:
            obj_dict = {}

        result: Dict[str, Any] = {}
        for k, v in obj_dict.items():
            if k.startswith("_"):
                continue
            try:
                result[k] = Teams._serialize_response(v)
            except Exception:
                result[k] = str(v)

        additional = getattr(response_obj, "additional_data", None)
        if isinstance(additional, dict):
            for k, v in additional.items():
                if k not in result:
                    try:
                        result[k] = Teams._serialize_response(v)
                    except Exception:
                        result[k] = str(v)

        return result if result else str(response_obj)

    @staticmethod
    def _extract_collection_items(serialized: Any) -> List[Any]:
        if isinstance(serialized, dict):
            value = serialized.get("value")
            if isinstance(value, list):
                return value
        if isinstance(serialized, list):
            return serialized
        return []

    @staticmethod
    def _extract_team_id(serialized: Any) -> Optional[str]:
        if isinstance(serialized, dict):
            team_id = serialized.get("id")
            if isinstance(team_id, str) and team_id:
                return team_id
            value = serialized.get("value")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item_id = item.get("id")
                        if isinstance(item_id, str) and item_id:
                            return item_id
        return None

    @staticmethod
    def _find_team_by_display_name(items: List[Any], display_name: str) -> Optional[Dict[str, Any]]:
        target_name = display_name.strip().lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            current_name = str(item.get("displayName") or "").strip().lower()
            if current_name == target_name:
                return item
        return None

    async def _wait_for_created_team(
        self,
        display_name: str,
        attempts: int = 5,
        delay_seconds: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        for attempt in range(attempts):
            try:
                response = await self.client.me_list_joined_teams()
                if response.success:
                    serialized = self._serialize_response(response.data)
                    teams = self._extract_collection_items(serialized)
                    matched_team = self._find_team_by_display_name(teams, display_name)
                    if matched_team:
                        return matched_team
            except Exception as e:
                logger.debug("create_team polling attempt %s failed: %s", attempt + 1, e)

            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
        return None


    @staticmethod
    def _extract_next_link(serialized: Any) -> Optional[str]:
        if not isinstance(serialized, dict):
            return None
        for key in ("@odata.nextLink", "odata_next_link", "next_link", "nextLink"):
            value = serialized.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def _resolve_user_identifier(
        self,
        user_identifier: str,
        allow_ambiguous: bool = False,
    ) -> Optional[str]:
        """Resolve user identifier (ID, UPN/email, or display name) to user ID."""
        try:
            if not user_identifier or not isinstance(user_identifier, str):
                return None

            target_identifier = user_identifier.lstrip("@").strip().casefold()
            if not target_identifier:
                return None

            # Fast-path: ID/UPN/email can often be resolved directly via /users/{id-or-upn}
            direct_response = await self.client.teams_get_user(user_identifier.strip())
            if direct_response.success and direct_response.data:
                direct_user = self._serialize_response(direct_response.data)
                if isinstance(direct_user, dict):
                    direct_id = direct_user.get("id")
                    if isinstance(direct_id, str) and direct_id:
                        return direct_id

            exact_matches: List[Dict[str, Any]] = []
            partial_matches: List[Dict[str, Any]] = []
            next_link: Optional[str] = None
            seen_links = set()

            for _ in range(50):
                users_response = await self.client.teams_list_users(cursor_url=next_link)
                if not users_response.success or not users_response.data:
                    break

                users_payload = self._serialize_response(users_response.data)
                users = self._extract_collection_items(users_payload)
                if not users:
                    break

                for user in users:
                    if not isinstance(user, dict):
                        continue
                    user_id = user.get("id")
                    if not isinstance(user_id, str) or not user_id:
                        continue

                    user_info = {
                        "id": user_id,
                        "display_name": user.get("displayName") or user.get("display_name"),
                        "mail": user.get("mail"),
                        "user_principal_name": user.get("userPrincipalName") or user.get("user_principal_name"),
                    }

                    names_to_match = [
                        user.get("displayName"),
                        user.get("display_name"),
                        user.get("mail"),
                        user.get("userPrincipalName"),
                        user.get("user_principal_name"),
                        user_id,
                    ]

                    found_exact = False
                    for name in names_to_match:
                        if not isinstance(name, str):
                            continue
                        name_normalized = name.casefold()
                        if name_normalized == target_identifier:
                            if not any(m.get("id") == user_id for m in exact_matches):
                                exact_matches.append(user_info)
                            found_exact = True
                            break

                    if found_exact:
                        continue

                    for name in names_to_match:
                        if not isinstance(name, str):
                            continue
                        name_normalized = name.casefold()
                        if len(target_identifier) >= 3 and (
                            target_identifier in name_normalized or name_normalized in target_identifier
                        ):
                            if not any(m.get("id") == user_id for m in partial_matches):
                                partial_matches.append(user_info)
                            break

                next_link_candidate = self._extract_next_link(users_payload)
                if not next_link_candidate or next_link_candidate in seen_links:
                    break
                seen_links.add(next_link_candidate)
                next_link = next_link_candidate

            if exact_matches:
                if len(exact_matches) > 1 and not allow_ambiguous:
                    raise TeamsAmbiguousUserError(user_identifier, exact_matches)
                return exact_matches[0]["id"]

            if partial_matches:
                if len(partial_matches) > 1 and not allow_ambiguous:
                    raise TeamsAmbiguousUserError(user_identifier, partial_matches)
                return partial_matches[0]["id"]

            logger.debug(f"Could not resolve Teams user identifier '{user_identifier}'")
            return None
        except TeamsAmbiguousUserError:
            raise
        except Exception as e:
            logger.error(f"Error resolving Teams user identifier '{user_identifier}': {e}")
            return None

    # ------------------------------------------------------------------
    # User tools
    # ------------------------------------------------------------------

    @tool(
        app_name="teams",
        tool_name="get_user_info",
        description="Get information about a Teams/Entra user",
        args_schema=GetUserInfoInput,
        when_to_use=[
            "User wants Teams user information",
            "User asks about a Teams user",
            "User provides a user email/name and needs details",
        ],
        when_not_to_use=[
            "User wants list of all users (use get_users_list)",
            "User wants Teams/channel message operations",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get info about user@company.com in Teams",
            "Who is Alex in Teams?",
            "Show Teams user details",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_user_info(self, user: str) -> tuple[bool, str]:
        """Get Teams user details with transformed response for easy extraction."""
        try:
            try:
                user_id = await self._resolve_user_identifier(user, allow_ambiguous=False)
            except TeamsAmbiguousUserError as e:
                matches_list = []
                for match in e.matches[:20]:
                    label = match.get("display_name") or match.get("user_principal_name") or "Unknown"
                    if match.get("mail"):
                        label += f" ({match.get('mail')})"
                    label += f" [ID: {match.get('id', 'Unknown')}]"
                    matches_list.append(f"  - {label}")

                error_msg = (
                    f"Multiple users found matching '{user}'. Please use email/UPN or user ID for disambiguation.\n\n"
                    f"Matching users:\n" + "\n".join(matches_list)
                )
                return False, json.dumps({"error": error_msg})

            if not user_id:
                user_id = user
                logger.debug(f"Could not resolve Teams user '{user}', trying as-is: {user_id}")

            response = await self.client.teams_get_user(user_id=user_id)
            if response.success and response.data:
                user_obj = self._serialize_response(response.data)
                if isinstance(user_obj, dict):
                    transformed = {
                        "id": user_obj.get("id") or user_id,
                        "display_name": user_obj.get("displayName") or user_obj.get("display_name"),
                        "mail": user_obj.get("mail"),
                        "user_principal_name": user_obj.get("userPrincipalName") or user_obj.get("user_principal_name"),
                        "given_name": user_obj.get("givenName") or user_obj.get("given_name"),
                        "surname": user_obj.get("surname"),
                        "job_title": user_obj.get("jobTitle") or user_obj.get("job_title"),
                        "department": user_obj.get("department"),
                        "office_location": user_obj.get("officeLocation") or user_obj.get("office_location"),
                        "mobile_phone": user_obj.get("mobilePhone") or user_obj.get("mobile_phone"),
                        "business_phones": user_obj.get("businessPhones") or user_obj.get("business_phones"),
                        "raw_user": user_obj,
                    }
                    return True, json.dumps({
                        **transformed,
                        "data": {
                            # Keep both shapes for placeholder compatibility:
                            # - teams.get_user_info.data.id
                            # - teams.get_user_info.data.results[0].id
                            "id": transformed["id"],
                            "results": [transformed],
                        },
                    })
            return False, json.dumps({"error": response.error or "Failed to get user info"})
        except TeamsAmbiguousUserError:
            raise
        except Exception as e:
            return self._handle_error(e, "get user info")

    @tool(
        app_name="teams",
        tool_name="get_users_list",
        description="Get list of users available in Microsoft Entra/Teams tenant",
        args_schema=GetUsersListInput,
        when_to_use=[
            "User wants to list all Teams users",
            "User asks for all users in tenant",
            "User needs available users directory",
        ],
        when_not_to_use=[
            "User wants one specific user info (use get_user_info)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all Teams users",
            "Show available users in my tenant",
            "Get all Microsoft users",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_users_list(self, limit: Optional[int] = None) -> tuple[bool, str]:
        """Get users list with pagination support."""
        try:
            # If limit is specified, single page is enough then slice.
            if limit:
                response = await self.client.teams_list_users()
                if not response.success:
                    return False, json.dumps({"error": response.error or "Failed to get users list"})
                payload = self._serialize_response(response.data)
                users = self._extract_collection_items(payload)
                users = users[: max(limit, 0)]
                return True, json.dumps({
                    "members": users,
                    "count": len(users),
                    "data": {"results": users},
                })

            all_users: List[Any] = []
            next_link: Optional[str] = None
            seen_links = set()

            for _ in range(50):
                response = await self.client.teams_list_users(cursor_url=next_link)
                if not response.success or not response.data:
                    if not all_users:
                        return False, json.dumps({"error": response.error or "Failed to get users list"})
                    break

                payload = self._serialize_response(response.data)
                users = self._extract_collection_items(payload)
                all_users.extend(users)

                next_link_candidate = self._extract_next_link(payload)
                if not next_link_candidate or next_link_candidate in seen_links:
                    break
                seen_links.add(next_link_candidate)
                next_link = next_link_candidate

            return True, json.dumps({
                "members": all_users,
                "count": len(all_users),
                "data": {"results": all_users},
            })
        except Exception as e:
            return self._handle_error(e, "get users list")

    # @tool(
    #     app_name="teams",
    #     tool_name="get_user_conversations",
    #     description="Get conversations/chats for the authenticated Teams user",
    #     args_schema=GetUserConversationsInput,
    #     when_to_use=[
    #         "User wants to list their Teams conversations",
    #         "User asks for chats they are part of",
    #     ],
    #     when_not_to_use=[
    #         "User wants team channels only (use get_user_channels)",
    #         "No Teams mention",
    #     ],
    #     primary_intent=ToolIntent.SEARCH,
    #     typical_queries=[
    #         "Show my Teams conversations",
    #         "List my chats in Teams",
    #     ],
    #     category=ToolCategory.SEARCH,
    # )
    # async def get_user_conversations(self, top: Optional[int] = 100) -> tuple[bool, str]:
    #     try:
    #         response = await self.client.teams_get_user_conversations()
    #         if response.success:
    #             serialized = self._serialize_response(response.data)
    #             conversations = self._extract_collection_items(serialized)
    #             limit = min(top or 100, 500)
    #             conversations = conversations[:limit]
    #             return True, json.dumps({
    #                 "data": {"results": conversations},
    #                 "conversations": conversations,
    #                 "count": len(conversations),
    #             })
    #         return False, json.dumps({"error": response.error or "Failed to get user conversations"})
    #     except Exception as e:
    #         return self._handle_error(e, "get user conversations")

    @tool(
        app_name="teams",
        tool_name="get_user_conversations",
        description="Get conversation messages with a Teams user",
        args_schema=GetUserConversationsInput,
        when_to_use=[
            "User wants to see conversation with someone in Teams",
            "User asks for chat history with a person",
            "User asks messages with someone within a time range",
        ],
        when_not_to_use=[
            "User wants team channels only",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my conversation with Vansh",
            "Show my conversation with Vansh in last 7 days",
            "Show my messages with john@company.com",
            "Get chat history with Alex in last 1 hour",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_user_conversations(
        self,
        user_identifier: Optional[str] = None,
        minutes: Optional[int] = None,
        hours: Optional[int] = None,
        days: Optional[int] = None,
        top: Optional[int] = 50,
        ) -> tuple[bool, str]:

        try:

            response = await self.client.teams_get_conversation_with_user(
                user_identifier=user_identifier,
                minutes=minutes,
                hours=hours,
                days=days,
                top=top,
            )
          

            if response.success:

                serialized = self._serialize_response(response.data)
                messages = self._extract_collection_items(
                    serialized.get("results") if isinstance(serialized, dict) else serialized
                )
                if not messages and isinstance(serialized, dict):
                    raw_messages = serialized.get("results")
                    if isinstance(raw_messages, list):
                        messages = raw_messages
                    else:
                        messages = self._extract_collection_items(serialized)

                return True, json.dumps(
                    {
                        "data": {"results": messages},
                        "messages": messages,
                        "count": len(messages),
                    }
                )

            return False, json.dumps(
                {"error": response.error or "Failed to get conversation"}
            )

        except Exception as e:
            return self._handle_error(e, "get user conversations")
            
    @tool(
        app_name="teams",
        tool_name="get_user_channels",
        description="Get channels accessible to the authenticated Teams user",
        args_schema=GetUserChannelsInput,
        when_to_use=[
            "User wants channels they can access in Teams",
            "User asks for their Teams channels",
        ],
        when_not_to_use=[
            "User wants messages from a specific channel (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my Teams channels",
            "List user channels in Teams",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_user_channels(self, team_id: Optional[str] = None, top: Optional[int] = 200) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_user_channels(team_id=team_id)
            if response.success:
                serialized = self._serialize_response(response.data)
                channels = self._extract_collection_items(serialized.get("channels") if isinstance(serialized, dict) else serialized)
                if not channels and isinstance(serialized, dict):
                    raw_channels = serialized.get("channels")
                    if isinstance(raw_channels, list):
                        channels = raw_channels
                limit = min(top or 200, 1000)
                channels = channels[:limit]
                return True, json.dumps({
                    "data": {"results": channels},
                    "channels": channels,
                    "count": len(channels),
                    "team_id": team_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get user channels"})
        except Exception as e:
            return self._handle_error(e, "get user channels")

    @tool(
        app_name="teams",
        tool_name="get_meetings",
        description=(
            "Get meetings for the authenticated Teams user with optional strict filters "
            "(date range, deleted, cancelled, recurring/one_time)."
        ),
        args_schema=GetMeetingsInput,
        when_to_use=[
            "User wants meetings with strict filtering",
            "User asks for meetings in a period with status/type filters",
            "User wants one tool for all meetings and filtered meetings",
        ],
        when_not_to_use=[
            "User wants meeting transcripts (use get_meeting_transcripts)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get my meetings from start to end with only recurring ones",
            "List non-cancelled meetings",
            "Show deleted meetings for this month",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_meetings(
        self,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        is_deleted: Optional[bool] = None,
        is_cancelled: Optional[bool] = None,
        meeting_type: Optional[str] = None,
        top: Optional[int] = 100,
    ) -> tuple[bool, str]:
        try:

            logger.info("========== GET_MEETINGS TOOL CALLED ==========")

            logger.info(f"start_datetime: {start_datetime} | type: {type(start_datetime)}")
            logger.info(f"end_datetime: {end_datetime} | type: {type(end_datetime)}")
            logger.info(f"is_deleted: {is_deleted} | type: {type(is_deleted)}")
            logger.info(f"is_cancelled: {is_cancelled} | type: {type(is_cancelled)}")
            logger.info(f"meeting_type: {meeting_type} | type: {type(meeting_type)}")
            logger.info(f"top: {top} | type: {type(top)}")

            logger.info("===============================================")
            response = await self.client.teams_get_meetings(
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                is_deleted=is_deleted,
                is_cancelled=is_cancelled,
                meeting_type=meeting_type,
                top=top or 100,
            )
            if response.success:
                serialized = self._serialize_response(response.data)
                
                meetings = []
                if isinstance(serialized, dict):
                    raw_meetings = serialized.get("results")
                    if isinstance(raw_meetings, list):
                        meetings = raw_meetings

                normalized_meetings = []
                for meeting in meetings:
                    if not isinstance(meeting, dict):
                        continue
                    meeting_id = meeting.get("meeting_id") or meeting.get("online_meeting_id") 
                    meeting["meeting_id"] = meeting_id
                    normalized_meetings.append(meeting)

                return True, json.dumps({
                    "data": {"results": normalized_meetings},
                    "meetings": normalized_meetings,
                    "count": len(normalized_meetings),
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                    "is_deleted": is_deleted,
                    "is_cancelled": is_cancelled,
                    "meeting_type": meeting_type,
                })
            return False, json.dumps({"error": response.error or "Failed to get meetings"})
        except Exception as e:
            return self._handle_error(e, "get meetings")

    # @tool(
    #     app_name="teams",
    #     tool_name="get_my_meetings",
    #     description="Get meetings for the authenticated Teams user",
    #     args_schema=GetMyMeetingsInput,
    #     when_to_use=[
    #         "User wants to list their upcoming or recent meetings",
    #         "User asks for my Teams meetings",
    #     ],
    #     when_not_to_use=[
    #         "User wants chat conversations (use get_user_conversations)",
    #         "No Teams mention",
    #     ],
    #     primary_intent=ToolIntent.SEARCH,
    #     typical_queries=[
    #         "Show my meetings",
    #         "List my Teams meetings",
    #     ],
    #     category=ToolCategory.SEARCH,
    # )
    # async def get_my_meetings(self, top: Optional[int] = 50) -> tuple[bool, str]:
    #     try:
    #         response = await self.client.teams_get_my_meetings(top=top or 50)
    #         if response.success:
    #             serialized = self._serialize_response(response.data)
    #             meetings = []
    #             if isinstance(serialized, dict):
    #                 raw_meetings = serialized.get("results")
    #                 if isinstance(raw_meetings, list):
    #                     meetings = raw_meetings
    #             return True, json.dumps({
    #                 "data": {"results": meetings},
    #                 "meetings": meetings,
    #                 "count": len(meetings),
    #             })
    #         return False, json.dumps({"error": response.error or "Failed to get meetings"})
    #     except Exception as e:
    #         return self._handle_error(e, "get my meetings")

    # @tool(
    #     app_name="teams",
    #     tool_name="get_my_recurring_meetings",
    #     description="Get recurring meetings for the authenticated Teams user",
    #     args_schema=GetMyRecurringMeetingsInput,
    #     when_to_use=[
    #         "User wants recurring meetings only",
    #         "User asks for repeated/series meetings",
    #     ],
    #     when_not_to_use=[
    #         "User wants all meetings (use get_my_meetings)",
    #         "No Teams mention",
    #     ],
    #     primary_intent=ToolIntent.SEARCH,
    #     typical_queries=[
    #         "Show my recurring meetings",
    #         "List repeated Teams meetings",
    #     ],
    #     category=ToolCategory.SEARCH,
    # )
    # async def get_my_recurring_meetings(self, top: Optional[int] = 50) -> tuple[bool, str]:
    #     try:
    #         response = await self.client.teams_get_my_recurring_meetings(top=top or 50)
    #         if response.success:
    #             serialized = self._serialize_response(response.data)
    #             meetings = []
    #             if isinstance(serialized, dict):
    #                 raw_meetings = serialized.get("results")
    #                 if isinstance(raw_meetings, list):
    #                     meetings = raw_meetings
    #             return True, json.dumps({
    #                 "data": {"results": meetings},
    #                 "meetings": meetings,
    #                 "count": len(meetings),
    #             })
    #         return False, json.dumps({"error": response.error or "Failed to get recurring meetings"})
    #     except Exception as e:
    #         return self._handle_error(e, "get my recurring meetings")

    # @tool(
    #     app_name="teams",
    #     tool_name="get_my_meetings_for_given_period",
    #     description="Get meetings for the authenticated Teams user within a given datetime period",
    #     args_schema=GetMyMeetingsForGivenPeriodInput,
    #     when_to_use=[
    #         "User wants meetings between start and end datetime",
    #         "User asks meetings for a specific period",
    #     ],
    #     when_not_to_use=[
    #         "User wants all meetings without date filter (use get_my_meetings)",
    #         "No Teams mention",
    #     ],
    #     primary_intent=ToolIntent.SEARCH,
    #     typical_queries=[
    #         "Show my meetings between two dates",
    #         "Find meetings for this week",
    #     ],
    #     category=ToolCategory.SEARCH,
    # )
    # async def get_my_meetings_for_given_period(
    #     self,
    #     start_datetime: str,
    #     end_datetime: str,
    #     top: Optional[int] = 100,
    # ) -> tuple[bool, str]:
    #     try:  
    #         response = await self.client.teams_get_my_meetings_for_given_period(
    #             start_datetime=start_datetime,
    #             end_datetime=end_datetime,
    #             top=top or 100,
    #         )
    #         if response.success:
    #             serialized = self._serialize_response(response.data)
    #             logger.info(
    #                 "teams_get_my_meetings_for_given_period response=%s",
    #                 json.dumps(serialized, default=str)[:4000],
    #             )
    #             meetings = []
    #             if isinstance(serialized, dict):
    #                 raw_meetings = serialized.get("results")
    #                 if isinstance(raw_meetings, list):
    #                     meetings = raw_meetings
    #             return True, json.dumps({
    #                 "data": {"results": meetings},
    #                 "meetings": meetings,
    #                 "count": len(meetings),
    #                 "start_datetime": start_datetime,
    #                 "end_datetime": end_datetime,
    #             })
    #         return False, json.dumps({"error": response.error or "Failed to get meetings for period"})
    #     except Exception as e:
    #         return self._handle_error(e, "get my meetings for given period")

    @tool(
        app_name="teams",
        tool_name="search_calendar_events_in_range",
        description=(
            "Search calendar events by partial name (subject) within a time frame. "
            "Returns only events matching the keyword that fall within the given date range."
        ),
        args_schema=SearchCalendarEventsInRangeInput,
        when_to_use=[
            "User wants to find a specific event by name within a time period",
            "User asks for a named meeting in a date range",
            "User wants to check if a specific recurring event occurs in a period",
        ],
        when_not_to_use=[
            "User wants all events in a range (use get_calendar_events)",
            "User wants semantic/conceptual search (use semantic_search_calendar_events)",
            "User wants to create or update an event",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find standup meetings this week",
            "Search for catchup events in March",
            "Is there a sprint review next week?",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def search_calendar_events_in_range(
        self,
        keyword: str,
        start_datetime: str,
        end_datetime: str,
        timezone: str = "UTC",
        top: int = 10,
    ) -> tuple[bool, str]:
        """Search calendar events by partial subject match within a time range.

        Uses Graph API $filter with:
        - contains(subject, '{keyword}')        — partial name match
        - start/dateTime ge '{start_datetime}'  — time range start
        - end/dateTime   le '{end_datetime}'    — time range end
        """
        try:
            keyword = keyword.strip().replace("'", "''")

            if not keyword:
                return False, json.dumps({"error": "keyword cannot be empty."})


            resp = await self.client.me_search_events_in_range(
                keyword=keyword,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                timezone=timezone,
                top=top,
            )

            if not resp.success:
                return False, json.dumps({"error": resp.error or "Failed to search calendar events"})
            
            data = self._serialize_response(resp.data)
            
            events = (
                data.get("value", []) if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )

            

            return True, json.dumps({
                "results": events,
                "count": len(events),
                "keyword": keyword,
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
            })

        except Exception as e:
            print(f"[search_calendar_events_in_range] exception: {e!r}")
            return self._handle_error(e, "search calendar events in range")




    @tool(
        app_name="teams",
        tool_name="get_my_meeting_transcripts",
        description="Get transcript records, content, and metadata for one of the authenticated user's online meetings",
        args_schema=GetMeetingTranscriptsInput,
        when_to_use=[
            "User wants transcript data for a meeting",
            "User asks for meeting transcript records",
        ],
        when_not_to_use=[
            "User wants meeting list first (use get_my_meetings)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get transcript for meeting X",
            "Show transcript records for this meeting",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_my_meeting_transcripts(
        self,
        meeting_id: Optional[str] = None,
        event_id: Optional[str] = None,
        join_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Fetch all transcripts for an online meeting.

        Preferred: meeting_id (direct call, no resolution).
        Fallback:  join_url (skips one API call) or event_id (fetches event first).
        """
        
        try:
            # Step 1: Resolve to online meeting ID
            resolved_meeting_id = meeting_id
            if not resolved_meeting_id:
                resolved_meeting_id = await self._resolve_to_online_meeting_id(
                    event_id=event_id,
                    join_url=join_url,
                )

            if not resolved_meeting_id:
                return False, json.dumps({
                    "error": (
                        "Could not resolve to a Teams online meeting ID. "
                        "Provide meeting_id directly, or pass a valid join_url/event_id. "
                        "The event may not be a Teams meeting, or you may lack "
                        "OnlineMeetings.Read permission."
                    )
                })

            

            # Step 2: List transcripts
            list_resp = await self.client.me_list_online_meeting_transcripts(
                onlineMeeting_id=resolved_meeting_id,
            )
            if not list_resp.success:
                return False, json.dumps({"error": list_resp.error or "Failed to list transcripts"})
            
            data = self._serialize_response(list_resp.data) if list_resp.data else {}
            transcript_items = (
                data.get("value", []) if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )

            if not transcript_items:
                return True, json.dumps({
                    "message": "No transcripts available for this meeting",
                    "transcripts": [],
                })

            

            # Step 3: Fetch metadataContent for each transcript
            all_transcripts = []
            for t_obj in transcript_items:
                t_id = (
                    t_obj.id if hasattr(t_obj, "id")
                    else (t_obj.get("id") if isinstance(t_obj, dict) else None)
                )
                if not t_id:
                    continue

                created = (
                    str(t_obj.created_date_time) if hasattr(t_obj, "created_date_time")
                    else (t_obj.get("createdDateTime") if isinstance(t_obj, dict) else None)
                )

                parsed_entries = []
                meta_resp = await self.client.me_get_online_meeting_transcript_metadata(
                    onlineMeeting_id=resolved_meeting_id,
                    callTranscript_id=t_id,
                )
                if meta_resp.success:
                    meta_data = self._serialize_response(meta_resp.data) if meta_resp.data else {}
                    meta_text = meta_data.get("content", "") if isinstance(meta_data, dict) else ""
                    if meta_text:
                        parsed_entries = self._parse_metadata_json(meta_text)

                all_transcripts.append({
                    "transcript_id": t_id,
                    "created": created,
                    "entries": parsed_entries,
                    "entry_count": len(parsed_entries),
                })
            
            
            return True, json.dumps({
                "meeting_id": resolved_meeting_id,
                "transcripts": all_transcripts,
                "transcript_count": len(all_transcripts),
            })

        except Exception as e:
            return self._handle_error(e, "get meeting transcripts")


    async def _resolve_to_online_meeting_id(
        self,
        event_id: Optional[str] = None,
        join_url: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve to an online meeting ID.

        Path A (join_url provided) — 1 API call:
            GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'

        Path B (event_id provided) — 2 API calls:
            GET /me/events/{event_id}  →  extract joinUrl
            GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
        """
        try:
            # Path A: join_url provided directly — skip event fetch
            if join_url:
                result = await self._online_meeting_id_from_join_url(join_url)
                return result

            # Path B: event_id provided — fetch event to extract joinUrl
            if event_id:
                ev_resp = await self.client.me_calendar_get_events(event_id=event_id)
                if not ev_resp.success:
                    return None

                ev = self._serialize_response(ev_resp.data) if ev_resp.data else {}
                if not isinstance(ev, dict):
                    return None

                om = ev.get("onlineMeeting") or ev.get("online_meeting")
                if not isinstance(om, dict):
                    return None

                extracted_join_url = (
                    om.get("joinWebUrl")
                    or om.get("joinUrl")
                    or om.get("join_web_url")
                    or om.get("join_url")
                )
                if not extracted_join_url or not isinstance(extracted_join_url, str):
                    return None

                result = await self._online_meeting_id_from_join_url(extracted_join_url)
                return result

            return None

        except Exception as e:
            print(f"[_resolve_to_online_meeting_id] exception: {e!r}")
            return None


    async def _online_meeting_id_from_join_url(self, join_url: str) -> Optional[str]:
        """Resolve a Teams joinWebUrl to an online meeting ID.

        GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
        NOTE: join_url must be URL-decoded before filtering — Graph API 
        returns 400 if the URL contains percent-encoded characters.
        """
        try:
            from urllib.parse import unquote
            
            # Decode percent-encoded characters before passing to Graph filter
            decoded_url = unquote(join_url)

            safe_url = decoded_url.replace("'", "''")
            resp = await self.client.me_list_online_meetings(
                filter=f"joinWebUrl eq '{safe_url}'",
            )
            if not resp.success:
                return None

            data = self._serialize_response(resp.data) if resp.data else {}
            
            items = (
                data.get("value") or data.get("results", [])
                if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )
            if not items:
                return None

            first = items[0]
            return (
                first.get("id") if isinstance(first, dict)
                else getattr(first, "id", None)
            )

        except Exception:
            return None

    @staticmethod
    def _parse_metadata_json(meta_text: str) -> List[Dict[str, str]]:
        """Parse metadataContent (speaker diarization JSON lines) into entries."""
        entries: List[Dict[str, str]] = []
        for line in meta_text.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    speaker = obj.get("speakerName", "Unknown")
                    text = obj.get("spokenText", "")
                    if text:
                        entries.append({"timestamp": "", "speaker": speaker, "text": text})
                except json.JSONDecodeError:
                    pass
        return entries



    @tool(
        app_name="teams",
        tool_name="get_people_attended",
        description="Get people attendance records for a Teams online meeting",
        args_schema=GetPeopleAttendedInput,
        when_to_use=[
            "User wants attendees/attendance for a meeting",
            "User asks who attended a Teams meeting",
        ],
        when_not_to_use=[
            "User wants meeting list first (use get_my_meetings)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Who attended meeting X?",
            "Get attendance list for this meeting",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_people_attended(
        self,
        meeting_id: Optional[str] = None,
        event_id: Optional[str] = None,
        join_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            resolved_meeting_id = meeting_id
            if not resolved_meeting_id:
                resolved_meeting_id = await self._resolve_to_online_meeting_id(
                    event_id=event_id,
                    join_url=join_url,
                )

            if not resolved_meeting_id:
                return False, json.dumps({
                    "error": (
                        "Could not resolve to a Teams online meeting ID. "
                        "Provide meeting_id directly, or pass a valid join_url/event_id. "
                        "The event may not be a Teams meeting, or you may lack "
                        "OnlineMeetings.Read permission."
                    )
                })

            response = await self.client.teams_get_people_attended(meeting_id=resolved_meeting_id)
            if response.success:
                serialized = self._serialize_response(response.data)
                people = []
                if isinstance(serialized, dict):
                    raw_people = serialized.get("results")
                    if isinstance(raw_people, list):
                        people = raw_people
                return True, json.dumps({
                    "data": {"results": people},
                    "people": people,
                    "count": len(people),
                    "meeting_id": resolved_meeting_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get people attended"})
        except Exception as e:
            return self._handle_error(e, "get people attended")

    @tool(
        app_name="teams",
        tool_name="get_people_invited",
        description="Get people invited to a Teams meeting",
        args_schema=GetPeopleInvitedInput,
        when_to_use=[
            "User wants invited people for a meeting",
            "User asks who was invited to a Teams meeting",
        ],
        when_not_to_use=[
            "User wants attendance records (use get_people_attended)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Who was invited to meeting X?",
            "Get invitees for this Teams meeting",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_people_invited(self, meeting_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_people_invited(meeting_id=meeting_id)
            if response.success:
                serialized = self._serialize_response(response.data)
                people = []
                if isinstance(serialized, dict):
                    raw_people = serialized.get("results")
                    if isinstance(raw_people, list):
                        people = raw_people
                return True, json.dumps({
                    "data": {"results": people},
                    "people": people,
                    "count": len(people),
                    "meeting_id": meeting_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get people invited"})
        except Exception as e:
            return self._handle_error(e, "get people invited")

    @tool(
        app_name="teams",
        tool_name="create_event",
        description="Create a calendar event/meeting for the authenticated Teams user",
        args_schema=CreateEventInput,
        when_to_use=[
            "User wants to schedule/create a meeting event",
            "User asks to create a Teams event and it is not a channel meeting",
        ],
        when_not_to_use=[
            "When user wants to create a meeting for a channel, use create_channel_meeting",
            "User wants to list meetings (use get_my_meetings)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create an event tomorrow at 10 AM",
            "Schedule a Teams meeting for next week",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def create_event(
        # self,
        # subject: str,
        # start_datetime: str,
        # end_datetime: str,
        # timezone: Optional[str] = "UTC",
        # description: Optional[str] = None,
        # is_online_meeting: Optional[bool] = True,
        self,
        subject: str,
        start_datetime: str,
        end_datetime: str,
        timezone: Optional[str] = "UTC",
        body: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        recurrence: Optional[Dict[str, Any]] = None,
        is_online_meeting: Optional[bool] = False,
    ) -> tuple[bool, str]:
        try:
            tz = timezone or "UTC"
            event_body: Dict[str, Any] = {
                "subject": subject,
                "start": {
                    "dateTime": start_datetime,
                    "timeZone": tz,
                },
                "end": {
                    "dateTime": end_datetime,
                    "timeZone": tz,
                },
                "isOnlineMeeting": bool(is_online_meeting),
                "onlineMeetingProvider": "teamsForBusiness",
            }

            if body:
                event_body["body"] = {
                    "contentType": "Text",
                    "content": body,
                }

            if location:
                event_body["location"] = {"displayName": location}

            if attendees:
                event_body["attendees"] = [
                    {
                        "emailAddress": {"address": addr.strip()},
                        "type": "required",
                    }
                    for addr in attendees
                    if addr.strip()
                ]

            if recurrence:
                event_body["recurrence"] = _build_recurrence_body(recurrence)
            response = await self.client.me_calendar_create_events(request_body=event_body)
            if response.success:
                serialized_result = self._serialize_response(response.data)
                event_id = None
                if isinstance(serialized_result, dict):
                    event_id = serialized_result.get("id")
                return True, json.dumps({
                    "message": "Event created successfully",
                    "event_id": event_id,
                    "subject": subject,
                    "result": serialized_result,
                })
            return False, json.dumps({"error": response.error or "Failed to create event"})
        except Exception as e:
            return self._handle_error(e, "create event")

    @tool(
        app_name="teams",
        tool_name="create_channel_meeting",
        description="Schedule an online meeting for a Teams channel in a team",
        args_schema=CreateChannelMeetingInput,
        when_to_use=[
            "User wants to schedule a meeting for a specific Teams channel",
            "User asks to create a channel meeting in a team",
        ],
        when_not_to_use=[
            "User wants a personal calendar event (use create_event)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Schedule a channel meeting tomorrow at 10 AM",
            "Create a Teams meeting for the Engineering channel",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def create_channel_meeting(
        self,
        team_id: str,
        channel_name: str,
        subject: str,
        start_datetime: str,
        end_datetime: str,
        timezone: Optional[str] = "Asia/Kolkata",
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_create_channel_meeting(
                team_id=team_id,
                channel_name=channel_name,
                subject=subject,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                timezone=timezone or "Asia/Kolkata",
            )
            if response.success:
                serialized_result = self._serialize_response(response.data)
                event_id = None
                if isinstance(serialized_result, dict):
                    event_id = serialized_result.get("id")
                return True, json.dumps(
                    {
                        "message": "Channel meeting created successfully",
                        "event_id": event_id,
                        "team_id": team_id,
                        "channel_name": channel_name,
                        "subject": subject,
                        "result": serialized_result,
                    }
                )
            return False, json.dumps(
                {"error": response.error or response.message or "Failed to create channel meeting"}
            )
        except Exception as e:
            return self._handle_error(e, "create channel meeting")

    @tool(
        app_name="teams",
        tool_name="edit_event",
        description="Edit/update an existing calendar event for the authenticated Teams user",
        args_schema=EditEventInput,
        when_to_use=[
            "User wants to edit an existing meeting/event",
            "User asks to update Teams event details",
        ],
        when_not_to_use=[
            "User wants to create a new event (use create_event)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update event title and time",
            "Edit my Teams meeting details",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def edit_event(
        self,
        event_id: str,
        subject: Optional[str] = None,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        timezone: Optional[str] = "UTC",
        description: Optional[str] = None,
        is_online_meeting: Optional[bool] = None,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_edit_event(
                event_id=event_id,
                subject=subject,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                timezone=timezone,
                description=description,
                is_online_meeting=is_online_meeting,
            )
            if response.success:
                serialized_result = self._serialize_response(response.data)
                return True, json.dumps({
                    "message": "Event updated successfully",
                    "event_id": event_id,
                    "result": serialized_result,
                })
            return False, json.dumps({"error": response.error or "Failed to edit event"})
        except Exception as e:
            return self._handle_error(e, "edit event")

    # Team tools
    # ------------------------------------------------------------------

    @tool(
        app_name="teams",
        tool_name="get_teams",
        description="List Microsoft Teams that the authenticated user has joined",
        args_schema=GetTeamsInput,
        when_to_use=[
            "User wants to list their Microsoft Teams",
            "User asks what Teams they are in",
            "User needs to find a team ID before performing another operation",
        ],
        when_not_to_use=[
            "User wants details of one team (use get_team)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my teams",
            "List Microsoft Teams I joined",
            "What Teams am I part of?",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_teams(self, top: Optional[int] = 20) -> tuple[bool, str]:
        try:
            response = await self.client.me_list_joined_teams()
            if response.success:
                serialized = self._serialize_response(response.data)
                teams = self._extract_collection_items(serialized)
                limit = min(top or 20, 100)
                teams = teams[:limit]
                return True, json.dumps({
                    "teams": teams,
                    "count": len(teams),
                    # Backward-compatible shape for placeholder resolvers
                    # expecting teams.get_teams.data.results[*].id
                    "data": {
                        "results": teams,
                    },
                })
            return False, json.dumps({"error": response.error or "Failed to get teams"})
        except Exception as e:
            return self._handle_error(e, "get teams")

    @tool(
        app_name="teams",
        tool_name="get_team",
        description="Get details for a specific Microsoft Team",
        args_schema=GetTeamInput,
        when_to_use=[
            "User wants details for a specific team by ID",
            "User provides a team ID and asks to fetch it",
        ],
        when_not_to_use=[
            "User wants to list teams (use get_teams)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details for team ID X",
            "Show this Teams team details",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_team(self, team_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.me_get_joined_teams(team_id=team_id)
            if response.success:
                return True, json.dumps(self._serialize_response(response.data))
            return False, json.dumps({"error": response.error or "Failed to get team"})
        except Exception as e:
            return self._handle_error(e, f"get team {team_id}")

    @tool(
        app_name="teams",
        tool_name="create_team",
        description="Create a new Microsoft Team",
        args_schema=CreateTeamInput,
        when_to_use=[
            "User wants to create a new Microsoft Team",
            "User asks to create a team workspace",
        ],
        when_not_to_use=[
            "User wants to create a channel only (use create_channel)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a new team called Product Launch",
            "Set up a Teams workspace for project alpha",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def create_team(self, display_name: str, description: Optional[str] = None) -> tuple[bool, str]:
        try:
            request_body: Dict[str, Any] = {
                "template@odata.bind": "https://graph.microsoft.com/v1.0/teamsTemplates('standard')",
                "displayName": display_name,
            }
            if description:
                request_body["description"] = description

            response = await self.client.teams_team_create_team(body=request_body)
            if response.success:
                serialized_result = self._serialize_response(response.data)
                team_id = self._extract_team_id(serialized_result)
                matched_team: Optional[Dict[str, Any]] = None

                if not team_id:
                    matched_team = await self._wait_for_created_team(display_name=display_name)
                    if matched_team:
                        team_id = matched_team.get("id")

                if team_id:
                    return True, json.dumps({
                        "message": "Team created successfully",
                        "display_name": display_name,
                        "team_id": team_id,
                        "result": serialized_result,
                        "provisioning_status": "completed",
                    })

                return True, json.dumps({
                    "message": "Team creation request submitted successfully",
                    "display_name": display_name,
                    "result": serialized_result,
                    "provisioning_status": "accepted",
                    "next_step": "Use get_teams shortly to fetch the new team_id once provisioning finishes.",
                })
            return False, json.dumps({"error": response.error or "Failed to create team"})
        except Exception as e:
            return self._handle_error(e, "create team")

   

    @tool(
        app_name="teams",
        tool_name="get_members",
        description="List members in a Microsoft Team or channel",
        args_schema=GetMembersInput,
        when_to_use=[
            "User wants to list members of a specific Microsoft Team",
            "User wants members of a specific Teams channel",
            "User needs member IDs before remove_member",
            "User asks who is in a Teams team",
        ],
        when_not_to_use=[
            "User wants to list channels (use get_channels)",
            "User wants one specific user profile (use get_user_info)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show members in team X",
            "List members in channel Y of team X",
            "List users in this Microsoft Team",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_members(
        self,
        team_id: str,
        channel_id: Optional[str] = None,
        top: Optional[int] = 100,
    ) -> tuple[bool, str]:
        try:
            membership_scope = "team"
            membership_type: Optional[str] = None

            if channel_id:
                channels_response = await self.client.teams_get_channels(team_id=team_id)
                if not channels_response.success:
                    return False, json.dumps({
                        "error": channels_response.error or "Failed to fetch channels to resolve membership scope"
                    })

                serialized_channels = self._serialize_response(channels_response.data)
                channels = self._extract_collection_items(serialized_channels)
                channel_obj = next(
                    (
                        channel
                        for channel in channels
                        if isinstance(channel, dict) and channel.get("id") == channel_id
                    ),
                    None,
                )
                if not channel_obj:
                    return False, json.dumps({
                        "error": f"Channel '{channel_id}' was not found in team '{team_id}'"
                    })

                membership_type = str(
                    channel_obj.get("membershipType")
                    or channel_obj.get("membership_type")
                    or "standard"
                ).strip().lower()

                # Standard channels inherit team membership.
                if membership_type == "standard":
                    response = await self.client.teams_list_members(team_id=team_id)
                    membership_scope = "team"
                else:
                    response = await self.client.teams_list_channel_members(
                        team_id=team_id,
                        channel_id=channel_id,
                    )
                    membership_scope = "channel"
            else:
                response = await self.client.teams_list_members(team_id=team_id)

            if response.success:
                serialized = self._serialize_response(response.data)
                members = self._extract_collection_items(serialized)
                limit = min(top or 100, 500)
                members = members[:limit]
                payload: Dict[str, Any] = {
                    "data": {
                        "results": members,
                    },
                    "members": members,
                    "count": len(members),
                    "team_id": team_id,
                }
                if channel_id:
                    payload["channel_id"] = channel_id
                    payload["membership_type"] = membership_type
                    payload["membership_scope"] = membership_scope
                return True, json.dumps(payload)
            return False, json.dumps({"error": response.error or "Failed to get members"})
        except Exception as e:
            return self._handle_error(e, f"get members for team {team_id}")


    @tool(
        app_name="teams",
        tool_name="add_member",
        description="Add a member to a Microsoft Team or channel (supports team, standard channel, and private channel)",
        args_schema=AddMemberInput,
        when_to_use=[
            "User wants to add someone to a Microsoft Team",
            "User wants to add a user to a specific private Teams channel",
            "User asks to invite a member or owner to a team/channel",
        ],
        when_not_to_use=[
            "User wants to remove a member (use remove_member)",
            "User wants to list members (use get_members)",
            "User wants to add members to a shared channel (not supported by this tool)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add user 123 to team ABC",
            "Invite jane@company.com as owner in this team",
            "Add this user to the private channel in team X",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def add_member(
        self,
        team_id: str,
        user_id: str,
        role: Optional[str] = "member",
        channel_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            logger.info(f"Adding member to team {team_id} with user {user_id} and role {role} and channel {channel_id}")
            normalized_role = (role or "member").strip().lower()
            roles = ["owner"] if normalized_role == "owner" else []
            safe_user_id = user_id.replace("'", "''")
            request_body = {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": roles,
                "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{safe_user_id}')",
            }

            # CASE 1 — Add to team
            if not channel_id:
                response = await self.client.me_joined_teams_create_members(
                    team_id=team_id,
                    body=request_body,
                )
                if response.success:
                    return True, json.dumps({
                        "message": "Member added to team successfully",
                        "team_id": team_id,
                        "user_id": user_id,
                    })
                return False, json.dumps({"error": response.error or "Failed to add member to team"})

            # CASE 2 — Channel provided: determine membership type first
            channels_response = await self.client.teams_get_channels(team_id=team_id)
            if not channels_response.success:
                return False, json.dumps({
                    "error": channels_response.error or "Failed to fetch channels to resolve membership type"
                })

            serialized_channels = self._serialize_response(channels_response.data)
            channels = self._extract_collection_items(serialized_channels)
            channel_obj = next(
                (
                    channel
                    for channel in channels
                    if isinstance(channel, dict) and channel.get("id") == channel_id
                ),
                None,
            )
            if not channel_obj:
                return False, json.dumps({
                    "error": f"Channel '{channel_id}' was not found in team '{team_id}'"
                })

            membership_type = str(
                channel_obj.get("membershipType")
                or channel_obj.get("membership_type")
                or "standard"
            ).strip().lower()

            # Standard channel inherits team membership
            if membership_type == "standard":
                response = await self.client.me_joined_teams_create_members(
                    team_id=team_id,
                    body=request_body,
                )
                if response.success:
                    return True, json.dumps({
                        "message": "User added to team (standard channel inherits team members)",
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "user_id": user_id,
                    })
                return False, json.dumps({
                    "error": response.error or "Failed to add user to team for standard channel"
                })

            # Private channel
            elif membership_type == "private":
                response = await self.client.teams_channels_create_members(
                    team_id=team_id,
                    channel_id=channel_id,
                    body=request_body,
                )
                if response.success:
                    return True, json.dumps({
                        "message": "User added to private channel",
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "user_id": user_id,
                    })
                return False, json.dumps({
                    "error": response.error or "Failed to add user to private channel"
                })

            return False, json.dumps({
                "error": f"Unsupported channel membership type: {membership_type}"
            })

        except Exception as e:
            return self._handle_error(e, "add member")
        

    @tool(
        app_name="teams",
        tool_name="get_channels",
        description="List channels in a Microsoft Team",
        args_schema=GetChannelsInput,
        when_to_use=[
            "User wants channels for a specific Microsoft Team",
            "User asks to list team channels",
            "User needs to find a channel ID before sending a message",
        ],
        when_not_to_use=[
            "User wants messages from a channel (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show channels in team X",
            "List channels for this Microsoft Team",
        ],
        category=ToolCategory.SEARCH,
    )
    async def get_channels(self, team_id: str, top: Optional[int] = 50) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_channels(team_id=team_id)
            if response.success:
                serialized = self._serialize_response(response.data)
                channels = self._extract_collection_items(serialized)
                limit = min(top or 50, 200)
                channels = channels[:limit]
                return True, json.dumps({
                    "data":{
                        "results": channels,
                    }
                })
            return False, json.dumps({"error": response.error or "Failed to get channels"})
        except Exception as e:
            return self._handle_error(e, f"get channels for team {team_id}")

    @tool(
        app_name="teams",
        tool_name="create_channel",
        description="Create a new channel in a Microsoft Team",
        args_schema=CreateChannelInput,
        when_to_use=[
            "User wants to create a new channel in a Microsoft Team",
            "User asks to add a channel to an existing team",
        ],
        when_not_to_use=[
            "User wants to create an entirely new team (use create_team)",
            "User wants to list existing channels (use get_channels)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a channel called 'announcements' in team X",
            "Add a private channel to my team",
            "Set up a new channel for the design team",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def create_channel(
        self,
        team_id: str,
        display_name: str,
        description: Optional[str] = None,
        channel_type: Optional[str] = "standard",
    ) -> tuple[bool, str]:
        try:
            membership_type = (channel_type or "standard").strip().lower()
            if membership_type not in ("standard", "private"):
                membership_type = "standard"

            request_body: Dict[str, Any] = {
                "displayName": display_name,
                "membershipType": membership_type,
            }
            if description:
                request_body["description"] = description

            response = await self.client.teams_create_channels(
                team_id=team_id,
                body=request_body,
            )
            if response.success:
                data = self._serialize_response(response.data)
                channel_id = None
                if isinstance(data, dict):
                    channel_id = data.get("id")
                return True, json.dumps({
                    "message": "Channel created successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "display_name": display_name,
                    "channel": data,
                })
            return False, json.dumps({"error": response.error or "Failed to create channel"})
        except Exception as e:
            return self._handle_error(e, f"create channel in team {team_id}")

    # @tool(
    #     app_name="teams",
    #     tool_name="delete_channel",
    #     description="Delete a channel from a Microsoft Team",
    #     args_schema=DeleteChannelInput,
    #     when_to_use=[
    #         "User wants to delete or remove a channel from a Microsoft Team",
    #         "User asks to permanently remove a team channel",
    #     ],
    #     when_not_to_use=[
    #         "User wants to delete the entire team (use delete_team)",
    #         "User wants to send a message (use send_message)",
    #         "No Teams mention",
    #     ],
    #     primary_intent=ToolIntent.ACTION,
    #     typical_queries=[
    #         "Delete channel X from team Y",
    #         "Remove the announcements channel",
    #         "Permanently delete this Teams channel",
    #     ],
    #     category=ToolCategory.COMMUNICATION,
    # )
    # async def delete_channel(
    #     self,
    #     team_id: str,
    #     channel_id: str,
    # ) -> tuple[bool, str]:
    #     try:
    #         response = await self.client.teams_delete_channels(
    #             team_id=team_id,
    #             channel_id=channel_id,
    #         )
    #         if response.success:
    #             return True, json.dumps({
    #                 "message": "Channel deleted successfully",
    #                 "team_id": team_id,
    #                 "channel_id": channel_id,
    #             })
    #         return False, json.dumps({"error": response.error or "Failed to delete channel"})
    #     except Exception as e:
    #         return self._handle_error(e, f"delete channel {channel_id} from team {team_id}")

    @tool(
        app_name="teams",
        tool_name="update_channel",
        description="Update a Microsoft Teams channel's display name or description",
        args_schema=UpdateChannelInput,
        when_to_use=[
            "User wants to rename or update description of a Teams channel",
            "User asks to modify channel properties",
        ],
        when_not_to_use=[
            "User wants to delete the channel (use delete_channel)",
            "User wants to send a message to the channel (use send_message)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Rename channel X to 'general-updates'",
            "Update the description of channel Y",
            "Change the channel name",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def update_channel(
        self,
        team_id: str,
        channel_id: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            patch_body: Dict[str, Any] = {}
            if display_name is not None:
                patch_body["displayName"] = display_name
            if description is not None:
                patch_body["description"] = description

            if not patch_body:
                return False, json.dumps({"error": "No fields provided to update"})

            response = await self.client.teams_update_channels(
                team_id=team_id,
                channel_id=channel_id,
                body=patch_body,
            )
            if response.success:
                return True, json.dumps({
                    "message": "Channel updated successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                })
            return False, json.dumps({"error": response.error or "Failed to update channel"})
        except Exception as e:
            return self._handle_error(e, f"update channel {channel_id} in team {team_id}")

    # ------------------------------------------------------------------
    # Channel message tools
    # ------------------------------------------------------------------

    @tool(
        app_name="teams",
        tool_name="send_channel_message",
        description="Send a message to a Microsoft Teams channel",
        args_schema=SendChannelMessageInput,
        when_to_use=[
            "User wants to send a message in Microsoft Teams",
            "User asks to post in a specific Teams channel",
            "User mentions Teams and channel messaging",
        ],
        when_not_to_use=[
            "User wants to read channel history (use get_channel_messages)",
            "User wants to list teams/channels",
            "User wants to create a 1:1 chat (use create_chat)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Post this update to the engineering channel",
            "Send a Teams message to team X channel Y",
            "Notify the team in Microsoft Teams",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def send_channel_message(self, team_id: str, channel_id: str, message: str) -> tuple[bool, str]:
        try:
            response = await self.client.teams_send_channel_message(
                team_id=team_id,
                channel_id=channel_id,
                message=message,
            )
            if response.success:
                serialized_result = self._serialize_response(response.data)
                return True, json.dumps({
                    "message": "Teams message sent successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "result": serialized_result,
                })
            return False, json.dumps({"error": response.error or "Failed to send Teams message"})
        except Exception as e:
            return self._handle_error(e, "send Teams message")

    @tool(
        app_name="teams",
        tool_name="send_user_message",
        description="Send a direct message to a Microsoft Teams user",
        args_schema=SendUserMessageInput,
        when_to_use=[
            "User wants to send a Teams message to a specific person",
            "User provides a user name/email/id and asks to send a message",
            "User asks to send a direct message in Teams",
        ],
        when_not_to_use=[
            "User wants to send a channel message (use send_channel_message)",
            "User wants to read conversation history (use get_user_conversations)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send a Teams message to john@company.com",
            "Message Alex in Teams saying deployment is done",
            "Send a direct Teams message to Vansh",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def send_user_message(
        self,
        user_identifier: str,
        message: str,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_send_message_to_user(
                user_identifier=user_identifier,
                message=message,
            )
            if response.success:
                serialized_result = self._serialize_response(response.data)
                return True, json.dumps(
                    {
                        "message": "Teams direct message sent successfully",
                        "user_identifier": user_identifier,
                        "result": serialized_result,
                    }
                )
            return False, json.dumps(
                {"error": response.error or "Failed to send Teams direct message"}
            )
        except Exception as e:
            return self._handle_error(e, "send Teams direct message")

    @tool(
        app_name="teams",
        tool_name="reply_to_message",
        description="Reply to a specific message in a Microsoft Teams channel thread",
        args_schema=ReplyToMessageInput,
        when_to_use=[
            "User wants to reply to a specific Teams message",
            "User asks to post a thread reply in Teams",
        ],
        when_not_to_use=[
            "User wants a new top-level message (use send_message)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Reply to this Teams message",
            "Post a thread reply in channel X",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def reply_to_message(
        self,
        team_id: str,
        channel_id: str,
        parent_message_id: str,
        message: str,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_reply_to_channel_message(
                team_id=team_id,
                channel_id=channel_id,
                parent_message_id=parent_message_id,
                message=message,
            )
            if response.success:
                return True, json.dumps({
                    "message": "Reply posted successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "parent_message_id": parent_message_id,
                    "result": self._serialize_response(response.data),
                })
            return False, json.dumps({"error": response.error or "Failed to reply to message"})
        except Exception as e:
            return self._handle_error(e, "reply to Teams message")

    @tool(
        app_name="teams",
        tool_name="send_message_to_multiple_channels",
        description="Send the same message to multiple channels in a Microsoft Team",
        args_schema=SendMessageToMultipleChannelsInput,
        when_to_use=[
            "User wants to broadcast a message to multiple Teams channels",
            "User asks to post same update in several channels",
        ],
        when_not_to_use=[
            "User wants a single-channel message (use send_message)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send this update to channels A, B, and C",
            "Broadcast message to multiple Teams channels",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def send_message_to_multiple_channels(
        self,
        team_id: str,
        channel_ids: List[str],
        message: str,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_send_message_to_multiple_channels(
                team_id=team_id,
                channel_ids=channel_ids,
                message=message,
            )
            serialized = self._serialize_response(response.data)
            return response.success, json.dumps({
                "message": "Message sent to multiple channels" if response.success else "One or more channel sends failed",
                "result": serialized,
                "error": response.error,
            })
        except Exception as e:
            return self._handle_error(e, "send Teams message to multiple channels")

    @tool(
        app_name="teams",
        tool_name="search_messages",
        description="Search messages in Microsoft Teams channels",
        args_schema=SearchMessagesInput,
        when_to_use=[
            "User wants to find Teams messages by text",
            "User asks to search channel messages",
        ],
        when_not_to_use=[
            "User wants full channel history without filtering (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search Teams messages for deployment failure",
            "Find channel messages containing keyword X",
        ],
        category=ToolCategory.SEARCH,
    )
    async def search_messages(
        self,
        query: str,
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        top_per_channel: Optional[int] = 25,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_search_messages(
                query=query,
                team_id=team_id,
                channel_id=channel_id,
                top_per_channel=max(top_per_channel or 25, 1),
            )
            if response.success:
                serialized = self._serialize_response(response.data)
                results = []
                if isinstance(serialized, dict):
                    raw_results = serialized.get("results")
                    if isinstance(raw_results, list):
                        results = raw_results
                return True, json.dumps({
                    "data": {"results": results},
                    "results": results,
                    "count": len(results),
                    "query": query,
                })
            return False, json.dumps({"error": response.error or "Failed to search messages"})
        except Exception as e:
            return self._handle_error(e, "search Teams messages")

    @tool(
        app_name="teams",
        tool_name="add_reaction",
        description="Add a reaction to a Microsoft Teams channel message",
        args_schema=AddReactionInput,
        when_to_use=[
            "User wants to react to a Teams channel message",
            "User asks to add emoji reaction on a Teams message",
            "User provides message ID and wants to add reaction",
        ],
        when_not_to_use=[
            "User wants to send a new channel message (use send_message)",
            "User wants to read message history (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add a like reaction to this Teams message",
            "React with heart to message ID X",
            "Add emoji reaction in Teams channel",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def add_reaction(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        reaction_type: str,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_add_message_reaction(
                team_id=team_id,
                channel_id=channel_id,
                message_id=message_id,
                reaction_type=reaction_type,
            )
            if response.success:
                serialized_result = self._serialize_response(response.data)
                return True, json.dumps({
                    "message": "Reaction added successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "reaction_type": (reaction_type or "").strip().lower(),
                    "result": serialized_result,
                })
            return False, json.dumps({"error": response.error or "Failed to add reaction"})
        except Exception as e:
            return self._handle_error(e, "add Teams reaction")

    @tool(
        app_name="teams",
        tool_name="get_reactions",
        description="Get reactions for a Microsoft Teams channel message",
        args_schema=GetReactionsInput,
        when_to_use=[
            "User wants to view reactions on a Teams message",
            "User asks what emoji reactions a message has",
        ],
        when_not_to_use=[
            "User wants to add reaction (use add_reaction)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show reactions for this message",
            "Get Teams message emoji reactions",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_reactions(self, team_id: str, channel_id: str, message_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_message_reactions(
                team_id=team_id,
                channel_id=channel_id,
                message_id=message_id,
            )
            if response.success:
                serialized = self._serialize_response(response.data)
                reactions = []
                if isinstance(serialized, dict):
                    raw_reactions = serialized.get("reactions")
                    if isinstance(raw_reactions, list):
                        reactions = raw_reactions
                return True, json.dumps({
                    "data": {"results": reactions},
                    "reactions": reactions,
                    "count": len(reactions),
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get reactions"})
        except Exception as e:
            return self._handle_error(e, "get Teams reactions")

    @tool(
        app_name="teams",
        tool_name="remove_reaction",
        description="Remove a reaction from a Microsoft Teams channel message",
        args_schema=RemoveReactionInput,
        when_to_use=[
            "User wants to remove emoji reaction from Teams message",
            "User asks to undo reaction on a message",
        ],
        when_not_to_use=[
            "User wants to add reaction (use add_reaction)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Remove like reaction from message",
            "Undo heart reaction in Teams",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def remove_reaction(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        reaction_type: str,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_remove_message_reaction(
                team_id=team_id,
                channel_id=channel_id,
                message_id=message_id,
                reaction_type=reaction_type,
            )
            if response.success:
                return True, json.dumps({
                    "message": "Reaction removed successfully",
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "reaction_type": (reaction_type or "").strip().lower(),
                    "result": self._serialize_response(response.data),
                })
            return False, json.dumps({"error": response.error or "Failed to remove reaction"})
        except Exception as e:
            return self._handle_error(e, "remove Teams reaction")

    @tool(
        app_name="teams",
        tool_name="get_channel_messages",
        description="List messages from a Microsoft Teams channel",
        args_schema=GetChannelMessagesInput,
        when_to_use=[
            "User wants to read message history from a Teams channel",
            "User asks for recent messages in a channel",
        ],
        when_not_to_use=[
            "User wants to send a new message (use send_message)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get recent messages from channel X",
            "Show latest posts in this Teams channel",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_channel_messages(
        self,
        team_id: str,
        channel_id: str,
        top: Optional[int] = 20,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_channel_messages(
                team_id=team_id,
                channel_id=channel_id,
            )
            if response.success:
                serialized = self._serialize_response(response.data)
                messages = self._extract_collection_items(serialized)
                limit = min(top or 20, 100)
                messages = messages[:limit]
                return True, json.dumps({
                    "data": {
                        "results": messages,
                    },
                    "messages": messages,
                    "count": len(messages),
                    "team_id": team_id,
                    "channel_id": channel_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get channel messages"})
        except Exception as e:
            return self._handle_error(e, "get channel messages")

    @tool(
        app_name="teams",
        tool_name="get_thread_replies",
        description="Get replies in a Microsoft Teams message thread",
        args_schema=GetThreadRepliesInput,
        when_to_use=[
            "User wants thread replies for a Teams message",
            "User asks for conversation replies under a message",
        ],
        when_not_to_use=[
            "User wants top-level channel messages (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show thread replies for this message",
            "Get replies in Teams message thread",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_thread_replies(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        top: Optional[int] = 50,
    ) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_thread_replies(
                team_id=team_id,
                channel_id=channel_id,
                message_id=message_id,
            )
            if response.success:
                serialized = self._serialize_response(response.data)
                replies = self._extract_collection_items(serialized)
                limit = min(top or 50, 200)
                replies = replies[:limit]
                return True, json.dumps({
                    "data": {"results": replies},
                    "replies": replies,
                    "count": len(replies),
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                })
            return False, json.dumps({"error": response.error or "Failed to get thread replies"})
        except Exception as e:
            return self._handle_error(e, "get Teams thread replies")

    @tool(
        app_name="teams",
        tool_name="update_message",
        description="Update an existing Microsoft Teams message in a channel or direct chat",
        args_schema=UpdateMessageInput,
        when_to_use=[
            "User wants to edit a Teams channel message",
            "User wants to edit a Teams direct chat message",
            "User asks to modify message text",
        ],
        when_not_to_use=[
            "User wants to send new message (use send_message)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update this Teams message",
            "Edit message in channel",
            "Edit this direct Teams message",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def update_message(
        self,
        message_id: str,
        message: str,
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            if not chat_id and ((team_id and not channel_id) or (channel_id and not team_id)):
                return False, json.dumps({
                    "error": "Provide both team_id and channel_id for channel updates, or provide chat_id for direct chat updates",
                })

            response = await self.client.teams_update_channel_message(
                team_id=team_id,
                channel_id=channel_id,
                chat_id=chat_id,
                message_id=message_id,
                message=message,
            )

            if response.success:
                result_payload = {
                    "message": "Message updated successfully",
                    "message_id": message_id,
                    "result": self._serialize_response(response.data),
                }
                if chat_id:
                    result_payload["chat_id"] = chat_id
                elif team_id and channel_id:
                    result_payload["team_id"] = team_id
                    result_payload["channel_id"] = channel_id
                return True, json.dumps(result_payload)
            return False, json.dumps({"error": response.error or "Failed to update message"})
        except Exception as e:
            return self._handle_error(e, "update Teams message")

    @tool(
        app_name="teams",
        tool_name="get_message_permalink",
        description="Get a permalink/URL for a Microsoft Teams channel message",
        args_schema=GetMessagePermalinkInput,
        when_to_use=[
            "User wants a shareable link to a Teams message",
            "User asks for message URL",
        ],
        when_not_to_use=[
            "User wants message content list (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get permalink for this Teams message",
            "Share message URL",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_message_permalink(self, team_id: str, channel_id: str, message_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.teams_get_message_permalink(
                team_id=team_id,
                channel_id=channel_id,
                message_id=message_id,
            )
            if response.success:
                data = self._serialize_response(response.data)
                permalink = data.get("permalink") if isinstance(data, dict) else None
                return True, json.dumps({
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "permalink": permalink,
                    "result": data,
                })
            return False, json.dumps({"error": response.error or "Failed to get message permalink"})
        except Exception as e:
            return self._handle_error(e, "get Teams message permalink")

    # ------------------------------------------------------------------
    # Chat tools
    # ------------------------------------------------------------------

    @tool(
        app_name="teams",
        tool_name="create_chat",
        description="Create a 1:1 or group chat in Microsoft Teams",
        args_schema=CreateChatInput,
        when_to_use=[
            "User wants to start a direct message or group chat in Teams",
            "User asks to create a 1:1 chat with someone",
            "User wants to start a group conversation outside of a channel",
        ],
        when_not_to_use=[
            "User wants to send a message to an existing channel (use send_message)",
            "User wants to create a whole new team (use create_team)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Start a 1:1 chat with john@example.com",
            "Create a group chat with Alice and Bob",
            "Open a direct message conversation in Teams",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def create_chat(
        self,
        chat_type: str,
        member_user_ids: List[str],
        topic: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            normalized_type = (chat_type or "oneOnOne").strip()
            if normalized_type not in ("oneOnOne", "group"):
                normalized_type = "oneOnOne"

            members = [
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{uid.strip()}')",
                }
                for uid in member_user_ids
                if uid.strip()
            ]

            request_body: Dict[str, Any] = {
                "chatType": normalized_type,
                "members": members,
            }
            if topic and normalized_type == "group":
                request_body["topic"] = topic

            response = await self.client.me_create_chats(body=request_body)
            if response.success:
                data = self._serialize_response(response.data)
                chat_id = None
                if isinstance(data, dict):
                    chat_id = data.get("id")
                return True, json.dumps({
                    "message": "Chat created successfully",
                    "chat_id": chat_id,
                    "chat_type": normalized_type,
                    "chat": data,
                })
            return False, json.dumps({"error": response.error or "Failed to create chat"})
        except Exception as e:
            return self._handle_error(e, "create chat")

    @tool(
        app_name="teams",
        tool_name="get_chat",
        description="Get details of a specific Microsoft Teams chat",
        args_schema=GetChatInput,
        when_to_use=[
            "User wants details of a specific Teams chat by its ID",
            "User needs to look up chat metadata",
        ],
        when_not_to_use=[
            "User wants to list all teams (use get_teams)",
            "User wants to read channel messages (use get_channel_messages)",
            "No Teams mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details for chat ID X",
            "Show me information about this Teams chat",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_chat(self, chat_id: str) -> tuple[bool, str]:
        try:
            response = await self.client.me_get_chats(chat_id=chat_id)
            if response.success:
                return True, json.dumps(self._serialize_response(response.data))
            return False, json.dumps({"error": response.error or "Failed to get chat"})
        except Exception as e:
            return self._handle_error(e, f"get chat {chat_id}")
