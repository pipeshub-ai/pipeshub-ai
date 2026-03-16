import json
import logging
from typing import Any, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

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
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.zoom.zoom import ZoomClient
from app.sources.external.zoom.zoom import ZoomDataSource

logger = logging.getLogger(__name__)


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
        description="Meeting type: 'scheduled', 'live', 'upcoming', or 'all'",
        alias="type",
    )
    page_size: Optional[int] = Field(default=30, ge=1, le=300, description="Number of meetings per page (default 30, max 300).")
    next_page_token: Optional[str] = Field(default=None, description="Pagination token from previous response.")
    from_: Optional[str] = Field(default=None, description="Start date (YYYY-MM-DD) to list meetings from.", alias="from")
    to: Optional[str] = Field(default=None, description="End date (YYYY-MM-DD) to list meetings to.")


class GetMeetingInput(BaseModel):
    meeting_id: str = Field(description="Zoom meeting ID.")
    occurrence_id: Optional[str] = Field(default=None, description="Meeting occurrence ID for recurring meetings.")
    show_previous_occurrences: Optional[bool] = Field(default=None, description="Include previous occurrences of recurring meeting.")


class CreateMeetingInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )
    topic: str = Field(description="Meeting topic/title.")
    agenda: Optional[str] = Field(default=None, description="Meeting agenda/description.")
    start_time: Optional[str] = Field(default=None, description="Start time in ISO 8601 format (e.g. 2025-03-20T14:00:00Z).")
    duration: Optional[int] = Field(default=60, ge=1, description="Duration in minutes (default 60).")
    timezone: Optional[str] = Field(default=None, description="Timezone for the meeting (e.g. America/New_York).")
    type_: Optional[int] = Field(
        default=2,
        description="Meeting type: 1=instant, 2=scheduled, 3=recurring no fixed time, 8=recurring fixed time.",
        alias="type",
    )
    password: Optional[str] = Field(default=None, description="Meeting passcode.")
    settings: Optional[dict] = Field(default=None, description="Additional meeting settings (e.g. join_before_host, waiting_room).")


class UpdateMeetingInput(BaseModel):
    meeting_id: str = Field(description="Zoom meeting ID to update.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID for recurring meetings.")
    topic: Optional[str] = Field(default=None, description="New meeting topic.")
    agenda: Optional[str] = Field(default=None, description="New agenda.")
    start_time: Optional[str] = Field(default=None, description="New start time (ISO 8601).")
    duration: Optional[int] = Field(default=None, ge=1, description="New duration in minutes.")
    timezone: Optional[str] = Field(default=None, description="New timezone.")
    password: Optional[str] = Field(default=None, description="New passcode.")
    settings: Optional[dict] = Field(default=None, description="Updated settings.")


class DeleteMeetingInput(BaseModel):
    meeting_id: str = Field(description="Zoom meeting ID to delete.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID for recurring meetings; omit to delete all.")
    schedule_for_reminder: Optional[bool] = Field(default=None, description="Send cancellation email to registrants.")
    cancel_meeting_reminder: Optional[bool] = Field(default=None, description="Send cancel reminder to registrants.")


class ListUpcomingMeetingsInput(BaseModel):
    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )


class GetMeetingInvitationInput(BaseModel):
    meeting_id: str = Field(description="Zoom meeting ID.")


class ListRecordingsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )
    page_size: Optional[int] = Field(default=30, ge=1, le=300, description="Results per page (max 300).")
    next_page_token: Optional[str] = Field(default=None, description="Pagination token from previous response.")
    from_: Optional[str] = Field(default=None, alias="from", description="Start date (YYYY-MM-DD).")
    to: Optional[str] = Field(default=None, description="End date (YYYY-MM-DD).")
    meeting_id: Optional[str] = Field(default=None, description="Filter recordings by a specific meeting ID.")
    trash: Optional[bool] = Field(default=False, description="List recordings in the trash when True.")


class GetMeetingRecordingsInput(BaseModel):
    meeting_id: str = Field(
        description=(
            "Meeting ID or UUID. "
            "Double-encode UUIDs that start with '/' or contain '//'."
        )
    )
    include_fields: Optional[str] = Field(
        default=None,
        description="Comma-separated extra fields to include, e.g. 'download_access_token'.",
    )


class GetMeetingTranscriptInput(BaseModel):
    meeting_id: str = Field(
        description=(
            "Meeting ID or UUID of the recorded meeting. "
            "Cloud recording with audio transcription must have been enabled."
        )
    )


class DeleteMeetingTranscriptInput(BaseModel):
    meeting_id: str = Field(description="Meeting ID or UUID whose transcript should be deleted.")


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
                    "user:read:email",
                    "meeting:read:list_upcoming_meetings"
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
        self.client = ZoomDataSource(client.get_client())

    def _handle_response(self, response: Any, success_message: str) -> Tuple[bool, str]:
        """Normalize datasource response (HTTPResponse or dict) to (success, json_string)."""
        if isinstance(response, HTTPResponse):
            status = getattr(response, "status", None) or getattr(response, "status_code", None)
            if status is not None and 200 <= status < 300:
                try:
                    data = response.json() if hasattr(response, "json") else {}
                    return True, json.dumps({"message": success_message, "data": data})
                except Exception:
                    text = getattr(response, "text", lambda: "")()
                    return True, json.dumps({"message": success_message, "data": text})
            try:
                err_body = response.json() if hasattr(response, "json") else {}
            except Exception:
                err_body = {"error": getattr(response, "text", lambda: "Unknown error")()}
            return False, json.dumps({"error": err_body})
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
        llm_description="Returns the profile of the Zoom user (name, email, timezone, account type). Use user_id='me' for the token owner.",
        args_schema=GetMyProfileInput,
        returns="JSON with user profile details",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to know their Zoom account details",
            "User asks for their email or timezone in Zoom",
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
        llm_description="Lists meetings for a user. Use user_id='me' for the authenticated user. Optional: type (scheduled/live/upcoming/all), page_size, next_page_token, from, to dates.",
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
        description="Get details of a Zoom meeting.",
        llm_description="Returns a single meeting by meeting_id. Optional: occurrence_id for recurring, show_previous_occurrences.",
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
            "User wants to create or update a meeting",
        ],
        typical_queries=["Get Zoom meeting 123", "Show meeting details", "What is the link for meeting X?"],
    )
    async def get_meeting(
        self,
        meeting_id: str,
        occurrence_id: Optional[str] = None,
        show_previous_occurrences: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Get a Zoom meeting by ID."""
        try:
            logger.info("zoom.get_meeting called with meeting_id=%s", meeting_id)
            response = await self.client.meeting(
                meetingId=meeting_id,
                occurrence_id=occurrence_id,
                show_previous_occurrences=show_previous_occurrences,
            )
            return self._handle_response(response, "Meeting fetched successfully")
        except Exception as e:
            logger.error("Error getting meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="create_meeting",
        description="Create a new Zoom meeting.",
        llm_description="Creates a meeting for the user. user_id='me' for authenticated user. Required: topic. Optional: agenda, start_time, duration, timezone, type, password, settings.",
        args_schema=CreateMeetingInput,
        returns="JSON with created meeting details including join URL",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to create a Zoom meeting",
            "User asks to schedule a meeting",
        ],
        when_not_to_use=[
            "User wants to list or get a meeting (use list_meetings or get_meeting)",
            "User wants to update a meeting (use update_meeting)",
        ],
        typical_queries=["Create a Zoom meeting", "Schedule a meeting", "Set up a Zoom call"],
    )
    async def create_meeting(
        self,
        user_id: str,
        topic: str,
        agenda: Optional[str] = None,
        start_time: Optional[str] = None,
        duration: Optional[int] = 60,
        timezone: Optional[str] = None,
        type_: Optional[int] = 2,
        password: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> Tuple[bool, str]:
        """Create a Zoom meeting."""
        try:
            body = {"topic": topic}
            if agenda is not None:
                body["agenda"] = agenda
            if start_time is not None:
                body["start_time"] = start_time
            if duration is not None:
                body["duration"] = duration
            if timezone is not None:
                body["timezone"] = timezone
            if type_ is not None:
                body["type"] = type_
            if password is not None:
                body["password"] = password
            if settings is not None:
                body["settings"] = settings
            logger.info("zoom.create_meeting called for user_id=%s topic=%s", user_id, topic)
            base_url = getattr(self.client, "_base_url", "https://api.zoom.us/v2")
            endpoint = f"{base_url}/users/{user_id}/meetings"
            response = await self.client._rest.request(
                "POST",
                endpoint,
                params={"userId": user_id},
                body=body,
                timeout=None,
            )
            return self._handle_response(response, "Meeting created successfully")
        except Exception as e:
            logger.error("Error creating meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="update_meeting",
        description="Update a Zoom meeting.",
        llm_description="Updates an existing meeting. Pass meeting_id and only the fields to change (topic, agenda, start_time, duration, timezone, password, settings).",
        args_schema=UpdateMeetingInput,
        returns="JSON with updated meeting details",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to change meeting topic, time, or settings",
        ],
        when_not_to_use=[
            "User wants to create or delete a meeting",
        ],
        typical_queries=["Update Zoom meeting", "Change meeting time", "Reschedule meeting"],
    )
    async def update_meeting(
        self,
        meeting_id: str,
        occurrence_id: Optional[str] = None,
        topic: Optional[str] = None,
        agenda: Optional[str] = None,
        start_time: Optional[str] = None,
        duration: Optional[int] = None,
        timezone: Optional[str] = None,
        password: Optional[str] = None,
        settings: Optional[dict] = None,
    ) -> Tuple[bool, str]:
        """Update a Zoom meeting."""
        try:
            body = {}
            if topic is not None:
                body["topic"] = topic
            if agenda is not None:
                body["agenda"] = agenda
            if start_time is not None:
                body["start_time"] = start_time
            if duration is not None:
                body["duration"] = duration
            if timezone is not None:
                body["timezone"] = timezone
            if password is not None:
                body["password"] = password
            if settings is not None:
                body["settings"] = settings
            if not body:
                return False, json.dumps({"error": "At least one field to update is required"})
            logger.info("zoom.update_meeting called for meeting_id=%s", meeting_id)
            base_url = getattr(self.client, "_base_url", "https://api.zoom.us/v2")
            endpoint = f"{base_url}/meetings/{meeting_id}"
            params = {"meetingId": meeting_id}
            if occurrence_id is not None:
                params["occurrence_id"] = occurrence_id
            response = await self.client._rest.request(
                "PATCH",
                endpoint,
                params=params,
                body=body,
                timeout=None,
            )
            return self._handle_response(response, "Meeting updated successfully")
        except Exception as e:
            logger.error("Error updating meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="delete_meeting",
        description="Delete a Zoom meeting.",
        llm_description="Deletes a meeting by meeting_id. For recurring, pass occurrence_id to delete one occurrence; omit to delete all.",
        args_schema=DeleteMeetingInput,
        returns="JSON confirming deletion",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to cancel or delete a Zoom meeting",
        ],
        when_not_to_use=[
            "User wants to list or get a meeting",
        ],
        typical_queries=["Cancel Zoom meeting", "Delete meeting", "Remove meeting"],
    )
    async def delete_meeting(
        self,
        meeting_id: str,
        occurrence_id: Optional[str] = None,
        schedule_for_reminder: Optional[bool] = None,
        cancel_meeting_reminder: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """Delete a Zoom meeting."""
        try:
            logger.info("zoom.delete_meeting called for meeting_id=%s", meeting_id)
            response = await self.client.meeting_delete(
                meetingId=meeting_id,
                occurrence_id=occurrence_id,
                schedule_for_reminder=schedule_for_reminder,
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
            "User wants to see upcoming Zoom meetings only",
        ],
        when_not_to_use=[
            "User wants all meetings (use list_meetings with type)",
            "User wants a single meeting (use get_meeting)",
        ],
        typical_queries=["My upcoming Zoom meetings", "What's my next Zoom call?"],
    )
    async def list_upcoming_meetings(self, user_id: str) -> Tuple[bool, str]:
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
        tool_name="get_meeting_invitation",
        description="Get the invitation text for a Zoom meeting (join link and details).",
        llm_description="Returns the invitation body for a meeting (join URL, dial-in, etc.). Use for sharing or displaying invite.",
        args_schema=GetMeetingInvitationInput,
        returns="JSON with meeting invitation text",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants the meeting invite text or join link to share",
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

    @tool(
        app_name="zoom",
        tool_name="list_recordings",
        description="List all cloud recordings for a Zoom user.",
        llm_description="Returns cloud recordings for user_id filtered by optional date range. Use 'me' for the authenticated user.",
        args_schema=ListRecordingsInput,
        returns="JSON with list of recordings including meeting info and file download URLs",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to browse or search their past Zoom recordings",
            "User asks for all recorded meetings in a date range",
        ],
        when_not_to_use=[
            "User wants files for a specific meeting (use get_meeting_recordings)",
            "User wants a transcript (use get_meeting_transcript)",
        ],
        typical_queries=["Show my Zoom recordings", "List recorded meetings", "Recordings from last week"],
    )
    async def list_recordings(
        self,
        user_id: str,
        page_size: Optional[int] = 30,
        next_page_token: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        meeting_id: Optional[str] = None,
        trash: Optional[bool] = False,
    ) -> Tuple[bool, str]:
        """List all cloud recordings for a user."""
        try:
            logger.info("zoom.list_recordings called for user_id=%s", user_id)
            response = await self.client.recordings_list(
                userId=user_id,
                page_size=page_size,
                next_page_token=next_page_token,
                from_=from_,
                to=to,
                meeting_id=meeting_id,
                trash=trash,
            )
            return self._handle_response(response, "Recordings listed successfully")
        except Exception as e:
            logger.error("Error listing recordings: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="get_meeting_recordings",
        description="Get all recording files for a specific Zoom meeting.",
        llm_description=(
            "Returns all recording files for a meeting (video, audio, chat, VTT transcript). "
            "Each file has a download_url. The transcript VTT file has recording_type='audio_transcript'. "
            "Use meeting_id (or UUID, double-encoded if it starts with '/')."
        ),
        args_schema=GetMeetingRecordingsInput,
        returns="JSON with recording files list including download URLs and file types",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to download or view recordings from a specific meeting",
            "User asks for the recording link of a particular call",
            "User wants to find the transcript VTT file for a meeting",
        ],
        when_not_to_use=[
            "User wants recordings across all meetings (use list_recordings)",
            "User wants only the transcript text (use get_meeting_transcript)",
        ],
        typical_queries=["Get recording for meeting 123", "Download Zoom call recording", "Recording link for yesterday's call"],
    )
    async def get_meeting_recordings(
        self,
        meeting_id: str,
        include_fields: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Get all recording files for a specific meeting."""
        try:
            logger.info("zoom.get_meeting_recordings called for meeting_id=%s", meeting_id)
            response = await self.client.recording_get(
                meetingId=meeting_id,
                include_fields=include_fields,
            )
            return self._handle_response(response, "Meeting recordings fetched successfully")
        except Exception as e:
            logger.error("Error getting meeting recordings: %s", e)
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Transcript tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="get_meeting_transcript",
        description="Get the transcript for a recorded Zoom meeting.",
        llm_description=(
            "Returns the transcript metadata and VTT download URL for a recorded meeting. "
            "Requires cloud recording with audio transcription enabled before the meeting. "
            "Required scope: recording:read."
        ),
        args_schema=GetMeetingTranscriptInput,
        returns="JSON with transcript metadata and VTT file download URL",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants the transcript of a Zoom call",
            "User asks what was discussed or said in a recorded meeting",
            "User wants to download the VTT transcript file",
        ],
        when_not_to_use=[
            "User wants the video or audio recording (use get_meeting_recordings)",
            "User wants to list all recordings (use list_recordings)",
        ],
        typical_queries=["Get transcript for meeting 123", "What was said in yesterday's call?", "Download Zoom call transcript"],
    )
    async def get_meeting_transcript(self, meeting_id: str) -> Tuple[bool, str]:
        """Get the VTT transcript for a recorded meeting."""
        try:
            logger.info("zoom.get_meeting_transcript called for meeting_id=%s", meeting_id)
            response = await self.client.get_meeting_transcript(meetingId=meeting_id)
            return self._handle_response(response, "Meeting transcript fetched successfully")
        except Exception as e:
            logger.error("Error getting meeting transcript: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="delete_meeting_transcript",
        description="Delete the transcript for a recorded Zoom meeting.",
        llm_description="Permanently deletes the transcript for a meeting or webinar recording. This action cannot be undone.",
        args_schema=DeleteMeetingTranscriptInput,
        returns="JSON confirming transcript deletion",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User explicitly wants to delete a meeting transcript",
        ],
        when_not_to_use=[
            "User wants to read or download the transcript (use get_meeting_transcript)",
            "User wants to delete the recording itself",
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