import json
import logging
from typing import Annotated, Any, Literal, Optional, Tuple

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from urllib.parse import quote
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
from app.sources.client.zoom.zoom import ZoomClient, ZoomResponse
from app.sources.external.zoom.zoom import ZoomDataSource

logger = logging.getLogger(__name__)


def _coerce_meeting_id(v: Any) -> str:
    """Accept meeting_id as int or str (e.g. from tool result); coerce to str."""
    return str(v)


# meeting_id from Zoom API / tool results can be int; coerce to str for API calls
MeetingId = Annotated[str, BeforeValidator(_coerce_meeting_id)]


# ---------------------------------------------------------------------------
# Pydantic input schemas
# ---------------------------------------------------------------------------

class GetMyProfileInput(BaseModel):
    user_id: str = Field(
        default="me",
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )


class ListMeetingsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )
    type_: Literal["scheduled", "live", "upcoming", "all"] = Field(
        default="scheduled",
        alias="type",
        description="Meeting type filter.",
    )
    page_size: Optional[int] = Field(default=30, ge=1, le=300, description="Results per page (max 300).")
    next_page_token: Optional[str] = Field(default=None, description="Pagination token.")
    from_: Optional[str] = Field(default=None, alias="from", description="Start date (YYYY-MM-DD).")
    to: Optional[str] = Field(default=None, description="End date (YYYY-MM-DD).")


class GetMeetingInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID for recurring meetings. (optional)")


class CreateMeetingInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )
    topic: str = Field(description="Meeting topic/title.")
    start_time: Optional[str] = Field(default=None, description="Start time in ISO 8601 format (e.g. 2025-03-20T14:00:00Z).")
    duration: Optional[int] = Field(default=60, ge=1, description="Duration in minutes (default 60).")
    timezone: Optional[str] = Field(default=None, description="Timezone (e.g. Asia/Kolkata). Infer from context if not stated.")
    agenda: Optional[str] = Field(default=None, description="Meeting agenda/description.")
    type_: Optional[int] = Field(
        default=2,
        alias="type",
        description="Meeting type: 1=instant, 2=scheduled.",
    )


class UpdateMeetingInput(BaseModel):
    """Only include fields the user explicitly asked to change."""
    meeting_id: MeetingId = Field(description="Zoom meeting ID to update.")
    topic: Optional[str] = Field(default=None, description="New meeting topic. Only set if user asked to rename it.")
    start_time: Optional[str] = Field(default=None, description="New start time in ISO 8601 (e.g. 2026-03-16T17:00:00). Only set if user asked to reschedule.")
    duration: Optional[int] = Field(default=None, description="New duration in minutes. Only set if user mentioned it.")
    timezone: Optional[str] = Field(default=None, description="Timezone for start_time. Infer from context; default Asia/Kolkata if user is in India.")
    agenda: Optional[str] = Field(default=None, description="New agenda. Only set if user provided one.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID for recurring meetings only.")


class DeleteMeetingInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID to delete.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID to delete only one occurrence of a recurring meeting.")
    cancel_meeting_reminder: Optional[bool] = Field(default=None, description="Send cancellation email to registrants.")


class ListUpcomingMeetingsInput(BaseModel):
    user_id: str = Field(
        default="me",
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )


class GetMeetingInvitationInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID.")


# class ListRecordingsInput(BaseModel):
#     model_config = ConfigDict(populate_by_name=True)

#     user_id: str = Field(
#         default="me",
#         description="Zoom user ID or email. Use 'me' for the authenticated user.",
#     )
#     page_size: Optional[int] = Field(default=30, ge=1, le=300, description="Results per page.")
#     next_page_token: Optional[str] = Field(default=None, description="Pagination token.")
#     from_: Optional[str] = Field(default=None, alias="from", description="Start date (YYYY-MM-DD).")
#     to: Optional[str] = Field(default=None, description="End date (YYYY-MM-DD).")
#     meeting_id: Optional[str] = Field(default=None, description="Filter by meeting ID.")


# class GetMeetingRecordingsInput(BaseModel):
#     meeting_id: MeetingId = Field(description="Meeting ID or UUID.")


class GetMeetingTranscriptInput(BaseModel):
    meeting_id: MeetingId = Field(description="Meeting ID or UUID of the recorded meeting.")


class DeleteMeetingTranscriptInput(BaseModel):
    meeting_id: MeetingId = Field(description="Meeting ID or UUID whose transcript to delete.")


class SearchMeetingsByNameInput(BaseModel):
    query: str = Field(description="Meeting name or topic keyword to search for.")
    user_id: str = Field(default="me", description="Zoom user ID or email. Use 'me' for the authenticated user.")


class ListPastMeetingsInput(BaseModel):
    user_id: str = Field(default="me", description="Zoom user ID or email. Use 'me' for the authenticated user.")
    page_size: Optional[int] = Field(default=30, ge=1, le=300, description="Number of past meetings to return (max 300).")
    from_: Optional[str] = Field(default=None, alias="from", description="Start date filter (YYYY-MM-DD).")
    to: Optional[str] = Field(default=None, description="End date filter (YYYY-MM-DD).")


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

@ToolsetBuilder("Zoom")\
    .in_group("Video & Meetings")\
    .with_description("Zoom integration for meetings, webinars, and collaboration")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Zoom",
            authorize_url="https://zoom.us/oauth/authorize",
            token_url="https://zoom.us/oauth/token",
            redirect_uri="toolsets/oauth/callback/zoom",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "meeting:read:meeting",
                    "meeting:read:list_meetings",
                    "meeting:read:list_upcoming_meetings",
                    "meeting:read:invitation",
                    "meeting:write:meeting",
                    "meeting:delete:meeting",
                    "user:read:email",
                    "user:read:user",
                    "cloud_recording:read:list_user_recordings",
                    "cloud_recording:read:list_recording_files",
                    "cloud_recording:read:recording",
                    "cloud_recording:read:meeting_transcript",
                    "meeting:read:list_past_instances",
                    "meeting:update:meeting",
                ]
            ),
            fields=[
                CommonFields.client_id("Zoom Marketplace App"),
                CommonFields.client_secret("Zoom Marketplace App"),
            ],
            icon_path="/assets/icons/connectors/zoom.svg",
            app_group="Video & Meetings",
            app_description="Zoom OAuth application for agent integration",
        ),
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/zoom.svg"))\
    .build_decorator()
class Zoom:
    """Zoom tools exposed to agents using ZoomDataSource."""

    def __init__(self, client: ZoomClient) -> None:
        self.client = ZoomDataSource(client)

    def _handle_response(self, response: Any, success_message: str) -> Tuple[bool, str]:
        """Normalize ZoomResponse to (success, json_string)."""
        if isinstance(response, ZoomResponse):
            if response.success:
                return True, json.dumps({"message": success_message, "data": response.data})
            error = response.error or response.message or "Unknown error"
            return False, json.dumps({"error": error})
        # Fallback for raw dict responses
        if isinstance(response, dict):
            if response.get("code") or response.get("error"):
                return False, json.dumps(response)
            return True, json.dumps({"message": success_message, "data": response})
        return True, json.dumps({"message": success_message, "data": str(response)})

    # ------------------------------------------------------------------
    # User tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="get_my_profile",
        description="Get the authenticated Zoom user's profile.",
        llm_description="Returns the Zoom user's profile (name, email, timezone, account type). Use user_id='me' for the token owner.",
        args_schema=GetMyProfileInput,
        returns="JSON with user profile details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to know their Zoom account details",
            "User asks for their Zoom email or timezone",
        ],
        when_not_to_use=[
            "User wants to list or manage meetings",
        ],
        typical_queries=["My Zoom profile", "What is my Zoom email?", "Get my Zoom account info"],
    )
    async def get_my_profile(self, user_id: str = "me") -> Tuple[bool, str]:
        """Get Zoom user profile."""
        try:
            logger.info("zoom.get_my_profile called for user_id=%s", user_id)
            response = await self.client.user(userId=user_id)
            return self._handle_response(response, "User profile fetched successfully")
        except Exception as e:
            logger.error("Error fetching user profile: %s", e)
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Meeting tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="list_meetings",
        description="List Zoom meetings for a user.",
        llm_description="Lists meetings for a user. Use user_id='me' for the authenticated user. Optional: type (scheduled/live/upcoming/all), page_size, from/to dates.",
        args_schema=ListMeetingsInput,
        returns="JSON with list of meetings and pagination token",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to see their Zoom meetings",
            "User asks for scheduled or upcoming meetings",
        ],
        when_not_to_use=[
            "User wants a single meeting's details (use get_meeting)",
            "User wants to create a meeting (use create_meeting)",
        ],
        typical_queries=["List my Zoom meetings", "Show my scheduled meetings", "What Zoom meetings do I have?"],
    )
    async def list_meetings(
        self,
        user_id: str,
        type_: str = "scheduled",
        page_size: Optional[int] = 30,
        next_page_token: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """List meetings for a Zoom user."""
        try:
            logger.info("zoom.list_meetings called with user_id=%s type=%s", user_id, type_)
            response = await self.client.meetings(
                userId=user_id,
                type_=type_,
                page_size=page_size,
                next_page_token=next_page_token,
                from_=from_,
                to=to,
            )
            return self._handle_response(response, "Meetings listed successfully")
        except Exception as e:
            logger.error("Error listing meetings: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="get_meeting",
        description="Get details of a specific Zoom meeting.",
        llm_description="Returns a single meeting by meeting_id including join URL, time, and settings.",
        args_schema=GetMeetingInput,
        returns="JSON with meeting details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants details of a specific Zoom meeting",
            "User asks for meeting link or settings",
        ],
        when_not_to_use=[
            "User wants to list meetings (use list_meetings)",
        ],
        typical_queries=["Get Zoom meeting 123", "Show meeting details", "What is the link for meeting X?"],
    )
    async def get_meeting(
        self,
        meeting_id: str,
        occurrence_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Get a Zoom meeting by ID."""
        try:
            logger.info("zoom.get_meeting called with meeting_id=%s", meeting_id)
            response = await self.client.meeting(
                meetingId=meeting_id,
                occurrence_id=occurrence_id,
            )
            return self._handle_response(response, "Meeting fetched successfully")
        except Exception as e:
            logger.error("Error getting meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="create_meeting",
        description="Create a new Zoom meeting.",
        llm_description=(
            "Creates a scheduled meeting. Required: topic. Optional: start_time (ISO 8601), duration (minutes), timezone, agenda. "
            "Infer timezone from context (e.g. if user is in India use Asia/Kolkata). "
            "Do NOT ask for password or settings unless user explicitly mentions them."
        ),
        args_schema=CreateMeetingInput,
        returns="JSON with created meeting details including join URL",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to create or schedule a Zoom meeting",
        ],
        when_not_to_use=[
            "User wants to update an existing meeting (use update_meeting)",
        ],
        typical_queries=["Create a Zoom meeting", "Schedule a meeting", "Set up a Zoom call for 5pm"],
    )
    async def create_meeting(
        self,
        user_id: str,
        topic: str,
        start_time: Optional[str] = None,
        duration: Optional[int] = 60,
        timezone: Optional[str] = None,
        agenda: Optional[str] = None,
        type_: Optional[int] = 2,
    ) -> Tuple[bool, str]:
        """Create a Zoom meeting."""
        try:
            body: dict[str, Any] = {"topic": topic}
            if start_time is not None:
                body["start_time"] = start_time
            if duration is not None:
                body["duration"] = duration
            if timezone is not None:
                body["timezone"] = timezone
            if agenda is not None:
                body["agenda"] = agenda
            if type_ is not None:
                body["type"] = type_
            logger.info("zoom.create_meeting called for user_id=%s topic=%s", user_id, topic)
            response = await self.client.meeting_create(userId=user_id, body=body)
            return self._handle_response(response, "Meeting created successfully")
        except Exception as e:
            logger.error("Error creating meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="update_meeting",
        description="Update a Zoom meeting.",
        llm_description=(
            "Updates only the fields the user explicitly asked to change. "
            "IMPORTANT: Only populate fields the user mentioned — if user says 'change to 5pm', only set start_time (and timezone if needed). "
            "NEVER ask for or include password, settings, agenda, or other fields unless the user specifically mentioned them. "
            "Infer timezone from context (default Asia/Kolkata for India). "
            "Convert natural time like '5pm' to ISO 8601 using today's date."
        ),
        args_schema=UpdateMeetingInput,
        returns="JSON confirming update",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to reschedule, rename, or change duration of a meeting",
        ],
        when_not_to_use=[
            "User wants to create a new meeting (use create_meeting)",
            "User wants to delete a meeting (use delete_meeting)",
        ],
        typical_queries=["Reschedule meeting to 5pm", "Change meeting time", "Update meeting topic", "Move my standup to 3pm"],
    )
    async def update_meeting(
        self,
        meeting_id: str,
        topic: Optional[str] = None,
        start_time: Optional[str] = None,
        duration: Optional[int] = None,
        timezone: Optional[str] = None,
        agenda: Optional[str] = None,
        occurrence_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Update a Zoom meeting — only fields explicitly provided by user."""
        try:
            body: dict[str, Any] = {}
            if topic is not None:
                body["topic"] = topic
            if start_time is not None:
                body["start_time"] = start_time
            if duration is not None:
                body["duration"] = duration
            if timezone is not None:
                body["timezone"] = timezone
            if agenda is not None:
                body["agenda"] = agenda
            if not body:
                return False, json.dumps({"error": "No fields to update were provided."})
            logger.info("zoom.update_meeting called for meeting_id=%s body=%s", meeting_id, body)
            response = await self.client.meeting_update(
                meetingId=meeting_id,
                body=body,
                occurrence_id=occurrence_id,
            )
            return self._handle_response(response, "Meeting updated successfully")
        except Exception as e:
            logger.error("Error updating meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="delete_meeting",
        description="Delete a Zoom meeting.",
        llm_description="Deletes a meeting by meeting_id. For recurring meetings pass occurrence_id to delete one occurrence only.",
        args_schema=DeleteMeetingInput,
        returns="JSON confirming deletion",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to cancel or delete a Zoom meeting",
        ],
        when_not_to_use=[
            "User wants to reschedule (use update_meeting)",
        ],
        typical_queries=["Cancel Zoom meeting", "Delete meeting", "Remove my standup"],
    )
    async def delete_meeting(
        self,
        meeting_id: str,
        occurrence_id: Optional[str] = None,
        cancel_meeting_reminder: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Delete a Zoom meeting."""
        try:
            logger.info("zoom.delete_meeting called for meeting_id=%s", meeting_id)
            response = await self.client.meeting_delete(
                meetingId=meeting_id,
                occurrence_id=occurrence_id,
                cancel_meeting_reminder=cancel_meeting_reminder,
            )
            return self._handle_response(response, "Meeting deleted successfully")
        except Exception as e:
            logger.error("Error deleting meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="list_upcoming_meetings",
        description="List upcoming Zoom meetings for a user.",
        llm_description="Returns upcoming meetings for user_id. Use 'me' for the authenticated user.",
        args_schema=ListUpcomingMeetingsInput,
        returns="JSON with upcoming meetings",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to see only upcoming Zoom meetings",
        ],
        when_not_to_use=[
            "User wants all meetings (use list_meetings)",
            "User wants a single meeting (use get_meeting)",
        ],
        typical_queries=["My upcoming Zoom meetings", "What's my next Zoom call?"],
    )
    async def list_upcoming_meetings(self, user_id: str = "me") -> Tuple[bool, str]:
        """List upcoming meetings for a user."""
        try:
            logger.info("zoom.list_upcoming_meetings called for user_id=%s", user_id)
            response = await self.client.list_upcoming_meeting(userId=user_id)
            return self._handle_response(response, "Upcoming meetings listed successfully")
        except Exception as e:
            logger.error("Error listing upcoming meetings: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="search_meetings_by_name",
        description="Search Zoom meetings by name or topic.",
        llm_description=(
            "Searches the user's scheduled and upcoming meetings by topic/name keyword. "
            "Returns matching meetings with their IDs, join URLs, and start times. "
            "Use when the user refers to a meeting by name rather than ID."
        ),
        args_schema=SearchMeetingsByNameInput,
        returns="JSON list of meetings whose topic contains the search query",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to find a meeting by name or topic",
            "User refers to a meeting by title instead of ID",
        ],
        when_not_to_use=[
            "User already knows the meeting ID (use get_meeting)",
            "User wants past meetings (use list_past_meetings)",
        ],
        typical_queries=["Find my standup meeting", "Search for design review", "Which meeting is called sync?"],
    )
    async def search_meetings_by_name(self, query: str, user_id: str = "me") -> Tuple[bool, str]:
        """Search scheduled/upcoming meetings by topic keyword."""
        try:
            logger.info("zoom.search_meetings_by_name called with query=%s user_id=%s", query, user_id)
            query_lower = query.lower()
            matched = []

            for meeting_type in ["scheduled", "upcoming"]:
                response = await self.client.meetings(userId=user_id, type_=meeting_type, page_size=100)
                if isinstance(response, ZoomResponse) and not response.success:
                    continue
                data = response.data if isinstance(response, ZoomResponse) else response
                meetings = data.get("meetings", []) if isinstance(data, dict) else []
                for m in meetings:
                    if query_lower in m.get("topic", "").lower():
                        matched.append(m)

            # deduplicate by meeting id
            seen: set = set()
            unique = [m for m in matched if not (m["id"] in seen or seen.add(m["id"]))]  # type: ignore

            if not unique:
                return False, json.dumps({"error": f"No meetings found matching '{query}'"})

            return True, json.dumps({"meetings": unique, "count": len(unique)})

        except Exception as e:
            logger.error("Error searching meetings by name: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="list_past_meetings",
        description="List previously held Zoom meetings.",
        llm_description=(
            "Returns a list of past/previous meetings for the user. "
            "Useful when the user wants to find a recent meeting by browsing history or get a meeting ID for transcript/recording lookup."
        ),
        args_schema=ListPastMeetingsInput,
        returns="JSON list of past meetings with IDs, topics, and start times",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User asks about previous or past meetings",
            "User wants to find a recent Zoom call",
            "User needs the meeting ID of a meeting that already happened",
        ],
        when_not_to_use=[
            "User wants upcoming or scheduled meetings (use list_meetings or list_upcoming_meetings)",
        ],
        typical_queries=["Show my past meetings", "List recent Zoom calls", "What meetings did I have last week?"],
    )
    async def list_past_meetings(
        self,
        user_id: str = "me",
        page_size: Optional[int] = 30,
        from_: Optional[str] = None,
        to: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """List previously held meetings for a user."""
        try:
            logger.info("zoom.list_past_meetings called for user_id=%s", user_id)
            response = await self.client.meetings(
                userId=user_id,
                type_="previous_meetings",
                page_size=page_size,
                from_=from_,
                to=to,
            )
            if isinstance(response, ZoomResponse) and not response.success:
                return False, json.dumps({"error": response.error or response.message})

            data = response.data if isinstance(response, ZoomResponse) else response
            meetings = data.get("meetings", []) if isinstance(data, dict) else []

            return True, json.dumps({"meetings": meetings, "count": len(meetings)})

        except Exception as e:
            logger.error("Error listing past meetings: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="get_meeting_invitation",
        description="Get the invitation text for a Zoom meeting.",
        llm_description="Returns the invitation body (join URL, dial-in details). Use for sharing or displaying invite.",
        args_schema=GetMeetingInvitationInput,
        returns="JSON with meeting invitation text",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants the meeting invite text or join link",
        ],
        when_not_to_use=[
            "User wants full meeting details (use get_meeting)",
        ],
        typical_queries=["Get Zoom meeting invite", "Meeting join link", "Share meeting details"],
    )
    async def get_meeting_invitation(self, meeting_id: str) -> Tuple[bool, str]:
        """Get meeting invitation text."""
        try:
            logger.info("zoom.get_meeting_invitation called for meeting_id=%s", meeting_id)
            response = await self.client.meeting_invitation(meetingId=meeting_id)
            return self._handle_response(response, "Meeting invitation fetched successfully")
        except Exception as e:
            logger.error("Error getting meeting invitation: %s", e)
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Recording tools
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="zoom",
    #     tool_name="list_recordings",
    #     description="List all cloud recordings for a Zoom user.",
    #     llm_description="Returns cloud recordings for user_id filtered by optional date range.",
    #     args_schema=ListRecordingsInput,
    #     returns="JSON with list of recordings including download URLs",
    #     primary_intent=ToolIntent.SEARCH,
    #     category=ToolCategory.COMMUNICATION,
    #     when_to_use=[
    #         "User wants to browse past Zoom recordings",
    #         "User asks for recorded meetings in a date range",
    #     ],
    #     when_not_to_use=[
    #         "User wants files for a specific meeting (use get_meeting_recordings)",
    #     ],
    #     typical_queries=["Show my Zoom recordings", "List recorded meetings", "Recordings from last week"],
    # )
    # async def list_recordings(
    #     self,
    #     user_id: str = "me",
    #     page_size: Optional[int] = 30,
    #     next_page_token: Optional[str] = None,
    #     from_: Optional[str] = None,
    #     to: Optional[str] = None,
    #     meeting_id: Optional[str] = None,
    # ) -> Tuple[bool, str]:
    #     """List all cloud recordings for a user."""
    #     try:
    #         logger.info("zoom.list_recordings called for user_id=%s", user_id)
    #         response = await self.client.recordings_list(
    #             userId=user_id,
    #             page_size=page_size,
    #             next_page_token=next_page_token,
    #             from_=from_,
    #             to=to,
    #             meeting_id=meeting_id,
    #         )
    #         return self._handle_response(response, "Recordings listed successfully")
    #     except Exception as e:
    #         logger.error("Error listing recordings: %s", e)
    #         return False, json.dumps({"error": str(e)})

    # @tool(
    #     app_name="zoom",
    #     tool_name="get_meeting_recordings",
    #     description="Get all recording files for a specific Zoom meeting.",
    #     llm_description=(
    #         "Returns all recording files for a meeting (video, audio, chat, VTT transcript). "
    #         "The transcript VTT file has recording_type='audio_transcript'."
    #     ),
    #     args_schema=GetMeetingRecordingsInput,
    #     returns="JSON with recording files list including download URLs",
    #     primary_intent=ToolIntent.SEARCH,
    #     category=ToolCategory.COMMUNICATION,
    #     when_to_use=[
    #         "User wants to download recordings from a specific meeting",
    #         "User asks for the recording link of a particular call",
    #     ],
    #     when_not_to_use=[
    #         "User wants recordings across all meetings (use list_recordings)",
    #         "User wants only the transcript (use get_meeting_transcript)",
    #     ],
    #     typical_queries=["Get recording for meeting 123", "Download Zoom call recording"],
    # )
    # async def get_meeting_recordings(
    #     self,
    #     meeting_id: str,
    # ) -> Tuple[bool, str]:
    #     """Get all recording files for a specific meeting."""
    #     try:
    #         logger.info("zoom.get_meeting_recordings called for meeting_id=%s", meeting_id)
    #         response = await self.client.recording_get(meetingId=meeting_id)
    #         return self._handle_response(response, "Meeting recordings fetched successfully")
    #     except Exception as e:
    #         logger.error("Error getting meeting recordings: %s", e)
    #         return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Transcript tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="get_meeting_transcript",
        description="Get the transcript for a recorded Zoom meeting.",
        llm_description=(
            "Returns transcript metadata and VTT download URL for a recorded meeting. "
            "Requires cloud recording with audio transcription enabled. Scope: cloud_recording:read:meeting_transcript."
        ),
        args_schema=GetMeetingTranscriptInput,
        returns="JSON with transcript metadata and VTT file download URL",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants the transcript of a Zoom call",
            "User asks what was discussed in a recorded meeting",
        ],
        when_not_to_use=[
            "User wants the video/audio recording (use get_meeting_recordings)",
        ],
        typical_queries=["Get transcript for meeting 123", "What was said in yesterday's call?"],
    )
    async def get_meeting_transcript(self, meeting_id: str) -> Tuple[bool, str]:
        try:
            logger.info("zoom.get_meeting_transcript called for meeting_id=%s", meeting_id)

            # Step 1 — get past instances to find the instance UUID
            instances_response = await self.client.past_meetings(meetingId=str(meeting_id))

            if isinstance(instances_response, ZoomResponse) and not instances_response.success:
                return False, json.dumps({"error": instances_response.error or instances_response.message})

            instances_data = instances_response.data if isinstance(instances_response, ZoomResponse) else instances_response
            meetings = instances_data.get("meetings", []) if isinstance(instances_data, dict) else []

            if not meetings:
                return False, json.dumps({
                    "error": "No past instances found for this meeting. Has the meeting ended yet?",
                    "meeting_id": meeting_id,
                })

            # Step 2 — take the most recent instance UUID and double-encode if needed
            instance_uuid = meetings[-1].get("uuid")  # last = most recent
            if not instance_uuid:
                return False, json.dumps({"error": "Instance UUID missing from past meeting data."})

            if instance_uuid.startswith("/") or "/" in instance_uuid:
                encoded_uuid = quote(quote(instance_uuid, safe=""), safe="")
            else:
                encoded_uuid = instance_uuid

            logger.info("Using instance UUID: %s (encoded: %s)", instance_uuid, encoded_uuid)

            # Step 3 — fetch transcript metadata
            transcript_response = await self.client.get_meeting_transcript(meetingId=encoded_uuid)

            if isinstance(transcript_response, ZoomResponse) and not transcript_response.success:
                return False, json.dumps({"error": transcript_response.error or transcript_response.message})

            transcript_data = transcript_response.data if isinstance(transcript_response, ZoomResponse) else transcript_response
            download_url = transcript_data.get("download_url") if isinstance(transcript_data, dict) else None

            if not download_url:
                return False, json.dumps({
                    "error": "No download_url in transcript response.",
                    "raw": transcript_data,
                })

            # Step 4 — download and parse VTT
            vtt_text = await self._fetch_text(download_url)
            if vtt_text is None:
                return False, json.dumps({"error": f"Failed to download transcript from {download_url}"})

            plain_text = self._parse_vtt(vtt_text)

            return True, json.dumps({
                "message": "Transcript fetched successfully",
                "meeting_id": meeting_id,
                "instance_uuid": instance_uuid,
                "transcript": plain_text,
            })

        except Exception as e:
            logger.error("Error getting meeting transcript: %s", e)
            return False, json.dumps({"error": str(e)})

    async def _fetch_text(self, url: str) -> Optional[str]:
        """Download a plain text/VTT file via the Zoom HTTP client."""
        try:
            from app.sources.client.http.http_request import HTTPRequest as _HTTPRequest
            request = _HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.http.execute(request)  # type: ignore[reportUnknownMemberType]
            return response.text() if hasattr(response, "text") else str(response)
        except Exception as e:
            logger.error("Error downloading transcript VTT: %s", e)
            return None
 
    @staticmethod
    def _parse_vtt(vtt: str) -> str:
        """Convert WebVTT content to clean plain text, removing headers and timecodes."""
        import re
        lines = vtt.splitlines()
        text_lines = []
        for line in lines:
            line = line.strip()
            # Skip WEBVTT header, NOTE blocks, empty lines, and timecode lines
            if not line:
                continue
            if line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            if re.match(r"^\d+$", line):          # cue index numbers
                continue
            if re.match(r"[\d:.]+ --> [\d:.]+", line):  # timecodes
                continue
            text_lines.append(line)
        return " ".join(text_lines)

    @tool(
        app_name="zoom",
        tool_name="delete_meeting_transcript",
        description="Delete the transcript for a recorded Zoom meeting.",
        llm_description="Permanently deletes the transcript for a meeting recording. Cannot be undone.",
        args_schema=DeleteMeetingTranscriptInput,
        returns="JSON confirming transcript deletion",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User explicitly wants to delete a meeting transcript",
        ],
        when_not_to_use=[
            "User wants to read the transcript (use get_meeting_transcript)",
        ],
        typical_queries=["Delete transcript for meeting 123", "Remove Zoom call transcript"],
    )
    async def delete_meeting_transcript(self, meeting_id: str) -> Tuple[bool, str]:
        """Delete the transcript for a recorded meeting."""
        try:
            logger.info("zoom.delete_meeting_transcript called for meeting_id=%s", meeting_id)
            response = await self.client.delete_meeting_transcript(meetingId=meeting_id)
            return self._handle_response(response, "Meeting transcript deleted successfully")
        except Exception as e:
            logger.error("Error deleting meeting transcript: %s", e)
            return False, json.dumps({"error": str(e)})