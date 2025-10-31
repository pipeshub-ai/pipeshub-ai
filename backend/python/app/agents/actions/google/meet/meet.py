import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource
from app.sources.external.google.meet.meet import GoogleMeetDataSource
from app.utils.time_conversion import parse_timestamp, prepare_iso_timestamps

logger = logging.getLogger(__name__)

class GoogleMeet:
    """Google Meet tool exposed to the agents using GoogleMeetDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Meet tool"""
        """
        Args:
            client: Google Meet client
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
        app_name="meet",
        tool_name="start_instant_meeting",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Meeting title/display name",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Meeting description",
                required=False
            )
        ]
    )
    def start_instant_meeting(self, title: Optional[str] = None, description: Optional[str] = None) -> tuple[bool, str]:
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
        app_name="meet",
        tool_name="join_meeting_by_code",
        parameters=[
            ToolParameter(
                name="meeting_code",
                type=ParameterType.STRING,
                description="Meeting code (e.g., 'abc-defg-hij')",
                required=True
            )
        ]
    )
    def join_meeting_by_code(self, meeting_code: str) -> tuple[bool, str]:
        """Join an existing Google Meet by meeting code"""
        """
        Args:
            meeting_code: Meeting code to join
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Clean the meeting code (remove spaces, convert to lowercase)
            clean_code = meeting_code.replace(" ", "").replace("-", "").lower()

            # Construct the join URL
            join_url = f"https://meet.google.com/{clean_code}"

            # Try to get space info if possible
            try:
                space = self._run_async(self.client.spaces_get(name=f"spaces/{clean_code}"))
                result = {
                    "meeting_code": clean_code,
                    "join_url": join_url,
                    "space_info": space,
                    "message": f"Join link generated for meeting {clean_code}"
                }
            except Exception:
                # If we can't get space info, just return the join URL
                result = {
                    "meeting_code": clean_code,
                    "join_url": join_url,
                    "message": f"Join link generated for meeting {clean_code}"
                }

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to generate join link for meeting {meeting_code}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="schedule_meeting_with_calendar",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Meeting title",
                required=True
            ),
            ToolParameter(
                name="start_time",
                type=ParameterType.STRING,
                description="Meeting start time (ISO format or timestamp)",
                required=True
            ),
            ToolParameter(
                name="duration_minutes",
                type=ParameterType.INTEGER,
                description="Meeting duration in minutes",
                required=True
            ),
            ToolParameter(
                name="attendees",
                type=ParameterType.ARRAY,
                description="List of attendee email addresses",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Meeting description/agenda",
                required=False
            ),
            ToolParameter(
                name="timezone",
                type=ParameterType.STRING,
                description="Timezone for the meeting (default: UTC)",
                required=False
            ),
            ToolParameter(
                name="recurrence",
                type=ParameterType.OBJECT,
                description="Recurrence pattern for recurring meetings",
                required=False
            )
        ]
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
        app_name="meet",
        tool_name="find_available_time",
        parameters=[
            ToolParameter(
                name="attendees",
                type=ParameterType.ARRAY,
                description="List of attendee email addresses",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="duration_minutes",
                type=ParameterType.INTEGER,
                description="Meeting duration in minutes",
                required=True
            ),
            ToolParameter(
                name="date_range_start",
                type=ParameterType.STRING,
                description="Start of date range to search (ISO format)",
                required=True
            ),
            ToolParameter(
                name="date_range_end",
                type=ParameterType.STRING,
                description="End of date range to search (ISO format)",
                required=True
            ),
            ToolParameter(
                name="working_hours_start",
                type=ParameterType.STRING,
                description="Working hours start time (HH:MM format)",
                required=False
            ),
            ToolParameter(
                name="working_hours_end",
                type=ParameterType.STRING,
                description="Working hours end time (HH:MM format)",
                required=False
            ),
            ToolParameter(
                name="timezone",
                type=ParameterType.STRING,
                description="Timezone for the search (default: UTC)",
                required=False
            )
        ]
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
        app_name="meet",
        tool_name="update_scheduled_meeting",
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="Calendar event ID to update",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="New meeting title",
                required=False
            ),
            ToolParameter(
                name="start_time",
                type=ParameterType.STRING,
                description="New start time (ISO format)",
                required=False
            ),
            ToolParameter(
                name="duration_minutes",
                type=ParameterType.INTEGER,
                description="New duration in minutes",
                required=False
            ),
            ToolParameter(
                name="attendees",
                type=ParameterType.ARRAY,
                description="Updated list of attendee email addresses",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="New meeting description",
                required=False
            )
        ]
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
        app_name="meet",
        tool_name="cancel_meeting",
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="Calendar event ID to cancel",
                required=True
            ),
            ToolParameter(
                name="notify_attendees",
                type=ParameterType.BOOLEAN,
                description="Whether to notify attendees about cancellation",
                required=False
            )
        ]
    )
    def cancel_meeting(self, event_id: str, notify_attendees: Optional[bool] = None) -> tuple[bool, str]:
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
        app_name="meet",
        tool_name="get_meeting_details",
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="Calendar event ID to get details for",
                required=True
            )
        ]
    )
    def get_meeting_details(self, event_id: str) -> tuple[bool, str]:
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
        app_name="meet",
        tool_name="list_upcoming_meetings",
        parameters=[
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of meetings to return",
                required=False
            ),
            ToolParameter(
                name="time_min",
                type=ParameterType.STRING,
                description="Lower bound for meeting start time (ISO format)",
                required=False
            ),
            ToolParameter(
                name="time_max",
                type=ParameterType.STRING,
                description="Upper bound for meeting start time (ISO format)",
                required=False
            )
        ]
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
        normalized = normalized.replace(""", '"').replace(""", '"').replace("'", "'")
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
        app_name="meet",
        tool_name="create_meeting_space",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Meeting title/display name",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Meeting description",
                required=False
            ),
            ToolParameter(
                name="start_time",
                type=ParameterType.STRING,
                description="Meeting start time (ISO format or timestamp)",
                required=False
            ),
            ToolParameter(
                name="duration_minutes",
                type=ParameterType.INTEGER,
                description="Meeting duration in minutes",
                required=False
            ),
            ToolParameter(
                name="attendees",
                type=ParameterType.ARRAY,
                description="List of attendee email addresses",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="timezone",
                type=ParameterType.STRING,
                description="Timezone for the meeting (default: UTC)",
                required=False
            ),
            ToolParameter(
                name="create_calendar_event",
                type=ParameterType.BOOLEAN,
                description="Whether to create a corresponding calendar event (default: True)",
                required=False
            ),
            ToolParameter(
                name="space_config",
                type=ParameterType.OBJECT,
                description="Additional space configuration for Meet API",
                required=False
            )
        ]
    )
    def create_meeting_space(
        self,
        title: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        attendees: Optional[list] = None,
        timezone: str = "UTC",
        create_calendar_event: bool = True,
        space_config: Optional[dict] = None
    ) -> tuple[bool, str]:
        """Create a new Google Meet space with optional scheduling and calendar integration"""
        """
        Args:
            title: Meeting title/display name
            description: Meeting description
            start_time: Meeting start time (ISO format or timestamp)
            duration_minutes: Meeting duration in minutes
            attendees: List of attendee email addresses
            timezone: Timezone for the meeting
            create_calendar_event: Whether to create a corresponding calendar event
            space_config: Additional space configuration for Meet API
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Create the Meet space first
            space = self._run_async(self.client.spaces_create(body=space_config))
            space_name = space.get("name", "")
            meeting_code = space.get("meetingCode", "")
            meeting_uri = space.get("meetingUri", "")

            result = {
                "space_name": space_name,
                "meeting_code": meeting_code,
                "meeting_uri": meeting_uri,
                "space_config": space.get("spaceConfig", {}),
                "message": "Meeting space created successfully"
            }

            # Note: Google Meet Spaces API does not support setting displayName/description
            # Title and description are handled through calendar events when scheduling meetings
            if title or description:
                result["note"] = "Title and description are not supported for meeting spaces. Use schedule_meeting_with_calendar for meetings with custom titles."

            # Create calendar event if requested and timing info provided
            if create_calendar_event and start_time and duration_minutes:
                try:
                    # Import calendar client if available
                    from app.sources.external.google.calendar.gcalendar import (
                        GoogleCalendarDataSource,
                    )
                    from app.utils.time_conversion import prepare_iso_timestamps

                    # Calculate end time
                    start_time_iso, _ = prepare_iso_timestamps(start_time, "")
                    from datetime import datetime, timedelta
                    start_dt = datetime.fromisoformat(start_time_iso.replace('Z', '+00:00'))
                    end_dt = start_dt + timedelta(minutes=duration_minutes)
                    end_time_iso = end_dt.isoformat().replace('+00:00', 'Z')

                    # Create calendar event with Meet integration
                    calendar_client = GoogleCalendarDataSource(self.google_client)
                    event_config = {
                        "summary": title or f"Google Meet - {meeting_code}",
                        "description": description or f"Join the meeting: {meeting_uri}",
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
                                "requestId": f"meet-{meeting_code}",
                                "conferenceSolutionKey": {
                                    "type": "hangoutsMeet"
                                }
                            }
                        }
                    }

                    calendar_event = self._run_async(calendar_client.events_insert(
                        calendarId="primary",
                        body=event_config
                    ))

                    result["calendar_event"] = {
                        "event_id": calendar_event.get("id"),
                        "event_link": calendar_event.get("htmlLink"),
                        "meet_link": calendar_event.get("hangoutLink")
                    }
                    result["message"] += " and calendar event created"

                except Exception as e:
                    logger.warning(f"Failed to create calendar event: {e}")
                    result["warning"] = f"Meet space created but calendar event failed: {str(e)}"

            return True, json.dumps(result)

        except Exception as e:
            logger.error(f"Failed to create meeting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="update_meeting_space",
        parameters=[
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Resource name of the space to update",
                required=True
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="New meeting title/display name",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="New meeting description",
                required=False
            ),
            ToolParameter(
                name="space_config",
                type=ParameterType.OBJECT,
                description="Additional space configuration updates",
                required=False
            )
        ]
    )
    def update_meeting_space(
        self,
        space_name: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        space_config: Optional[dict] = None
    ) -> tuple[bool, str]:
        """Update an existing Google Meet space"""
        """
        Args:
            space_name: Resource name of the space to update
            title: New meeting title/display name (not supported by API - will be ignored)
            description: New meeting description (not supported by API - will be ignored)
            space_config: Additional space configuration updates
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Note: Google Meet Spaces API does not support displayName/description fields
            if title or description:
                return False, json.dumps({
                    "error": "Google Meet Spaces API does not support updating title/description. Use schedule_meeting_with_calendar to create meetings with custom titles.",
                    "note": "Only spaceConfig updates are supported for Meet spaces"
                })

            if not space_config:
                return False, json.dumps({"error": "No updates provided. Only space_config updates are supported."})

            # Use GoogleMeetDataSource method - only update spaceConfig
            updated_space = self._run_async(self.client.spaces_patch(
                name=space_name,
                updateMask="spaceConfig",
                body={"spaceConfig": space_config}
            ))

            return True, json.dumps({
                "space_name": space_name,
                "updated_space": updated_space,
                "message": "Meeting space configuration updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to update meeting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_meeting_space",
        parameters=[
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Resource name of the space (e.g., 'spaces/{space}' or 'spaces/{meetingCode}')",
                required=True
            )
        ]
    )
    def get_meeting_space(self, space_name: str) -> tuple[bool, str]:
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
        app_name="meet",
        tool_name="end_active_conference",
        parameters=[
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Resource name of the space",
                required=True
            )
        ]
    )
    def end_active_conference(self, space_name: str) -> tuple[bool, str]:
        """End an active conference in a meeting space"""
        """
        Args:
            space_name: Resource name of the space
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            result = self._run_async(self.client.spaces_end_active_conference(name=space_name))

            return True, json.dumps({
                "message": f"Active conference ended for space {space_name}",
                "result": result
            })
        except Exception as e:
            logger.error(f"Failed to end active conference: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_records",
        parameters=[
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of conference records to return (default: 25, max: 100)",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Raw filter in EBNF format. If provided, it will be normalized to API-expected snake_case fields and double quotes.",
                required=False
            ),
            ToolParameter(
                name="start_time_from",
                type=ParameterType.STRING,
                description="ISO8601 UTC start time lower bound for records, e.g., 2025-01-01T00:00:00Z",
                required=False
            ),
            ToolParameter(
                name="start_time_to",
                type=ParameterType.STRING,
                description="ISO8601 UTC start time upper bound for records, e.g., 2025-01-02T00:00:00Z",
                required=False
            ),
            ToolParameter(
                name="meeting_code",
                type=ParameterType.STRING,
                description="Filter by specific meeting code",
                required=False
            ),
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Filter by specific space name",
                required=False
            ),
            ToolParameter(
                name="include_active_only",
                type=ParameterType.BOOLEAN,
                description="Include only conferences that are currently active (end_time IS NULL)",
                required=False
            )
        ]
    )
    def get_conference_records(
        self,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        filter: Optional[str] = None,
        start_time_from: Optional[str] = None,
        start_time_to: Optional[str] = None,
        meeting_code: Optional[str] = None,
        space_name: Optional[str] = None,
        include_active_only: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get list of conference records with enhanced filtering options"""
        """
        Args:
            page_size: Maximum number of records to return
            page_token: Page token for pagination
            filter: Filter condition
            start_time_from: Lower bound for start_time (inclusive)
            start_time_to: Upper bound for start_time (inclusive)
            meeting_code: Filter by specific meeting code
            space_name: Filter by specific space name
            include_active_only: Include only active conferences
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Build or normalize filter to match Google Meet API expectations
            effective_filter = None
            if filter:
                effective_filter = self._normalize_meet_filter(filter)
            else:
                # Construct filter from helper parameters if provided
                conditions = []
                if start_time_from:
                    conditions.append(f'start_time>="{start_time_from}"')
                if start_time_to:
                    conditions.append(f'start_time<="{start_time_to}"')
                if meeting_code:
                    conditions.append(f'space.meeting_code="{meeting_code}"')
                if space_name:
                    conditions.append(f'space.name="{space_name}"')
                if include_active_only:
                    conditions.append('end_time IS NULL')
                if conditions:
                    effective_filter = " AND ".join(conditions)

            # Use GoogleMeetDataSource method
            records = self._run_async(self.client.conference_records_list(
                pageSize=page_size,
                pageToken=page_token,
                filter=effective_filter
            ))

            # Enhance response with summary information
            enhanced_response = {
                "conference_records": records.get("conferenceRecords", []),
                "next_page_token": records.get("nextPageToken"),
                "total_count": len(records.get("conferenceRecords", [])),
                "filter_applied": effective_filter,
                "summary": {
                    "active_conferences": len([r for r in records.get("conferenceRecords", []) if not r.get("endTime")]),
                    "completed_conferences": len([r for r in records.get("conferenceRecords", []) if r.get("endTime")])
                }
            }

            return True, json.dumps(enhanced_response)
        except Exception as e:
            logger.error(f"Failed to get conference records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_record_details",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            )
        ]
    )
    def get_conference_record_details(self, conference_record: str) -> tuple[bool, str]:
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
        app_name="meet",
        tool_name="get_conference_participants",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of participants to return (default: 100, max: 250)",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter condition for participants (e.g., 'latest_end_time IS NULL' for active participants)",
                required=False
            ),
            ToolParameter(
                name="include_active_only",
                type=ParameterType.BOOLEAN,
                description="Include only currently active participants",
                required=False
            )
        ]
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

    @tool(
        app_name="meet",
        tool_name="get_conference_recordings",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of recordings to return",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            )
        ]
    )
    def get_conference_recordings(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get recordings from a conference record"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of recordings
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            recordings = self._run_async(self.client.conference_records_recordings_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token
            ))

            return True, json.dumps(recordings)
        except Exception as e:
            logger.error(f"Failed to get conference recordings: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_transcripts",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of transcripts to return (default: 10, max: 100)",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            ),
            ToolParameter(
                name="include_entries",
                type=ParameterType.BOOLEAN,
                description="Include transcript entries for each transcript",
                required=False
            )
        ]
    )
    def get_conference_transcripts(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        include_entries: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get transcripts from a conference record with optional entries"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of transcripts
            page_token: Page token for pagination
            include_entries: Include transcript entries for each transcript
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            transcripts = self._run_async(self.client.conference_records_transcripts_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token
            ))

            enhanced_response = {
                "transcripts": transcripts.get("transcripts", []),
                "next_page_token": transcripts.get("nextPageToken"),
                "total_count": len(transcripts.get("transcripts", []))
            }

            # Optionally include transcript entries
            if include_entries:
                transcript_entries = {}
                for transcript in transcripts.get("transcripts", []):
                    transcript_name = transcript.get("name", "")
                    if transcript_name:
                        try:
                            entries = self._run_async(self.client.conference_records_transcripts_entries_list(
                                parent=transcript_name,
                                pageSize=100  # Get all entries for each transcript
                            ))
                            transcript_entries[transcript_name] = entries.get("transcriptEntries", [])
                        except Exception as e:
                            logger.warning(f"Failed to get entries for transcript {transcript_name}: {e}")
                            transcript_entries[transcript_name] = []

                enhanced_response["transcript_entries"] = transcript_entries

            return True, json.dumps(enhanced_response)
        except Exception as e:
            logger.error(f"Failed to get conference transcripts: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_transcript_entries",
        parameters=[
            ToolParameter(
                name="transcript_name",
                type=ParameterType.STRING,
                description="Transcript name (e.g., 'conferenceRecords/{conference_record}/transcripts/{transcript}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of entries to return (default: 10, max: 100)",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            )
        ]
    )
    def get_transcript_entries(
        self,
        transcript_name: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get transcript entries from a specific transcript"""
        """
        Args:
            transcript_name: Transcript name
            page_size: Maximum number of entries
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            entries = self._run_async(self.client.conference_records_transcripts_entries_list(
                parent=transcript_name,
                pageSize=page_size,
                pageToken=page_token
            ))

            return True, json.dumps(entries)
        except Exception as e:
            logger.error(f"Failed to get transcript entries: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_meeting_summary",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="include_participants",
                type=ParameterType.BOOLEAN,
                description="Include participant information",
                required=False
            ),
            ToolParameter(
                name="include_recordings",
                type=ParameterType.BOOLEAN,
                description="Include recording information",
                required=False
            ),
            ToolParameter(
                name="include_transcripts",
                type=ParameterType.BOOLEAN,
                description="Include transcript information",
                required=False
            )
        ]
    )
    def get_meeting_summary(
        self,
        conference_record: str,
        include_participants: Optional[bool] = None,
        include_recordings: Optional[bool] = None,
        include_transcripts: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get comprehensive meeting summary including participants, recordings, and transcripts"""
        """
        Args:
            conference_record: Conference record name
            include_participants: Include participant information
            include_recordings: Include recording information
            include_transcripts: Include transcript information
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Get conference record details
            record = self._run_async(self.client.conference_records_get(name=conference_record))

            summary = {
                "conference_record": record,
                "meeting_info": {
                    "name": record.get("name"),
                    "start_time": record.get("startTime"),
                    "end_time": record.get("endTime"),
                    "space": record.get("space", {}),
                    "duration_minutes": None
                }
            }

            # Calculate duration if both start and end times are available
            if record.get("startTime") and record.get("endTime"):
                from datetime import datetime
                start_time = datetime.fromisoformat(record["startTime"].replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(record["endTime"].replace('Z', '+00:00'))
                duration = end_time - start_time
                summary["meeting_info"]["duration_minutes"] = int(duration.total_seconds() / 60)

            # Include participants if requested
            if include_participants:
                try:
                    participants = self._run_async(self.client.conference_records_participants_list(
                        parent=conference_record,
                        pageSize=250
                    ))
                    summary["participants"] = participants.get("participants", [])
                    summary["participant_count"] = len(participants.get("participants", []))
                except Exception as e:
                    logger.warning(f"Failed to get participants: {e}")
                    summary["participants_error"] = str(e)

            # Include recordings if requested
            if include_recordings:
                try:
                    recordings = self._run_async(self.client.conference_records_recordings_list(
                        parent=conference_record,
                        pageSize=100
                    ))
                    summary["recordings"] = recordings.get("recordings", [])
                    summary["recording_count"] = len(recordings.get("recordings", []))
                except Exception as e:
                    logger.warning(f"Failed to get recordings: {e}")
                    summary["recordings_error"] = str(e)

            # Include transcripts if requested
            if include_transcripts:
                try:
                    transcripts = self._run_async(self.client.conference_records_transcripts_list(
                        parent=conference_record,
                        pageSize=100
                    ))
                    summary["transcripts"] = transcripts.get("transcripts", [])
                    summary["transcript_count"] = len(transcripts.get("transcripts", []))
                except Exception as e:
                    logger.warning(f"Failed to get transcripts: {e}")
                    summary["transcripts_error"] = str(e)

            return True, json.dumps(summary)
        except Exception as e:
            logger.error(f"Failed to get meeting summary: {e}")
            return False, json.dumps({"error": str(e)})
