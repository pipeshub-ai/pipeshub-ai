import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolsetBuilder,
)
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource
from app.sources.external.google.meet.meet import GoogleMeetDataSource
from app.utils.time_conversion import parse_timestamp, prepare_iso_timestamps

logger = logging.getLogger(__name__)

# Pydantic schemas for Google Meet tools
class StartInstantMeetingInput(BaseModel):
    """Schema for starting an instant meeting"""
    title: Optional[str] = Field(default=None, description="Meeting title/display name")
    description: Optional[str] = Field(default=None, description="Meeting description")


class JoinMeetingByCodeInput(BaseModel):
    """Schema for joining a meeting by code"""
    meeting_code: str = Field(description="Meeting code (e.g., 'abc-defg-hij')")


class ScheduleMeetingWithCalendarInput(BaseModel):
    """Schema for scheduling a meeting with calendar"""
    title: str = Field(description="Meeting title")
    start_time: str = Field(description="Meeting start time (ISO format or timestamp)")
    duration_minutes: int = Field(description="Meeting duration in minutes")
    attendees: Optional[List[str]] = Field(default=None, description="List of attendee email addresses")
    description: Optional[str] = Field(default=None, description="Meeting description/agenda")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the meeting")
    recurrence: Optional[dict] = Field(default=None, description="Recurrence pattern for recurring meetings")


class FindAvailableTimeInput(BaseModel):
    """Schema for finding available time"""
    attendees: List[str] = Field(description="List of attendee email addresses")
    duration_minutes: int = Field(description="Meeting duration in minutes")
    date_range_start: str = Field(description="Start of date range to search (ISO format)")
    date_range_end: str = Field(description="End of date range to search (ISO format)")
    working_hours_start: Optional[str] = Field(default=None, description="Working hours start time (HH:MM format)")
    working_hours_end: Optional[str] = Field(default=None, description="Working hours end time (HH:MM format)")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the search")


class UpdateScheduledMeetingInput(BaseModel):
    """Schema for updating a scheduled meeting"""
    event_id: str = Field(description="Calendar event ID to update")
    title: Optional[str] = Field(default=None, description="New meeting title")
    start_time: Optional[str] = Field(default=None, description="New start time (ISO format)")
    duration_minutes: Optional[int] = Field(default=None, description="New duration in minutes")
    attendees: Optional[List[str]] = Field(default=None, description="Updated list of attendee email addresses")
    description: Optional[str] = Field(default=None, description="New meeting description")


class CancelMeetingInput(BaseModel):
    """Schema for canceling a meeting"""
    event_id: str = Field(description="Calendar event ID to cancel")
    notify_attendees: Optional[bool] = Field(default=None, description="Whether to notify attendees about cancellation")


class GetMeetingDetailsInput(BaseModel):
    """Schema for getting meeting details"""
    event_id: str = Field(description="Calendar event ID to get details for")


class ListUpcomingMeetingsInput(BaseModel):
    """Schema for listing upcoming meetings"""
    max_results: Optional[int] = Field(default=None, description="Maximum number of meetings to return")
    time_min: Optional[str] = Field(default=None, description="Lower bound for meeting start time (ISO format)")
    time_max: Optional[str] = Field(default=None, description="Upper bound for meeting start time (ISO format)")


class CreateMeetingSpaceInput(BaseModel):
    """Schema for creating a meeting space"""
    title: Optional[str] = Field(default=None, description="Meeting title/display name")
    description: Optional[str] = Field(default=None, description="Meeting description")
    start_time: Optional[str] = Field(default=None, description="Meeting start time (ISO format or timestamp)")
    duration_minutes: Optional[int] = Field(default=None, description="Meeting duration in minutes")
    attendees: Optional[List[str]] = Field(default=None, description="List of attendee email addresses")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the meeting")
    create_calendar_event: Optional[bool] = Field(default=True, description="Whether to create a corresponding calendar event")
    space_config: Optional[dict] = Field(default=None, description="Additional space configuration")


class UpdateMeetingSpaceInput(BaseModel):
    """Schema for updating a meeting space"""
    space_name: str = Field(description="Resource name of the space to update")
    title: Optional[str] = Field(default=None, description="New meeting title/display name")
    description: Optional[str] = Field(default=None, description="New meeting description")
    space_config: Optional[dict] = Field(default=None, description="Additional space configuration updates")


class GetMeetingSpaceInput(BaseModel):
    """Schema for getting a meeting space"""
    space_name: str = Field(description="Resource name of the space")


class EndActiveConferenceInput(BaseModel):
    """Schema for ending an active conference"""
    space_name: str = Field(description="Resource name of the space")


class GetConferenceRecordsInput(BaseModel):
    """Schema for getting conference records"""
    page_size: Optional[int] = Field(default=None, description="Maximum number of conference records to return")
    page_token: Optional[str] = Field(default=None, description="Page token for pagination")
    filter: Optional[str] = Field(default=None, description="Raw filter in EBNF format")
    start_time_from: Optional[str] = Field(default=None, description="ISO8601 UTC start time lower bound")
    start_time_to: Optional[str] = Field(default=None, description="ISO8601 UTC start time upper bound")
    meeting_code: Optional[str] = Field(default=None, description="Filter by specific meeting code")
    space_name: Optional[str] = Field(default=None, description="Filter by specific space name")
    include_active_only: Optional[bool] = Field(default=None, description="Include only conferences that are currently active")


class GetConferenceRecordDetailsInput(BaseModel):
    """Schema for getting conference record details"""
    conference_record: str = Field(description="Conference record name")


class GetConferenceParticipantsInput(BaseModel):
    """Schema for getting conference participants"""
    conference_record: str = Field(description="Conference record name")
    page_size: Optional[int] = Field(default=None, description="Maximum number of participants to return")
    page_token: Optional[str] = Field(default=None, description="Page token for pagination")
    filter: Optional[str] = Field(default=None, description="Filter condition for participants")
    include_active_only: Optional[bool] = Field(default=None, description="Include only currently active participants")


class GetConferenceRecordingsInput(BaseModel):
    """Schema for getting conference recordings"""
    conference_record: str = Field(description="Conference record name")
    page_size: Optional[int] = Field(default=None, description="Maximum number of recordings to return")
    page_token: Optional[str] = Field(default=None, description="Page token for pagination")


class GetConferenceTranscriptsInput(BaseModel):
    """Schema for getting conference transcripts"""
    conference_record: str = Field(description="Conference record name")
    page_size: Optional[int] = Field(default=None, description="Maximum number of transcripts to return")
    page_token: Optional[str] = Field(default=None, description="Page token for pagination")
    include_entries: Optional[bool] = Field(default=None, description="Include transcript entries for each transcript")


class GetTranscriptEntriesInput(BaseModel):
    """Schema for getting transcript entries"""
    transcript_name: str = Field(description="Transcript name")
    page_size: Optional[int] = Field(default=None, description="Maximum number of entries to return")
    page_token: Optional[str] = Field(default=None, description="Page token for pagination")


class GetMeetingSummaryInput(BaseModel):
    """Schema for getting meeting summary"""
    conference_record: str = Field(description="Conference record name")
    include_participants: Optional[bool] = Field(default=None, description="Include participant information")
    include_recordings: Optional[bool] = Field(default=None, description="Include recording information")
    include_transcripts: Optional[bool] = Field(default=None, description="Include transcript information")


# Register Google Meet toolset
@ToolsetBuilder("Meet")\
    .in_group("Google Workspace")\
    .with_description("Google Meet integration for video conferencing and meeting management")\
    .with_category(ToolCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Meet",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/meet",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/meetings.space.created"
                ]
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/meet.svg",
            app_group="Google Workspace",
            app_description="Meet OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/meet.svg"))\
    .build_decorator()
class GoogleMeet:
    """Meet tool exposed to the agents using MeetDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Meet tool"""
        """
        Args:
            client: Meet client
        Returns:
            None
        """
        self.google_client = client  # Store original GoogleClient for calendar access
        self.client = GoogleMeetDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

    @tool(
        path="/tools/meet/start_instant_meeting",
        short_description="Start an instant Google Meet meeting",
        description="Create a new instant Google Meet meeting space and return the join URL.",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="Meeting title/display name", required=False),
            ToolParameter(name="description", type=ParameterType.STRING, description="Meeting description", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="create")],
    )
    async def start_instant_meeting(self, title: Optional[str] = None, description: Optional[str] = None) -> tuple[bool, str]:
        """Start an instant Google Meet meeting"""
        """
        Args:
            title: Meeting title/display name
            description: Meeting description
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Create the Meet space
            space = self._run_async(self.client.spaces_create())
            space_name = space.get("name", "")
            meeting_code = space.get("meetingCode", "")
            meeting_uri = space.get("meetingUri", "")

            result = {
                "space_name": space_name,
                "meeting_code": meeting_code,
                "meeting_uri": meeting_uri,
                "join_url": f"https://meet.google.com/{meeting_code}",
                "message": "Instant meeting created successfully"
            }

            # Note: Google Meet Spaces API does not support setting displayName/description
            # Title and description are handled through calendar events when scheduling meetings
            if title or description:
                result["note"] = "Title and description are not supported for instant meetings. Use schedule_meeting_with_calendar for meetings with custom titles."

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to start instant meeting: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/schedule_meeting_with_calendar",
        short_description="Schedule a Google Meet meeting via Calendar",
        description="Schedule a Google Meet meeting with calendar integration, including attendees and recurrence options.",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="Meeting title", required=True),
            ToolParameter(name="start_time", type=ParameterType.STRING, description="Meeting start time (ISO format or timestamp)", required=True),
            ToolParameter(name="duration_minutes", type=ParameterType.INTEGER, description="Meeting duration in minutes", required=True),
            ToolParameter(name="attendees", type=ParameterType.ARRAY, description="List of attendee email addresses", required=False, items={"type": "string"}),
            ToolParameter(name="description", type=ParameterType.STRING, description="Meeting description/agenda", required=False),
            ToolParameter(name="timezone", type=ParameterType.STRING, description="Timezone for the meeting", required=False),
            ToolParameter(name="recurrence", type=ParameterType.OBJECT, description="Recurrence pattern for recurring meetings", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="create")],
    )
    def schedule_meeting_with_calendar(
        self,
        title: str,
        start_time: str,
        duration_minutes: int,
        attendees: Optional[list] = None,
        description: Optional[str] = None,
        timezone: str = "UTC",
        recurrence: Optional[dict] = None
    ) -> tuple[bool, str]:
        """Schedule a Google Meet with calendar integration"""
        """
        Args:
            title: Meeting title
            start_time: Meeting start time
            duration_minutes: Meeting duration in minutes
            attendees: List of attendee email addresses
            description: Meeting description/agenda
            timezone: Timezone for the meeting
            recurrence: Recurrence pattern for recurring meetings
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Calculate end time - parse start_time and calculate end_time

            start_timestamp = parse_timestamp(start_time)
            start_dt = datetime.fromtimestamp(start_timestamp / 1000, tz=dt_timezone.utc)
            end_dt = start_dt + timedelta(minutes=duration_minutes)

            # Convert to ISO format with timezone
            start_time_iso = start_dt.isoformat()
            end_time_iso = end_dt.isoformat()

            # Use GoogleCalendarDataSource directly with the authenticated GoogleClient
            calendar_client = GoogleCalendarDataSource(self.google_client)

            event_config = {
                "summary": title,
                "description": description or "",
                "start": {
                    "dateTime": start_time_iso,
                    "timeZone": timezone
                },
                "end": {
                    "dateTime": end_time_iso,
                    "timeZone": timezone
                },
                "attendees": [{"email": email} for email in attendees] if attendees else [],
                "conferenceData": {
                    "createRequest": {
                        "requestId": f"meet-{int(datetime.now().timestamp())}",
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet"
                        }
                    }
                }
            }

            # Add recurrence if specified
            if recurrence:
                event_config["recurrence"] = [recurrence.get("rule", "RRULE:FREQ=WEEKLY")]

            calendar_event = self._run_async(calendar_client.events_insert(
                calendarId="primary",
                conferenceDataVersion=1,
                body=event_config
            ))

            result = {
                "event_id": calendar_event.get("id"),
                "event_link": calendar_event.get("htmlLink"),
                "meet_link": calendar_event.get("hangoutLink"),
                "meeting_title": title,
                "start_time": start_time_iso,
                "end_time": end_time_iso,
                "duration_minutes": duration_minutes,
                "attendees": attendees or [],
                "message": "Meeting scheduled successfully with calendar integration"
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to schedule meeting: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/find_available_time",
        short_description="Find available time slots for a meeting",
        description="Find available time slots for a group of attendees by checking their calendar free/busy status.",
        parameters=[
            ToolParameter(name="attendees", type=ParameterType.ARRAY, description="List of attendee email addresses", required=True, items={"type": "string"}),
            ToolParameter(name="duration_minutes", type=ParameterType.INTEGER, description="Meeting duration in minutes", required=True),
            ToolParameter(name="date_range_start", type=ParameterType.STRING, description="Start of date range to search (ISO format)", required=True),
            ToolParameter(name="date_range_end", type=ParameterType.STRING, description="End of date range to search (ISO format)", required=True),
            ToolParameter(name="working_hours_start", type=ParameterType.STRING, description="Working hours start time (HH:MM format)", required=False),
            ToolParameter(name="working_hours_end", type=ParameterType.STRING, description="Working hours end time (HH:MM format)", required=False),
            ToolParameter(name="timezone", type=ParameterType.STRING, description="Timezone for the search", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    def find_available_time(
        self,
        attendees: list,
        duration_minutes: int,
        date_range_start: str,
        date_range_end: str,
        working_hours_start: Optional[str] = None,
        working_hours_end: Optional[str] = None,
        timezone: str = "UTC"
    ) -> tuple[bool, str]:
        """Find available time slots for a group of attendees"""
        """
        Args:
            attendees: List of attendee email addresses
            duration_minutes: Meeting duration in minutes
            date_range_start: Start of date range to search
            date_range_end: End of date range to search
            working_hours_start: Working hours start time
            working_hours_end: Working hours end time
            timezone: Timezone for the search
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:

            # Prepare time range
            start_iso, end_iso = prepare_iso_timestamps(date_range_start, date_range_end)

            # Create freebusy query
            calendar_client = GoogleCalendarDataSource(self.google_client)

            freebusy_query = {
                "timeMin": start_iso,
                "timeMax": end_iso,
                "items": [{"id": email} for email in attendees]
            }

            freebusy_result = self._run_async(calendar_client.freebusy_query(body=freebusy_query))

            # Analyze freebusy data to find available slots
            calendars = freebusy_result.get("calendars", {})
            busy_times = []

            for email, calendar_data in calendars.items():
                busy_periods = calendar_data.get("busy", [])
                for period in busy_periods:
                    busy_times.append({
                        "start": period.get("start"),
                        "end": period.get("end")
                    })

            # Find available slots (simplified algorithm)
            available_slots = []

            current_time = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))

            # Sort busy periods by start for consistent advancement
            busy_times_sorted = sorted(
                (
                    {
                        "start": datetime.fromisoformat(p["start"].replace('Z', '+00:00')),
                        "end": datetime.fromisoformat(p["end"].replace('Z', '+00:00')),
                    }
                    for p in busy_times
                    if p.get("start") and p.get("end")
                ),
                key=lambda x: x["start"]
            )

            while current_time + timedelta(minutes=duration_minutes) <= end_time:
                slot_end = current_time + timedelta(minutes=duration_minutes)

                # Check if this slot conflicts with any busy time
                conflicts = False
                next_time = None
                for busy_period in busy_times_sorted:
                    busy_start = busy_period["start"]
                    busy_end = busy_period["end"]
                    if current_time < busy_end and slot_end > busy_start:
                        conflicts = True
                        # Jump to end of this conflicting busy period
                        next_time = busy_end if next_time is None else max(next_time, busy_end)
                        break

                if not conflicts:
                    available_slots.append({
                        "start": current_time.isoformat().replace('+00:00', 'Z'),
                        "end": slot_end.isoformat().replace('+00:00', 'Z'),
                        "duration_minutes": duration_minutes
                    })

                # Advance time
                if conflicts and next_time:
                    current_time = next_time
                else:
                    # No conflict: step by granularity (30 minutes)
                    current_time += timedelta(minutes=30)

            result = {
                "attendees": attendees,
                "duration_minutes": duration_minutes,
                "search_range": {
                    "start": start_iso,
                    "end": end_iso
                },
                "available_slots": available_slots[:10],  # Return first 10 slots
                "total_slots_found": len(available_slots),
                "message": f"Found {len(available_slots)} available time slots"
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to find available time: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/update_scheduled_meeting",
        short_description="Update a scheduled meeting",
        description="Update an existing scheduled meeting's title, time, duration, attendees, or description.",
        parameters=[
            ToolParameter(name="event_id", type=ParameterType.STRING, description="Calendar event ID to update", required=True),
            ToolParameter(name="title", type=ParameterType.STRING, description="New meeting title", required=False),
            ToolParameter(name="start_time", type=ParameterType.STRING, description="New start time (ISO format)", required=False),
            ToolParameter(name="duration_minutes", type=ParameterType.INTEGER, description="New duration in minutes", required=False),
            ToolParameter(name="attendees", type=ParameterType.ARRAY, description="Updated list of attendee email addresses", required=False, items={"type": "string"}),
            ToolParameter(name="description", type=ParameterType.STRING, description="New meeting description", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="update")],
    )
    def update_scheduled_meeting(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        attendees: Optional[list] = None,
        description: Optional[str] = None
    ) -> tuple[bool, str]:
        """Update an existing scheduled meeting"""
        """
        Args:
            event_id: Calendar event ID to update
            title: New meeting title
            start_time: New start time
            duration_minutes: New duration in minutes
            attendees: Updated list of attendees
            description: New meeting description
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            from app.sources.external.google.calendar.gcalendar import (
                GoogleCalendarDataSource,
            )
            from app.utils.time_conversion import prepare_iso_timestamps

            calendar_client = GoogleCalendarDataSource(self.google_client)

            # Get current event
            current_event = self._run_async(calendar_client.events_get(
                calendarId="primary",
                eventId=event_id
            ))

            # Prepare update data
            update_data = {}

            if title:
                update_data["summary"] = title

            if description:
                update_data["description"] = description

            if attendees:
                update_data["attendees"] = [{"email": email} for email in attendees]

            if start_time and duration_minutes:
                start_time_iso, _ = prepare_iso_timestamps(start_time, "")
                from datetime import datetime, timedelta
                start_dt = datetime.fromisoformat(start_time_iso.replace('Z', '+00:00'))
                end_dt = start_dt + timedelta(minutes=duration_minutes)
                end_time_iso = end_dt.isoformat().replace('+00:00', 'Z')

                update_data["start"] = {
                    "dateTime": start_time_iso,
                    "timeZone": current_event.get("start", {}).get("timeZone", "UTC")
                }
                update_data["end"] = {
                    "dateTime": end_time_iso,
                    "timeZone": current_event.get("end", {}).get("timeZone", "UTC")
                }

            # Update the event
            updated_event = self._run_async(calendar_client.events_patch(
                calendarId="primary",
                eventId=event_id,
                body=update_data
            ))

            result = {
                "event_id": event_id,
                "updated_event": updated_event,
                "meet_link": updated_event.get("hangoutLink"),
                "message": "Meeting updated successfully"
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to update meeting: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/cancel_meeting",
        short_description="Cancel a scheduled meeting",
        description="Cancel a scheduled meeting by deleting the calendar event, with optional attendee notification.",
        parameters=[
            ToolParameter(name="event_id", type=ParameterType.STRING, description="Calendar event ID to cancel", required=True),
            ToolParameter(name="notify_attendees", type=ParameterType.BOOLEAN, description="Whether to notify attendees about cancellation", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="delete")],
    )
    async def cancel_meeting(self, event_id: str, notify_attendees: Optional[bool] = None) -> tuple[bool, str]:
        """Cancel a scheduled meeting"""
        """
        Args:
            event_id: Calendar event ID to cancel
            notify_attendees: Whether to notify attendees
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            from app.sources.external.google.calendar.gcalendar import (
                GoogleCalendarDataSource,
            )

            calendar_client = GoogleCalendarDataSource(self.google_client)

            # Delete the event
            self._run_async(calendar_client.events_delete(
                calendarId="primary",
                eventId=event_id,
                sendUpdates="all" if notify_attendees else "none"
            ))

            result = {
                "event_id": event_id,
                "cancelled": True,
                "attendees_notified": notify_attendees or False,
                "message": "Meeting cancelled successfully"
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to cancel meeting: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/get_meeting_details",
        short_description="Get details of a scheduled meeting",
        description="Get details of a scheduled meeting including title, time, attendees, and Meet link.",
        parameters=[
            ToolParameter(name="event_id", type=ParameterType.STRING, description="Calendar event ID to get details for", required=True),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    async def get_meeting_details(self, event_id: str) -> tuple[bool, str]:
        """Get details of a scheduled meeting"""
        """
        Args:
            event_id: Calendar event ID
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            from app.sources.external.google.calendar.gcalendar import (
                GoogleCalendarDataSource,
            )

            calendar_client = GoogleCalendarDataSource(self.google_client)

            # Get event details
            event = self._run_async(calendar_client.events_get(
                calendarId="primary",
                eventId=event_id
            ))

            # Extract meeting information
            result = {
                "event_id": event_id,
                "title": event.get("summary", ""),
                "description": event.get("description", ""),
                "start_time": event.get("start", {}).get("dateTime"),
                "end_time": event.get("end", {}).get("dateTime"),
                "timezone": event.get("start", {}).get("timeZone"),
                "attendees": [attendee.get("email") for attendee in event.get("attendees", [])],
                "meet_link": event.get("hangoutLink"),
                "event_link": event.get("htmlLink"),
                "status": event.get("status"),
                "created": event.get("created"),
                "updated": event.get("updated")
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to get meeting details: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/list_upcoming_meetings",
        short_description="List upcoming Google Meet meetings",
        description="List upcoming Google Meet meetings from the user's calendar within an optional time range.",
        parameters=[
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Maximum number of meetings to return", required=False),
            ToolParameter(name="time_min", type=ParameterType.STRING, description="Lower bound for meeting start time (ISO format)", required=False),
            ToolParameter(name="time_max", type=ParameterType.STRING, description="Upper bound for meeting start time (ISO format)", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    def list_upcoming_meetings(
        self,
        max_results: Optional[int] = None,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None
    ) -> tuple[bool, str]:
        """List upcoming Google Meet meetings"""
        """
        Args:
            max_results: Maximum number of meetings to return
            time_min: Lower bound for meeting start time
            time_max: Upper bound for meeting start time
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            from datetime import datetime, timezone

            from app.sources.external.google.calendar.gcalendar import (
                GoogleCalendarDataSource,
            )

            calendar_client = GoogleCalendarDataSource(self.google_client)

            # Set default time range if not provided
            if not time_min:
                time_min = datetime.now(timezone.utc).isoformat()

            # Build query parameters
            query_params = {
                "calendarId": "primary",
                "timeMin": time_min,
                "singleEvents": True,
                "orderBy": "startTime"
            }

            if time_max:
                query_params["timeMax"] = time_max

            if max_results:
                query_params["maxResults"] = max_results

            # Get events
            events_response = self._run_async(calendar_client.events_list(**query_params))

            # Filter for events with Meet links
            meet_events = []
            for event in events_response.get("items", []):
                if event.get("hangoutLink") or event.get("conferenceData"):
                    meet_events.append({
                        "event_id": event.get("id"),
                        "title": event.get("summary", ""),
                        "description": event.get("description", ""),
                        "start_time": event.get("start", {}).get("dateTime"),
                        "end_time": event.get("end", {}).get("dateTime"),
                        "timezone": event.get("start", {}).get("timeZone"),
                        "attendees": [attendee.get("email") for attendee in event.get("attendees", [])],
                        "meet_link": event.get("hangoutLink"),
                        "event_link": event.get("htmlLink"),
                        "status": event.get("status")
                    })

            result = {
                "meetings": meet_events,
                "total_count": len(meet_events),
                "time_range": {
                    "start": time_min,
                    "end": time_max
                },
                "message": f"Found {len(meet_events)} upcoming meetings"
            }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to list upcoming meetings: {e}")
            return False, json.dumps({"error": str(e)})

    def _normalize_meet_filter(self, raw_filter: str) -> str:
        """Normalize user-provided filter to Meet API expected syntax.

        - Convert camelCase fields to snake_case: startTime/endTime/meetingCode -> start_time/end_time/meeting_code
        - Normalize nested fields: space.meetingCode -> space.meeting_code
        - Ensure values are wrapped with double quotes instead of single quotes
        """
        if not raw_filter:
            return raw_filter

        normalized = raw_filter.strip()

        # Normalize quotes: replace smart quotes and single quotes with double quotes around values
        normalized = normalized.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'")
        normalized = re.sub(r"'([^']*)'", r'"\1"', normalized)

        # Field mappings (use word-boundaries where possible)
        replacements = [
            (r"\bstartTime\b", "start_time"),
            (r"\bendTime\b", "end_time"),
            (r"\bmeetingCode\b", "meeting_code"),
            (r"space\.meetingCode", "space.meeting_code"),
        ]

        for pattern, repl in replacements:
            normalized = re.sub(pattern, repl, normalized)

        return normalized

    @tool(
        path="/tools/meet/get_meeting_space",
        short_description="Get details about a meeting space",
        description="Get details about a Google Meet meeting space by its resource name.",
        parameters=[
            ToolParameter(name="space_name", type=ParameterType.STRING, description="Resource name of the space", required=True),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    async def get_meeting_space(self, space_name: str) -> tuple[bool, str]:
        """Get details about a meeting space"""
        """
        Args:
            space_name: Resource name of the space
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            space = self._run_async(self.client.spaces_get(name=space_name))

            return True, json.dumps(space)
        except Exception as e:
            logger.error(f"Failed to get meeting space: {e}")
            return False, json.dumps({"error": str(e)})


    @tool(
        path="/tools/meet/get_conference_record_details",
        short_description="Get details about a conference record",
        description="Get detailed information about a specific conference record including start/end times and space info.",
        parameters=[
            ToolParameter(name="conference_record", type=ParameterType.STRING, description="Conference record name", required=True),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    async def get_conference_record_details(self, conference_record: str) -> tuple[bool, str]:
        """Get detailed information about a specific conference record"""
        """
        Args:
            conference_record: Conference record name
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            record = self._run_async(self.client.conference_records_get(name=conference_record))

            return True, json.dumps(record)
        except Exception as e:
            logger.error(f"Failed to get conference record details: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/meet/get_conference_participants",
        short_description="Get participants from a conference record",
        description="Get participants in a conference record with optional filtering for active participants.",
        parameters=[
            ToolParameter(name="conference_record", type=ParameterType.STRING, description="Conference record name", required=True),
            ToolParameter(name="page_size", type=ParameterType.INTEGER, description="Maximum number of participants to return", required=False),
            ToolParameter(name="page_token", type=ParameterType.STRING, description="Page token for pagination", required=False),
            ToolParameter(name="filter", type=ParameterType.STRING, description="Filter condition for participants", required=False),
            ToolParameter(name="include_active_only", type=ParameterType.BOOLEAN, description="Include only currently active participants", required=False),
        ],
        tags=[Tag(key="category", value="meetings"), Tag(key="type", value="read")],
    )
    def get_conference_participants(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        filter: Optional[str] = None,
        include_active_only: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get participants in a conference record with enhanced filtering"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of participants
            page_token: Page token for pagination
            filter: Filter condition
            include_active_only: Include only currently active participants
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Apply active filter if requested
            effective_filter = filter
            if include_active_only and not filter:
                effective_filter = "latest_end_time IS NULL"

            # Use GoogleMeetDataSource method
            participants = self._run_async(self.client.conference_records_participants_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token,
                filter=effective_filter
            ))

            # Enhance response with participant summary
            participant_list = participants.get("participants", [])
            enhanced_response = {
                "participants": participant_list,
                "next_page_token": participants.get("nextPageToken"),
                "total_count": len(participant_list),
                "filter_applied": effective_filter,
                "summary": {
                    "active_participants": len([p for p in participant_list if not p.get("latestEndTime")]),
                    "completed_participants": len([p for p in participant_list if p.get("latestEndTime")])
                }
            }

            return True, json.dumps(enhanced_response)
        except Exception as e:
            logger.error(f"Failed to get conference participants: {e}")
            return False, json.dumps({"error": str(e)})
