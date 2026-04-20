import asyncio
import json
import logging
import re
from typing import Annotated, Literal, Optional, Tuple
from urllib.parse import quote
from pydantic import model_validator
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
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
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.zoom.zoom import ZoomClient, ZoomResponse
from app.sources.external.zoom.zoom import ZoomDataSource

logger = logging.getLogger(__name__)


def _coerce_meeting_id(raw_meeting_id: str | int) -> str:
    """Accept meeting_id as int or str (e.g. from tool result); coerce to str."""
    return str(raw_meeting_id)


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
    from_: str = Field(
        min_length=1,
        description="Start date (YYYY-MM-DD). Required to bound the meeting range.",
    )
    to_: str = Field(
        min_length=1,
        description="End date (YYYY-MM-DD). Required to bound the meeting range.",
    )
    top: Optional[int] = Field(default=10, description="Maximum number of meetings to return.")
    search: Optional[str] = Field(default=None, description="Search keyword to filter meetings by topic/name.")


class GetMeetingInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID.")
    occurrence_id: Optional[str] = Field(default=None, description="Occurrence ID for recurring meetings. (optional)")


class RecurrenceInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: int = Field(
        alias="type", 
        description="Recurrence type: 1=Daily, 2=Weekly, 3=Monthly."
    )
    repeat_interval: Optional[int] = Field(default=None, description="Interval for when the meeting should recur.")
    end_date_time: Optional[str] = Field(default=None,description="End date/time in UTC ISO format. MUST end with 'Z' (e.g., '2026-03-31T19:00:00Z'). Cannot be used with end_times.")
    end_times: Optional[int] = Field(default=None, description="Number of times to repeat (max 60). Cannot be used with end_date_time.")
    monthly_day: Optional[int] = Field(default=None, description="Day of month for monthly recurrence (1-31).")
    monthly_week: Optional[int] = Field(default=None, description="Week of month for monthly recurrence (-1 for last week, 1 for first week).")
    monthly_week_day: Optional[int] = Field(default=None, description="Day of week for monthly recurrence (1=Sun, 2=Mon, etc.). Used with monthly_week.")
    weekly_days: Optional[str] = Field(default=None, description="Days of week for weekly recurrence (e.g., '1,2' for Sun/Mon).")


class CreateMeetingInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )
    topic: str = Field(description="Meeting topic/title.")
    start_time: Optional[str] = Field(default=None, description="Start time in ISO 8601 format (e.g. 2025-03-20T14:00:00Z).")
    duration: Optional[int] = Field(default=60, ge=1, description="Duration in minutes (default 60).")
    timezone: Optional[str] = Field(default=None, description="Timezone (e.g. Asia/Kolkata). Infer from system prompt, dont ask user")
    agenda: Optional[str] = Field(default=None, description="Meeting agenda/description.")
    type_: Optional[int] = Field(
        default=2,
        alias="type",
        description="Meeting type: 1=instant, 2=scheduled, 3=recurring (no fixed time), 8=recurring (fixed time).",
    )
    invitees: Optional[list[str]] = Field(default=None, description="List of email addresses to invite to the meeting.")
    recurrence: Optional[RecurrenceInput] = Field(default=None, description="Recurrence configuration for type 8 meetings.")

    @model_validator(mode="before")
    @classmethod
    def coerce_empty_recurrence(cls, values: dict) -> dict:
        if isinstance(values.get("recurrence"), dict) and not values["recurrence"]:
            values["recurrence"] = None
        return values


class UpdateMeetingInput(BaseModel):
    """Only include fields the user explicitly asked to change."""
    meeting_id: MeetingId = Field(description="Zoom meeting ID to update.")
    topic: Optional[str] = Field(default=None, description="New meeting topic. Only set if user asked to rename it.")
    start_time: Optional[str] = Field(default=None, description="New start time in ISO 8601 (e.g. 2026-03-16T17:00:00). Only set if user asked to reschedule.")
    duration: Optional[int] = Field(default=None, description="New duration in minutes. Only set if user mentioned it.")
    timezone: Optional[str] = Field(default=None, description="Timezone for start_time. Infer from context; default Asia/Kolkata if user is in India.")
    agenda: Optional[str] = Field(default=None, description="New agenda. Only set if user provided one.")
    occurrence_id: Optional[str] = Field(default=None, description="To update a single occurrence of a recurring meeting, provide the occurrence_id. To update the entire series, leave this blank.")
    invitees: Optional[list[str]] = Field(default=None, description="List of email addresses to invite to the meeting.")
    recurrence: Optional[RecurrenceInput] = Field(default=None, description="New recurrence configuration. Only set to change the recurrence pattern of the entire series.")

    @model_validator(mode="before")
    @classmethod
    def coerce_empty_recurrence(cls, values: dict) -> dict:
        if isinstance(values.get("recurrence"), dict) and not values["recurrence"]:
            values["recurrence"] = None
        return values


class DeleteMeetingInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID to delete.")
    occurrence_id: Optional[str] = Field(default=None, description="To delete a single occurrence of a recurring meeting, provide the occurrence_id. To delete the entire series, leave this blank.")
    cancel_meeting_reminder: Optional[bool] = Field(default=None, description="Send cancellation email to registrants.")


class ListUpcomingMeetingsInput(BaseModel):
    user_id: str = Field(
        default="me",
        description="Zoom user ID or email. Use 'me' for the authenticated user.",
    )


class GetMeetingInvitationInput(BaseModel):
    meeting_id: MeetingId = Field(description="Zoom meeting ID.")


class GetMeetingTranscriptInput(BaseModel):
    meeting_id: MeetingId = Field(description="Meeting ID or UUID of the recorded meeting.")


class ListContactsInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)  # add this
    type_: Optional[Literal["company", "external", "personal"]] = Field(default=None, alias="type", description="Filter contacts by type: 'company' (same org), 'external' (outside contacts), or 'personal' (your contacts). Omit to return all.")
    top: Optional[int] = Field(default=10, description="Maximum number of contacts to return.")


class GetContactInput(BaseModel):
    identifier: str = Field(description="Contact's user ID, email address, or member ID.")


class ListFolderChildrenInput(BaseModel):
    folder_id: str = Field(
        default="root",
        description=(
            "The file/folder ID whose children to list. "
            "Use 'root' (default) to list the contents of the authenticated user's root 'My Docs' folder. "
            "Pass a specific folder ID to drill into a sub-folder."
        ),
    )
    page_size: Optional[int] = Field(default=50, ge=1, le=50, description="Results per page (max 50).")


class ListRecurringMeetingsEndingInput(BaseModel):
    from_: str = Field(alias="from_", description="Range start in ISO 8601 UTC (e.g. '2026-03-01T00:00:00Z').")
    to_: str = Field(alias="to_", description="Range end in ISO 8601 UTC (e.g. '2026-03-31T23:59:59Z').")
    top: int = Field(default=10, ge=1, le=50, description="Max results to return.")


_STRIP_FIELDS = {"global_dial_in_numbers", "global_dial_in_countries", "dial_in_numbers"}

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
                    "meeting:write:meeting",
                    "meeting:delete:meeting",
                    "meeting:update:meeting",
                    "meeting:read:invitation",
                    "meeting:read:list_meetings",
                    "meeting:read:list_upcoming_meetings",
                    "meeting:read:list_past_instances",
                    "user:read:user",
                    "user:read:email",
                    "docs:read:list_children",
                    "contact:read:list_contacts",
                    "cloud_recording:read:recording",
                    "cloud_recording:read:meeting_transcript",
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
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/zoom.svg")
        .add_documentation_link(DocumentationLink(
            "Zoom OAuth App",
            "https://developers.zoom.us/docs/integrations/create/",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/zoom/zoom",
            "pipeshub",
        )))\
    .build_decorator()
class Zoom:
    """Zoom tools exposed to agents using ZoomDataSource."""

    def __init__(self, client: ZoomClient) -> None:
        self.client = ZoomDataSource(client)

    # ------------------------------------------------------------------
    # Helper functions
    # ------------------------------------------------------------------

    def _clean_response_data(self, data: object) -> object:
        """Strip noisy dial-in fields from any response data."""
        if isinstance(data, dict):
            cleaned = {
                key: val for key, val in data.items() if key not in _STRIP_FIELDS
            }
            if "settings" in cleaned and isinstance(cleaned["settings"], dict):
                cleaned["settings"] = {
                    key: val
                    for key, val in cleaned["settings"].items()
                    if key not in _STRIP_FIELDS
                }
            if "meetings" in cleaned and isinstance(cleaned["meetings"], list):
                cleaned["meetings"] = [
                    self._clean_response_data(meeting)
                    for meeting in cleaned["meetings"]
                ]
            return cleaned
        return data

    def _handle_response(
        self,
        response: ZoomResponse | dict[str, object],
        success_message: str,
    ) -> tuple[bool, str]:
        """Normalize ZoomResponse to (success, json_string)."""
        if isinstance(response, ZoomResponse):
            if response.success:
                return True, json.dumps({"message": success_message, "data": self._clean_response_data(response.data)})
            error = response.error or response.message or "Unknown error"
            return False, json.dumps({"error": error})
        # Fallback for raw dict responses
        if isinstance(response, dict):
            if response.get("code") or response.get("error"):
                return False, json.dumps(response)
            return True, json.dumps({"message": success_message, "data": self._clean_response_data(response)})

    async def _get_meeting_detail_dict(self, meeting_id: str) -> dict | None:
        """GET a single meeting; returns unwrapped payload or None on failure."""
        detail_response = await self.client.meeting(meetingId=meeting_id)
        success, detail_cleaned = self._handle_response(detail_response, "ok")
        if not success:
            return None
        detail = json.loads(detail_cleaned)
        if not isinstance(detail, dict):
            return None
        payload = detail.get("data", detail)
        return payload if isinstance(payload, dict) else None

    async def _meeting_details_by_id(
        self,
        meeting_ids: list[str],
    ) -> dict[str, dict]:
        unique_ids = list(dict.fromkeys(mid for mid in meeting_ids if mid))
        if not unique_ids:
            return {}

        async def fetch_one(mid: str) -> tuple[str, dict | None]:
            payload = await self._get_meeting_detail_dict(mid)
            return mid, payload

        out: dict[str, dict] = {}
        for i in range(0, len(unique_ids), 10):
            chunk = unique_ids[i:i + 10]
            pairs = await asyncio.gather(*[fetch_one(mid) for mid in chunk], return_exceptions=True)
            for item in pairs:
                if isinstance(item, Exception):
                    logger.warning("Parallel meeting detail fetch failed: %s", item)
                    continue
                mid, payload = item
                if payload is not None:
                    out[mid] = payload
        return out
    
    async def _fetch_text(self, url: str) -> str | None:
        """Download a plain text/VTT file via the Zoom HTTP client."""
        try:
            request = HTTPRequest(
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
        """Convert WebVTT to readable text: each cue is one line ``[start - end] spoken text``."""
        timecode_re = re.compile(r"^([\d:.]+)\s*-->\s*([\d:.]+)")
        lines = vtt.splitlines()
        segments: list[str] = []
        current_start: str | None = None
        current_end: str | None = None
        current_text: list[str] = []

        def flush() -> None:
            nonlocal current_start, current_end, current_text
            if current_start and current_text:
                stamp = f"[{current_start} - {current_end}]" if current_end else f"[{current_start}]"
                segments.append(f"{stamp} {' '.join(current_text)}")
            current_start = None
            current_end = None
            current_text = []

        for raw in lines:
            line = raw.strip()
            if not line:
                flush()
                continue
            if line.startswith("WEBVTT") or line.startswith("NOTE"):
                continue
            timecode_match = timecode_re.match(line)
            if timecode_match:
                flush()
                current_start = timecode_match.group(1)
                current_end = timecode_match.group(2)
                continue
            if re.match(r"^\d+$", line):
                continue
            if current_start is not None:
                current_text.append(line)

        flush()
        return "\n".join(segments)
    
    def _ensure_aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _in_range(self, start_time: str, from_dt: datetime, to_dt: datetime) -> bool:
        try:
            start_dt = self._ensure_aware(datetime.fromisoformat(start_time.replace("Z", "+00:00")))
            return from_dt <= start_dt <= to_dt
        except ValueError:
            return False

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
        llm_description=(
            "Lists meetings for a user within a date range. Use user_id='me' for the authenticated user. "
            "Optional: search keyword to filter by topic/name."
        ),
        args_schema=ListMeetingsInput,
        returns="JSON with list of meetings and pagination token",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to see their Zoom meetings",
            "User asks for scheduled or upcoming meetings",
            "User wants to find a meeting by name within a date range",
        ],
        when_not_to_use=[
            "User wants a single meeting's details (use get_meeting)",
            "User wants to create a meeting (use create_meeting)",
        ],
        typical_queries=["List my Zoom meetings", "Show my scheduled meetings", "Find my standup meetings this week"],
    )
    async def list_meetings(
        self,
        from_: str,
        to_: str,
        top: int | None = 200,
        search: str | None = None,
    ) -> Tuple[bool, str]:
        """Return all meeting instances (including recurring occurrences) within [from_, to_], optionally filtered by topic."""
        try:
            top = min(top or 200, 200)

            from_dt = self._ensure_aware(datetime.fromisoformat(from_.replace("Z", "+00:00")))
            to_dt   = self._ensure_aware(datetime.fromisoformat(to_.replace("Z", "+00:00"))) + timedelta(days=1)

            now = datetime.now(timezone.utc)
            search_lower = search.lower() if search else None

            # Step 1 — fetch only required meeting types
            meeting_types = []
            if from_dt < now:
                meeting_types.append("previous_meetings")
            if to_dt >= now:
                meeting_types.append("upcoming")
            if not meeting_types:
                meeting_types = ["upcoming"]

            all_parents: list[dict] = []

            for meeting_type in meeting_types:
                next_page_token = None

                while True:
                    response = await self.client.meetings(
                        userId="me",
                        type_=meeting_type,
                        page_size=100,
                        next_page_token=next_page_token,
                    )

                    success, cleaned = self._handle_response(response, "ok")
                    if not success:
                        break

                    data = json.loads(cleaned)
                    payload = data.get("data", data)

                    meetings = payload.get("meetings", [])
                    all_parents.extend(meetings)

                    next_page_token = payload.get("next_page_token")
                    if not next_page_token:
                        break

            # Step 2 — deduplicate + early topic filter
            seen: set[str] = set()
            parents: list[dict] = []

            for parent in all_parents:
                mid = str(parent.get("id", ""))
                if not mid or mid in seen:
                    continue

                seen.add(mid)

                if search_lower and search_lower not in str(parent.get("topic", "")).lower():
                    continue

                parents.append(parent)

            results: list[dict] = []
            recurring_ids: list[str] = []

            # Step 3 — process non-recurring + prune recurring early
            for meeting in parents:
                mtype = meeting.get("type")

                # Non-recurring
                if mtype == 2:
                    start = meeting.get("start_time")
                    if start and self._in_range(start, from_dt, to_dt):
                        results.append({
                            "meeting_id": meeting.get("id"),
                            "topic": meeting.get("topic"),
                            "start_time": start,
                            "duration": meeting.get("duration"),
                            "join_url": meeting.get("join_url"),
                            "recurring": False,
                        })

                        if len(results) >= top:
                            break

                # Recurring
                elif mtype in (3, 8):
                    recurrence = meeting.get("recurrence") or {}
                    end_date_time = recurrence.get("end_date_time")

                    # Skip expired recurring meetings
                    if end_date_time:
                        try:
                            end_dt = self._ensure_aware(datetime.fromisoformat(end_date_time.replace("Z", "+00:00")))
                            if end_dt < from_dt:
                                continue
                        except Exception:
                            pass

                    recurring_ids.append(str(meeting.get("id")))

            # Step 4 — fetch only required recurring details
            details_by_id = await self._meeting_details_by_id(recurring_ids) if recurring_ids else {}

            # Step 5 — expand occurrences (with early exit)
            for meeting in parents:
                if len(results) >= top:
                    break

                if meeting.get("type") not in (3, 8):
                    continue

                mid = str(meeting.get("id"))
                detail = details_by_id.get(mid)

                if not detail:
                    continue

                for occ in detail.get("occurrences") or []:
                    if len(results) >= top:
                        break

                    occ_start = occ.get("start_time")

                    if occ_start and self._in_range(occ_start, from_dt, to_dt):
                        results.append({
                            "meeting_id": mid,
                            "occurrence_id": occ.get("occurrence_id"),
                            "topic": meeting.get("topic"),
                            "start_time": occ_start,
                            "duration": occ.get("duration"),
                            "join_url": detail.get("join_url"),
                            "recurring": True,
                            "status": occ.get("status"),
                        })

            # 🚀 Step 6 — sort + trim
            results.sort(key=lambda row: row["start_time"])
            trimmed = results[:top]

            return True, json.dumps({
                "meetings": trimmed,
                "count": len(trimmed),
                "from": from_,
                "to": to_,
                **({"search": search} if search else {}),
            })

        except Exception as e:
            logger.error("Error listing meetings in range: %s", e)
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
        occurrence_id: str | None = None,
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
        description="Create a new Zoom meeting (including recurring meetings).",
        args_schema=CreateMeetingInput,
        returns="JSON with created meeting details including join URL",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to create or schedule a Zoom meeting",
            "User wants to create a daily, weekly, or monthly recurring meeting",
        ],
        when_not_to_use=[
            "User wants to update an existing meeting (use update_meeting)",
        ],
        typical_queries=["Create a Zoom meeting", "Schedule a daily meeting", "Set up a weekly recurring Zoom call"],
        llm_description=(
            "Creates a scheduled or recurring meeting. Required: topic. Optional: start_time (ISO 8601), duration (minutes), timezone, agenda. "
            "For recurring meetings, set type=8 and provide a recurrence object. "
            "IMPORTANT: If the meeting is NOT recurring, completely omit the recurrence field. Do not pass an empty object. "
            "If providing recurrence.end_date_time, you MUST format it as UTC and append 'Z' (e.g., '2026-03-31T23:59:00Z'). "
            "Infer timezone from context (e.g. if user is in India use Asia/Kolkata). "
            "Do NOT ask for password or settings unless user explicitly mentions them."
        ),
    )
    async def create_meeting(
        self,
        user_id: str,
        topic: str,
        start_time: str | None = None,
        duration: int | None = 60,
        timezone: str | None = None,
        agenda: str | None = None,
        type_: int | None = 2,
        invitees: list[str] | None = None,
        recurrence: RecurrenceInput | None = None,
    ) -> Tuple[bool, str]:
        """Create a Zoom meeting."""
        try:
            body: dict[str, object] = {"topic": topic}
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
            if recurrence is not None:
                # Forcefully append 'Z' to end_date_time if the AI forgot it
                if getattr(recurrence, "end_date_time", None) and not recurrence.end_date_time.endswith("Z"):
                    recurrence.end_date_time += "Z"
                
                # Dump the Pydantic model to a dict, applying aliases (e.g., type_ -> type)
                body["recurrence"] = recurrence.model_dump(by_alias=True, exclude_none=True)
            if invitees:
                body["settings"] = {
                    "meeting_invitees": [{"email": email} for email in invitees]
                }
            logger.info("zoom.create_meeting called for user_id=%s topic=%s", user_id, topic)
            response = await self.client.meeting_create(userId=user_id, body=body)
            return self._handle_response(response, "Meeting created successfully")
        except Exception as e:
            logger.error("Error creating meeting: %s", e)
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zoom",
        tool_name="update_meeting",
        description="Update a Zoom meeting (entire series or specific occurrence).",
        llm_description=(
            "Updates only the fields the user explicitly asked to change. "
            "For recurring meetings, omit occurrence_id to update the entire series, or provide occurrence_id to update a single instance. "
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
            "User wants to update a single occurrence or an entire recurring meeting series",
        ],
        when_not_to_use=[
            "User wants to create a new meeting (use create_meeting)",
            "User wants to delete a meeting (use delete_meeting)",
        ],
        typical_queries=["Reschedule meeting to 5pm", "Update all occurrences of my daily sync", "Change tomorrow's instance of the weekly meeting"],
    )
    async def update_meeting(
        self,
        meeting_id: str,
        topic: str | None = None,
        start_time: str | None = None,
        duration: int | None = None,
        timezone: str | None = None,
        agenda: str | None = None,
        occurrence_id: str | None = None,
        invitees: list[str] | None = None,
        recurrence: RecurrenceInput | None = None,
    ) -> Tuple[bool, str]:
        """Update a Zoom meeting — only fields explicitly provided by user."""
        try:
            body: dict[str, object] = {}
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
            if occurrence_id is not None:
                body["occurrence_id"] = occurrence_id
            if recurrence is not None:
                # Forcefully append 'Z' to end_date_time if the AI forgot it
                if getattr(recurrence, "end_date_time", None) and not recurrence.end_date_time.endswith("Z"):
                    recurrence.end_date_time += "Z"
                body["recurrence"] = recurrence.model_dump(by_alias=True, exclude_none=True)
            if invitees:
                body["settings"] = {
                    "meeting_invitees": [{"email": email} for email in invitees]
                }
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
        description="Delete a Zoom meeting (entire series or specific occurrence).",
        llm_description=(
            "Deletes a meeting by meeting_id. For recurring meetings, omit occurrence_id to delete the entire series. "
            "Provide occurrence_id to delete only one specific occurrence."
        ),
        args_schema=DeleteMeetingInput,
        returns="JSON confirming deletion",
        primary_intent=ToolIntent.ACTION,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to cancel or delete a Zoom meeting",
            "User wants to cancel a whole recurring series or just one instance of it",
            "User wants to delete the occurrences of a recurring meeting",
        ],
        when_not_to_use=[
            "User wants to reschedule (use update_meeting)",
        ],
        typical_queries=[
            "Cancel Zoom meeting",
            "Delete the entire sync series",
            "Cancel tomorrow's instance of my meeting",
            "Delete the occurrences of the recurring meeting on March 20 and March 21",
        ],
    )
    async def delete_meeting(
        self,
        meeting_id: str,
        occurrence_id: str | None = None,
        cancel_meeting_reminder: bool | None = None,
    ) -> Tuple[bool, str]:
        """Delete a Zoom meeting."""
        try:
            logger.info("zoom.delete_meeting called for meeting_id=%s occurrence_id=%s", meeting_id, occurrence_id)
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

    @tool(
        app_name="zoom",
        tool_name="list_recurring_meetings_ending_in_range",
        description="List recurring Zoom meetings whose series ends within a given date range.",
        llm_description=(
            "Returns recurring meetings that are ending (final occurrence) within the given from_/to_ window. "
            "Useful for finding series that are about to expire. "
            "Checks recurrence.end_date_time if set, otherwise falls back to the last occurrence's start_time."
        ),
        args_schema=ListRecurringMeetingsEndingInput,
        returns="JSON list of recurring meetings ending in range, with their end date and remaining occurrences",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User asks which recurring meetings are ending soon",
            "User wants to know which series expire this month/week",
        ],
        when_not_to_use=[
            "User wants all meetings in a range (use list_meetings)",
            "User wants a specific meeting's details (use get_meeting)",
        ],
        typical_queries=["Which recurring meetings are ending this month?", "Show me series expiring this week"],
    )
    async def list_recurring_meetings_ending_in_range(
        self,
        from_: str,
        to_: str,
        top: int | None = 200,
    ) -> Tuple[bool, str]:
        """Return recurring meetings whose series ends within [from_, to_]."""
        try:
            top = min(top or 200, 200)

            from_dt = self._ensure_aware(datetime.fromisoformat(from_.replace("Z", "+00:00")))
            to_dt   = self._ensure_aware(datetime.fromisoformat(to_.replace("Z", "+00:00")))

            # Step 1 — fetch only necessary meeting types + paginate
            all_parents: list[dict] = []

            for meeting_type in ("scheduled", "upcoming"):
                next_page_token = None

                while True:
                    response = await self.client.meetings(
                        userId="me",
                        type_=meeting_type,
                        page_size=100,
                        next_page_token=next_page_token,
                    )

                    success, cleaned = self._handle_response(response, "ok")
                    if not success:
                        break

                    data = json.loads(cleaned)
                    payload = data.get("data", data)

                    meetings = payload.get("meetings", [])
                    all_parents.extend(meetings)

                    next_page_token = payload.get("next_page_token")
                    if not next_page_token:
                        break

            # Step 2 — deduplicate + filter recurring
            seen: set[str] = set()
            recurring_parents: list[dict] = []

            for parent in all_parents:
                mid = str(parent.get("id", ""))

                if not mid or mid in seen:
                    continue

                seen.add(mid)

                if parent.get("type") in (3, 8):
                    recurring_parents.append(parent)

            results: list[dict] = []
            recurring_ids: list[str] = []

            # Step 3 — EARLY FILTER using recurrence.end_date_time
            for meeting in recurring_parents:
                recurrence = meeting.get("recurrence") or {}
                end_date_time = recurrence.get("end_date_time")

                if end_date_time:
                    try:
                        end_dt = self._ensure_aware(datetime.fromisoformat(end_date_time.replace("Z", "+00:00")))

                        if not (from_dt <= end_dt <= to_dt):
                            continue

                        # We already know it matches → NO API CALL NEEDED
                        results.append({
                            "meeting_id": meeting.get("id"),
                            "topic": meeting.get("topic"),
                            "series_end": end_date_time,
                            "end_determined_by": "end_date_time",
                            "recurrence_type": recurrence.get("type"),
                            "repeat_interval": recurrence.get("repeat_interval"),
                            "remaining_occurrences": [],  # skipped for perf
                            "join_url": meeting.get("join_url"),
                        })

                        if len(results) >= top:
                            break

                    except Exception:
                        pass

                else:
                    # Needs fallback → fetch details later
                    recurring_ids.append(str(meeting.get("id")))

            # Step 4 — fetch ONLY required details (fallback cases)
            if len(results) < top and recurring_ids:
                details_by_id = await self._meeting_details_by_id(recurring_ids)

                for meeting in recurring_parents:
                    if len(results) >= top:
                        break

                    mid = str(meeting.get("id"))
                    if mid not in recurring_ids:
                        continue

                    detail = details_by_id.get(mid)
                    if not detail:
                        continue

                    occurrences = detail.get("occurrences") or []
                    if not occurrences:
                        continue

                    # fallback → last occurrence
                    last_occ = max(
                        occurrences,
                        key=lambda o: o.get("start_time", "")
                    )

                    series_end = last_occ.get("start_time")
                    if not series_end:
                        continue

                    if not self._in_range(series_end, from_dt, to_dt):
                        continue

                    results.append({
                        "meeting_id": detail.get("id"),
                        "topic": detail.get("topic"),
                        "series_end": series_end,
                        "end_determined_by": "last_occurrence",
                        "recurrence_type": detail.get("recurrence", {}).get("type"),
                        "repeat_interval": detail.get("recurrence", {}).get("repeat_interval"),
                        "remaining_occurrences": occurrences,
                        "join_url": detail.get("join_url"),
                    })

            # Step 5 — sort + trim
            results.sort(key=lambda row: row["series_end"])
            trimmed = results[:top]

            return True, json.dumps({
                "meetings": trimmed,
                "count": len(trimmed),
                "from": from_,
                "to": to_,
            })

        except Exception as e:
            logger.error("Error listing recurring meetings ending in range: %s", e)
            return False, json.dumps({"error": str(e)})

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
        returns="JSON with timestamped transcript (one line per cue: [start - end] text), meeting_id, instance_uuid",
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
                return False, json.dumps({
                    "error": instances_response.error or instances_response.message or "Unknown error",
                })

            instances_data = instances_response.data if isinstance(instances_response, ZoomResponse) else instances_response
            meetings = instances_data.get("meetings", []) if isinstance(instances_data, dict) else []

            if not meetings:
                return False, json.dumps({
                    "error": "No past instances found for this meeting. Has the meeting ended yet?",
                    "meeting_id": meeting_id,
                })

            # Step 2 — take the most recent instance UUID and double-encode if needed
            instance_uuid_raw = meetings[-1].get("uuid")  # last = most recent
            if not instance_uuid_raw:
                return False, json.dumps({"error": "Instance UUID missing from past meeting data."})
            instance_uuid = str(instance_uuid_raw)

            if instance_uuid.startswith("/") or "/" in instance_uuid:
                encoded_uuid = quote(quote(instance_uuid, safe=""), safe="")
            else:
                encoded_uuid = instance_uuid

            logger.info("Using instance UUID: %s (encoded: %s)", instance_uuid, encoded_uuid)

            # Step 3 — fetch transcript metadata
            transcript_response = await self.client.get_meeting_transcript(meetingId=encoded_uuid)

            if isinstance(transcript_response, ZoomResponse) and not transcript_response.success:
                return False, json.dumps({
                    "error": transcript_response.error or transcript_response.message or "Unknown error",
                })

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

    # ------------------------------------------------------------------
    # Contact tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="list_contacts",
        description="List the authenticated user's Zoom contacts.",
        llm_description=(
            "Returns the user's Zoom contacts. "
            "Use type='company' for people in the same org, type='external' for outside contacts. "
            "Omit type to return all. Supports pagination via next_page_token."
        ),
        args_schema=ListContactsInput,
        returns="JSON with list of contacts and pagination token",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to see their Zoom contacts",
            "User asks who is in their Zoom contact list",
            "User wants to find a contact's email or user ID before inviting them to a meeting",
        ],
        when_not_to_use=[
            "User wants details of a single contact (use get_contact)",
            "User wants to list meeting participants (use list_meetings)",
        ],
        typical_queries=["Show my Zoom contacts", "List company contacts", "Who are my external Zoom contacts?"],
    )
    async def list_contacts(
        self,
        type_: str | None = None,
        top: int | None = 10,
    ) -> Tuple[bool, str]:
        """List Zoom contacts (personal, company, external) with auto-pagination."""
        try:
            # Zoom supports filtering via type param
            types_to_fetch = [type_] if type_ else ["personal", "company", "external"]
            limit = top if top is not None and top > 0 else 10
            page_size = 100  # Zoom API page size; `limit` caps total merged results
            all_contacts: list = []
            for contact_type in types_to_fetch:
                if len(all_contacts) >= limit:
                    break
                next_page_token: str | None = None

                while True:
                    response = await self.client.get_user_contacts(
                        type_=contact_type,
                        page_size=page_size,
                        next_page_token=next_page_token,
                    )

                    if not response.success:
                        break

                    data = response.data or {}
                    contacts = data.get("contacts", [])

                    # Tag contact type
                    for contact in contacts:
                        contact["contact_type"] = contact_type

                    all_contacts.extend(contacts)
                    if len(all_contacts) >= limit:
                        break

                    # Pagination handling
                    next_token = data.get("next_page_token")
                    if not next_token:
                        break
                    next_page_token = next_token

            trimmed_contacts = all_contacts[:limit]
            return True, json.dumps({
                "message": "Contacts fetched successfully",
                "data": {
                    "contacts": trimmed_contacts,
                    "total": len(trimmed_contacts),
                },
            })
        except Exception as e:
            logger.error("Error listing contacts: %s", e)
            return False, json.dumps({"error": str(e)})


    @tool(
        app_name="zoom",
        tool_name="get_contact",
        description="Get details of a specific Zoom contact by email, user ID, or member ID.",
        llm_description=(
            "Returns full details for a single Zoom contact. "
            "The identifier can be the contact's email address, Zoom user ID, or member ID. "
            "Set query_presence_status=true to also fetch their current availability/presence status."
        ),
        args_schema=GetContactInput,
        returns="JSON with contact details including name, email, and optionally presence status",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants details of a specific Zoom contact",
            "User asks for a contact's profile or presence status",
            "User provides an email or name and wants to look up their Zoom details",
        ],
        when_not_to_use=[
            "User wants to list all contacts (use list_contacts)",
        ],
        typical_queries=["Get contact details for john@example.com", "Is my contact online?", "Look up Zoom user by email"],
    )
    async def get_contact(
        self,
        identifier: str,
    ) -> Tuple[bool, str]:
        """Get a specific Zoom contact by identifier."""
        try:
            response = await self.client.get_user_contact(
                identifier=identifier,
            )
            return self._handle_response(response, "Contact fetched successfully")
        except Exception as e:
            logger.error("Error getting contact: %s", e)
            return False, json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Zoom Docs tools
    # ------------------------------------------------------------------

    @tool(
        app_name="zoom",
        tool_name="list_folder_children",
        description="List files and folders inside a Zoom Docs folder.",
        llm_description=(
            "Lists the contents (files and sub-folders) of a Zoom Docs folder. "
            "Use folder_id='root' (default) to browse the authenticated user's top-level 'My Docs' folder. "
            "Pass a specific folder_id to drill into any sub-folder. "
            "Supports pagination via next_page_token. "
            "Scope: docs:read:list_files."
        ),
        args_schema=ListFolderChildrenInput,
        returns="JSON with list of files and folders inside the specified folder",
        primary_intent=ToolIntent.SEARCH,
        category=ToolCategory.COMMUNICATION,
        when_to_use=[
            "User wants to browse their Zoom Docs files",
            "User asks what's in a Zoom Docs folder",
            "User wants to list files in their root Docs folder",
            "User wants to explore contents of a specific folder",
        ],
        when_not_to_use=[
            "User wants files shared with them (use list_shared_files or list_shared_folders)",
        ],
        typical_queries=["Show my Zoom Docs files", "What's in my root docs folder?", "List files in folder abc123", "Browse my Zoom documents"],
    )
    async def list_folder_children(
        self,
        folder_id: str = "root",
        page_size: int | None = 50
    ) -> Tuple[bool, str]:
        """List children of a Zoom Docs folder (or root)."""
        try:
            response = await self.client.list_all_children(
                fileId=folder_id,
                page_size=page_size,
            )
            return self._handle_response(response, f"Folder contents listed successfully for '{folder_id}'")
        except Exception as e:
            logger.error("Error listing folder children: %s", e)
            return False, json.dumps({"error": str(e)})
