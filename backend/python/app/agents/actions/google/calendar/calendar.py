import ast
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.google_api_errors import GoogleToolWording, google_error_message
from app.agents.actions.util.tool_summaries import entity_summary, list_summary
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
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource

logger = logging.getLogger(__name__)


def _calendar_event_label(event: dict) -> str:
    return event.get("summary") or event.get("id") or "?"


class _CalendarInputError(ValueError):
    """Bad tool arguments; the message says what to change and is safe to show the agent."""


_CALENDAR_WORDING = GoogleToolWording(
    product="Google Calendar",
    toolset="Calendar",
    access="calendar access",
    not_found=(
        "that event or calendar. Check the id, or call get_calendar_events or get_calendar_list "
        "to find the right one."
    ),
    gone="that event has already been deleted from Google Calendar.",
)


def _calendar_error_message(error: Exception, action: str) -> str:
    if isinstance(error, _CalendarInputError):
        return str(error)
    return google_error_message(error, action, _CALENDAR_WORDING)


def _calendar_failure(error: Exception, action: str) -> tuple[bool, str]:
    logger.error("Failed to %s: %s", action, error)
    return False, json.dumps({"error": _calendar_error_message(error, action)})


_EPOCH_MIN_DIGITS = 9
_EPOCH_MS_DIGITS = 13


def _parse_time(value: str, zone: ZoneInfo) -> datetime:
    """ISO 8601 or a Unix timestamp; a time without an offset is read in ``zone``, not the server's clock."""
    text = str(value).strip()
    if text.isdigit() and _EPOCH_MIN_DIGITS <= len(text) <= _EPOCH_MS_DIGITS:
        seconds = int(text) / 1000 if len(text) >= _EPOCH_MS_DIGITS else int(text)
        # Same instant, expressed in the event's zone so an all-day date is that zone's date.
        return datetime.fromtimestamp(seconds, tz=zone)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise _CalendarInputError(
            f"'{value}' is not a date and time Google Calendar can read. Use ISO 8601, "
            "for example '2026-09-30T14:00:00' or '2026-09-30T14:00:00+05:30'."
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


def _zone(name: str | None) -> tuple[str, ZoneInfo]:
    zone_name = (name or "UTC").strip() or "UTC"
    try:
        return zone_name, ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise _CalendarInputError(
            f"'{name}' is not a time zone Google Calendar understands. Use an IANA name such as "
            "'America/New_York', 'Europe/London' or 'UTC'."
        ) from None


def _event_times(start: str, end: str, zone_name: str | None, *, all_day: bool = False) -> tuple[dict, dict]:
    """Google event ``start``/``end`` objects; the zone goes on each, where the API reads it."""
    zone_name, zone = _zone(zone_name)
    start_dt, end_dt = _parse_time(start, zone), _parse_time(end, zone)
    if end_dt < start_dt:
        raise _CalendarInputError(
            "The event must end after it starts. Check the start and end times and try again."
        )
    if all_day:
        start_day, end_day = start_dt.date(), end_dt.date()
        # Google's all-day end date is exclusive, so a one-day event ends the next day.
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        return {"date": start_day.isoformat()}, {"date": end_day.isoformat()}
    return (
        {"dateTime": start_dt.isoformat(), "timeZone": zone_name},
        {"dateTime": end_dt.isoformat(), "timeZone": zone_name},
    )


def _meeting_link(event: dict) -> str:
    """The video entry point's URL, else the legacy hangoutLink; empty when the event has none."""
    conference = event.get("conferenceData")
    entry_points = conference.get("entryPoints") if isinstance(conference, dict) else None
    for entry in entry_points if isinstance(entry_points, list) else []:
        if isinstance(entry, dict) and entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return event.get("hangoutLink") or ""


def _event_when(part: object) -> str:
    """A timed event has ``dateTime``; an all-day one has only ``date``."""
    if not isinstance(part, dict):
        return ""
    return part.get("dateTime") or part.get("date") or ""


# Pydantic schemas for Google Calendar tools
class GetCalendarEventsInput(BaseModel):
    """Schema for getting calendar events"""
    calendar_id: Optional[str] = Field(default=None, description="The ID of the calendar to use (default: 'primary')")
    max_results: Optional[int] = Field(default=None, description="Maximum number of events to return")
    time_min: Optional[str] = Field(default=None, description="Lower bound for event start time (RFC3339 format)")
    time_max: Optional[str] = Field(default=None, description="Upper bound for event start time (RFC3339 format)")
    order_by: Optional[str] = Field(default=None, description="Order by (e.g., 'startTime' or 'updated')")
    single_events: Optional[bool] = Field(default=None, description="Whether to expand recurring events into instances")
    query: Optional[str] = Field(default=None, description="Free text search terms to find events")
    show_deleted: Optional[bool] = Field(default=None, description="Include deleted events")
    time_zone: Optional[str] = Field(default=None, description="Time zone used in the response")


def _coerce_attendees_emails(
    v: Union[None, str, List[object]],
) -> Optional[List[str]]:
    """
    Accept multiple shapes for attendee lists that can arrive from cascading tool
    placeholder resolution:
      - None / already List[str]  → pass through
      - List[dict] with 'email'   → extract the email strings
                   (e.g. Google Calendar attendees: [{'email': '...', 'responseStatus': '...'}])
      - str repr of a list        → ast.literal_eval then recurse (safety net)
    """
    if v is None:
        return v
    if isinstance(v, str):
        try:
            parsed = ast.literal_eval(v)
            if isinstance(parsed, list):
                return _coerce_attendees_emails(parsed)
        except (ValueError, SyntaxError):
            pass
        return [v]  # treat bare string as a single email
    if isinstance(v, list):
        emails: List[str] = []
        for item in v:
            if isinstance(item, dict):
                email = item.get("email") or item.get("emailAddress") or item.get("email_address")
                if email and isinstance(email, str):
                    emails.append(email)
            elif isinstance(item, str):
                emails.append(item)
        return emails or None
    return v  # type: ignore[return-value]


class CreateCalendarEventInput(BaseModel):
    """Schema for creating a calendar event"""
    event_start_time: str = Field(description="The start time of the event (ISO format or timestamp)")
    event_end_time: str = Field(description="The end time of the event (ISO format or timestamp)")
    event_title: Optional[str] = Field(default=None, description="The title/summary of the event")
    event_description: Optional[str] = Field(default=None, description="The description of the event")
    event_location: Optional[str] = Field(default=None, description="The location of the event")
    event_organizer: Optional[str] = Field(default=None, description="The email of the event organizer")
    event_attendees_emails: Optional[List[str]] = Field(default=None, description="List of email addresses for event attendees")
    event_meeting_link: Optional[str] = Field(default=None, description="The meeting link/URL for the event")
    event_timezone: Optional[str] = Field(default="UTC", description="The timezone for the event")
    event_all_day: Optional[bool] = Field(default=False, description="Whether the event is an all-day event")

    @field_validator("event_attendees_emails", mode="before")
    @classmethod
    def coerce_attendees(cls, v: object) -> Optional[List[str]]:
        return _coerce_attendees_emails(v)  # type: ignore[arg-type]


class UpdateCalendarEventInput(BaseModel):
    """Schema for updating a calendar event"""
    event_id: str = Field(description="The actual event ID from Google Calendar")
    event_title: Optional[str] = Field(default=None, description="The new title/summary for the event")
    event_description: Optional[str] = Field(default=None, description="The new description for the event")
    event_start_time: Optional[str] = Field(default=None, description="The new start time for the event (ISO format or timestamp)")
    event_end_time: Optional[str] = Field(default=None, description="The new end time for the event (ISO format or timestamp)")
    event_location: Optional[str] = Field(default=None, description="The new location for the event")
    event_organizer: Optional[str] = Field(default=None, description="The new organizer email for the event")
    event_attendees_emails: Optional[List[str]] = Field(default=None, description="The new list of attendee emails for the event")
    event_meeting_link: Optional[str] = Field(default=None, description="The new meeting link/URL for the event")
    event_timezone: Optional[str] = Field(default="UTC", description="The new timezone for the event")
    event_all_day: Optional[bool] = Field(default=False, description="Whether the event should be an all-day event")

    @field_validator("event_attendees_emails", mode="before")
    @classmethod
    def coerce_attendees(cls, v: object) -> Optional[List[str]]:
        return _coerce_attendees_emails(v)  # type: ignore[arg-type]


class DeleteCalendarEventInput(BaseModel):
    """Schema for deleting a calendar event"""
    event_id: str = Field(description="The actual event ID from Google Calendar")


class GetCalendarListByIdInput(BaseModel):
    """Schema for getting a calendar by ID"""
    calendar_id: Optional[str] = Field(default=None, description="The ID of the calendar to get (default: 'primary')")


class CreateMeetLinkInput(BaseModel):
    """Schema for creating a Google Meet link and attaching it to a calendar event"""
    event_start_time: str = Field(description="The start time of the event (ISO format or timestamp)")
    event_end_time: str = Field(description="The end time of the event (ISO format or timestamp)")
    event_title: Optional[str] = Field(default=None, description="The title/summary of the event")
    event_description: Optional[str] = Field(default=None, description="The description of the event")
    event_location: Optional[str] = Field(default=None, description="The location of the event")
    event_attendees_emails: Optional[List[str]] = Field(default=None, description="List of email addresses for event attendees")
    event_timezone: Optional[str] = Field(default="UTC", description="The timezone for the event")

    @field_validator("event_attendees_emails", mode="before")
    @classmethod
    def coerce_attendees(cls, v: object) -> Optional[List[str]]:
        return _coerce_attendees_emails(v)  # type: ignore[arg-type]


# Register Google Calendar toolset
@ToolsetBuilder("Calendar")\
    .in_group("Google Workspace")\
    .with_description("Google Calendar integration for event management and scheduling")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Calendar",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/calendar",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "https://www.googleapis.com/auth/calendar",
                    "https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/gmail.send"
                ]
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path=IconPaths.connector_icon("calendar"),
            app_group="Google Workspace",
            app_description="Calendar OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("calendar"))
        .add_documentation_link(DocumentationLink(
            "Calendar API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/google-workspace/calendar",
            "pipeshub",
        )))\
    .build_decorator()
class GoogleCalendar:
    """Calendar tool exposed to the agents using CalendarDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Calendar tool"""
        """
        Args:
            client: Calendar client
        Returns:
            None
        """
        self.client = GoogleCalendarDataSource(client)


    @tool(
        path="/tools/calendar/get_calendar_events",
        short_description="Get upcoming calendar events",
        description=(
            "Retrieve calendar events from Google Calendar with optional filters. "
            "Supports filtering by time range, search query, calendar ID, and ordering."
        ),
        parameters=[
            ToolParameter(name="calendar_id", type=ParameterType.STRING, description="The ID of the calendar to use (default: 'primary')", required=False),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Maximum number of events to return", required=False),
            ToolParameter(name="time_min", type=ParameterType.STRING, description="Lower bound for event start time (RFC3339 format)", required=False),
            ToolParameter(name="time_max", type=ParameterType.STRING, description="Upper bound for event start time (RFC3339 format)", required=False),
            ToolParameter(name="order_by", type=ParameterType.STRING, description="Order by (e.g., 'startTime' or 'updated')", required=False),
            ToolParameter(name="single_events", type=ParameterType.BOOLEAN, description="Whether to expand recurring events into instances", required=False),
            ToolParameter(name="query", type=ParameterType.STRING, description="Free text search terms to find events", required=False),
            ToolParameter(name="show_deleted", type=ParameterType.BOOLEAN, description="Include deleted events", required=False),
            ToolParameter(name="time_zone", type=ParameterType.STRING, description="Time zone used in the response", required=False),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="read")],
        args_summary=lambda args: (
            f'Searching calendar: "{args["query"]}"' if args.get("query") else "Fetching calendar events"
        ),
        result_summary=list_summary(("items",), _calendar_event_label, "event"),
    )
    async def get_calendar_events(
        self,
        calendar_id: Optional[str] = None,
        max_results: Optional[int] = None,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        order_by: Optional[str] = None,
        single_events: Optional[bool] = None,
        query: Optional[str] = None,
        show_deleted: Optional[bool] = None,
        time_zone: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Get calendar events"""
        """
        Args:
            calendar_id: The ID of the calendar to use
            max_results: Maximum number of events to return
            time_min: Lower bound for event start time
            time_max: Upper bound for event start time
            order_by: Order by key
            single_events: Expand recurring events
            query: Free text search
            show_deleted: Include deleted events
            time_zone: Time zone for response
        Returns:
            tuple[bool, str]: True if the events are fetched, False otherwise
        """
        try:
            _, zone = _zone(time_zone)
            events = await self.client.events_list(
                calendarId=calendar_id or "primary",
                maxResults=max_results,
                timeMin=_parse_time(time_min, zone).isoformat() if time_min else None,
                timeMax=_parse_time(time_max, zone).isoformat() if time_max else None,
                orderBy=order_by,
                singleEvents=single_events,
                q=query,
                showDeleted=show_deleted,
                timeZone=time_zone,
            )

            # Normalize events to ensure location field is always present
            # This prevents placeholder resolution errors when location is missing
            if isinstance(events, dict) and "items" in events:
                for item in events.get("items", []):
                    # Ensure location field is always present (empty string if not set)
                    if "location" not in item:
                        item["location"] = ""

            return True, json.dumps(events)
        except Exception as e:
            return _calendar_failure(e, "get calendar events")


    @tool(
        path="/tools/calendar/create_calendar_event",
        short_description="Create a new calendar event",
        description=(
            "Create a new event on Google Calendar. Supports setting title, description, location, "
            "attendees, meeting link, timezone, and all-day events. Sends invite emails to all attendees."
        ),
        parameters=[
            ToolParameter(name="event_start_time", type=ParameterType.STRING, description="The start time of the event (ISO format or timestamp)", required=True),
            ToolParameter(name="event_end_time", type=ParameterType.STRING, description="The end time of the event (ISO format or timestamp)", required=True),
            ToolParameter(name="event_title", type=ParameterType.STRING, description="The title/summary of the event", required=False),
            ToolParameter(name="event_description", type=ParameterType.STRING, description="The description of the event", required=False),
            ToolParameter(name="event_location", type=ParameterType.STRING, description="The location of the event", required=False),
            ToolParameter(name="event_organizer", type=ParameterType.STRING, description="The email of the event organizer", required=False),
            ToolParameter(name="event_attendees_emails", type=ParameterType.LIST, description="List of email addresses for event attendees", required=False),
            ToolParameter(name="event_meeting_link", type=ParameterType.STRING, description="The meeting link/URL for the event", required=False),
            ToolParameter(name="event_timezone", type=ParameterType.STRING, description="The timezone for the event", required=False, default="UTC"),
            ToolParameter(name="event_all_day", type=ParameterType.BOOLEAN, description="Whether the event is an all-day event", required=False, default=False),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="write")],
        args_summary=lambda args: (
            f'Creating calendar event "{args["event_title"]}"' if args.get("event_title") else "Creating calendar event"
        ),
        result_summary=entity_summary(lambda e: f"Event created: {e.get('event_title') or '?'}", path=()),
    )
    async def create_calendar_event(
        self,
        event_start_time: str,
        event_end_time: str,
        event_title: Optional[str] = None,
        event_description: Optional[str] = None,
        event_location: Optional[str] = None,
        event_organizer: Optional[str] = None,
        event_attendees_emails: Optional[List[str]] = None,
        event_meeting_link: Optional[str] = None,
        event_timezone: str = "UTC",
        event_all_day: bool = False,
    ) -> tuple[bool, str]:
        """Create a calendar event"""
        """
        Args:
            event_start_time: The start time of the event
            event_end_time: The end time of the event
            event_title: The title of the event
            event_description: The description of the event
            event_location: The location of the event
            event_organizer: The organizer of the event
            event_attendees_emails: The attendees of the event
            event_meeting_link: The meeting link of the event
            event_timezone: The timezone of the event
            event_all_day: Whether the event is all day
        Returns:
            tuple[bool, str]: True if the event is created, False otherwise
        """
        try:
            if not event_start_time:
                return False, json.dumps({"error": "Event start time is required"})
            if not event_end_time:
                return False, json.dumps({"error": "Event end time is required"})

            start, end = _event_times(event_start_time, event_end_time, event_timezone, all_day=bool(event_all_day))

            event_config = {
                "summary": event_title,
                "description": event_description,
                "start": start,
                "end": end,
                "location": event_location,
                "organizer": {
                    "email": event_organizer,
                },
                "attendees": [{"email": email} for email in event_attendees_emails] if event_attendees_emails else [],
            }

            if event_meeting_link:
                event_config["conferenceData"] = {
                    "createRequest": {
                        "requestId": event_meeting_link,
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet",
                        },
                    },
                }

            # Use GoogleCalendarDataSource method
            # sendUpdates="all" ensures Google sends invite emails to all attendees
            event = await self.client.events_insert(
                calendarId="primary",
                body=event_config,
                sendUpdates="all"
            )

            return True, json.dumps({
                "event_id": event.get("id", ""),
                "event_title": event.get("summary", ""),
                "event_start_time": _event_when(event.get("start")),
                "event_end_time": _event_when(event.get("end")),
                "event_location": event.get("location", ""),
                "event_organizer": event.get("organizer", {}).get("email", ""),
                "event_attendees": event.get("attendees", []),
                "event_meeting_link": _meeting_link(event),
                "event_timezone": event.get("timeZone", ""),
                "event_all_day": event_all_day,
            })
        except Exception as e:
            return _calendar_failure(e, "create the calendar event")

    @tool(
        path="/tools/calendar/update_calendar_event",
        short_description="Update a calendar event",
        description=(
            "Update an existing Google Calendar event. Supports modifying title, description, time, "
            "location, attendees, meeting link, and timezone. Sends update notifications to all attendees."
        ),
        parameters=[
            ToolParameter(name="event_id", type=ParameterType.STRING, description="The actual event ID from Google Calendar", required=True),
            ToolParameter(name="event_title", type=ParameterType.STRING, description="The new title/summary for the event", required=False),
            ToolParameter(name="event_description", type=ParameterType.STRING, description="The new description for the event", required=False),
            ToolParameter(name="event_start_time", type=ParameterType.STRING, description="The new start time for the event (ISO format or timestamp)", required=False),
            ToolParameter(name="event_end_time", type=ParameterType.STRING, description="The new end time for the event (ISO format or timestamp)", required=False),
            ToolParameter(name="event_location", type=ParameterType.STRING, description="The new location for the event", required=False),
            ToolParameter(name="event_organizer", type=ParameterType.STRING, description="The new organizer email for the event", required=False),
            ToolParameter(name="event_attendees_emails", type=ParameterType.LIST, description="The new list of attendee emails for the event", required=False),
            ToolParameter(name="event_meeting_link", type=ParameterType.STRING, description="The new meeting link/URL for the event", required=False),
            ToolParameter(name="event_timezone", type=ParameterType.STRING, description="The new timezone for the event", required=False, default="UTC"),
            ToolParameter(name="event_all_day", type=ParameterType.BOOLEAN, description="Whether the event should be an all-day event", required=False, default=False),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="write")],
    )
    async def update_calendar_event(
        self,
        event_id: str,
        event_title: Optional[str] = None,
        event_description: Optional[str] = None,
        event_start_time: Optional[str] = None,
        event_end_time: Optional[str] = None,
        event_location: Optional[str] = None,
        event_organizer: Optional[str] = None,
        event_attendees_emails: Optional[List[str]] = None,
        event_meeting_link: Optional[str] = None,
        event_timezone: str = "UTC",
        event_all_day: bool = False,
    ) -> tuple[bool, str]:
        """Update a calendar event"""
        """
        Args:
            event_id: The ID of the event to update
            event_title: The new title of the event
            event_description: The new description of the event
            event_start_time: The new start time of the event
            event_end_time: The new end time of the event
            event_location: The new location of the event
            event_organizer: The new organizer of the event
            event_attendees_emails: The new attendees of the event
            event_meeting_link: The new meeting link of the event
            event_timezone: The new timezone of the event
            event_all_day: Whether the event is all day
        Returns:
            tuple[bool, str]: True if the event is updated, False otherwise
        """
        if bool(event_start_time) != bool(event_end_time):
            return False, json.dumps({
                "error": "To move an event, give both the new start time and the new end time."
            })
        try:
            new_times = (
                _event_times(event_start_time, event_end_time, event_timezone, all_day=bool(event_all_day))
                if event_start_time and event_end_time
                else None
            )
            # Use GoogleCalendarDataSource method to get event
            event = await self.client.events_get(
                calendarId="primary",
                eventId=event_id
            )

            if event_title:
                event["summary"] = event_title
            if event_description:
                event["description"] = event_description
            if event_location:
                event["location"] = event_location
            if event_organizer:
                event["organizer"] = {"email": event_organizer}
            if event_attendees_emails:
                event["attendees"] = [{"email": email} for email in event_attendees_emails]
            if event_meeting_link:
                event["conferenceData"] = {
                    "entryPoints": [
                        {
                            "entryPointType": "video",
                            "uri": event_meeting_link,
                        }
                    ],
                }
            if new_times:
                event["start"], event["end"] = new_times

            # Use GoogleCalendarDataSource method to update event
            # sendUpdates="all" ensures Google sends update notification emails to all attendees
            updated_event = await self.client.events_update(
                calendarId="primary",
                eventId=event_id,
                body=event,
                sendUpdates="all"
            )

            return True, json.dumps({
                "success": True,
                "message": f"Event updated successfully! Event ID: {updated_event.get('id', '')}",
                "event_id": updated_event.get("id", ""),
                "event_title": updated_event.get("summary", ""),
                "event_start_time": _event_when(updated_event.get("start")),
                "event_end_time": _event_when(updated_event.get("end")),
                "event_location": updated_event.get("location", ""),
                "event_organizer": updated_event.get("organizer", {}).get("email", ""),
                "event_attendees": updated_event.get("attendees", []),
                "event_meeting_link": _meeting_link(updated_event),
                "event_timezone": updated_event.get("timeZone", ""),
                "event_all_day": event_all_day,
            })
        except Exception as e:
            return _calendar_failure(e, "update the calendar event")

    @tool(
        path="/tools/calendar/create_meet_link",
        short_description="Create a Google Meet link with a calendar event",
        description=(
            "Create a Google Meet link and attach it to a new calendar event. "
            "Uses the Google Calendar API conference data feature to auto-generate a Meet room "
            "and returns the link along with event details. Sends invite emails to all attendees."
        ),
        parameters=[
            ToolParameter(name="event_start_time", type=ParameterType.STRING, description="The start time of the event (ISO format or timestamp)", required=True),
            ToolParameter(name="event_end_time", type=ParameterType.STRING, description="The end time of the event (ISO format or timestamp)", required=True),
            ToolParameter(name="event_title", type=ParameterType.STRING, description="The title/summary of the event", required=False),
            ToolParameter(name="event_description", type=ParameterType.STRING, description="The description of the event", required=False),
            ToolParameter(name="event_location", type=ParameterType.STRING, description="The location of the event", required=False),
            ToolParameter(name="event_attendees_emails", type=ParameterType.LIST, description="List of email addresses for event attendees", required=False),
            ToolParameter(name="event_timezone", type=ParameterType.STRING, description="The timezone for the event", required=False, default="UTC"),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="write")],
    )
    async def create_meet_link(
        self,
        event_start_time: str,
        event_end_time: str,
        event_title: Optional[str] = None,
        event_description: Optional[str] = None,
        event_location: Optional[str] = None,
        event_attendees_emails: Optional[List[str]] = None,
        event_timezone: Optional[str] = "UTC",
    ) -> tuple[bool, str]:
        """Create a Google Meet link and attach it to a new calendar event.

        Uses the Google Calendar API conference data feature (conferenceDataVersion=1)
        to auto-generate a Google Meet room and return the link along with event details.

        Args:
            event_start_time: The start time of the event
            event_end_time: The end time of the event
            event_title: The title of the event
            event_description: The description of the event
            event_location: The location of the event
            event_attendees_emails: The attendees of the event
            event_timezone: The timezone of the event
        Returns:
            tuple[bool, str]: True if the event and Meet link were created, False otherwise
        """
        try:
            if not event_start_time:
                return False, json.dumps({"error": "Event start time is required"})
            if not event_end_time:
                return False, json.dumps({"error": "Event end time is required"})

            start, end = _event_times(event_start_time, event_end_time, event_timezone)

            event_config = {
                "summary": event_title or "Meeting",
                "description": event_description,
                "start": start,
                "end": end,
                "location": event_location,
                "attendees": [{"email": email} for email in event_attendees_emails] if event_attendees_emails else [],
                # conferenceData.createRequest tells the Calendar API to auto-generate a Meet room
                "conferenceData": {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet"
                        }
                    }
                }
            }

            # conferenceDataVersion=1 is required for the Calendar API to create the Meet room
            # sendUpdates="all" ensures Google sends invite emails to all attendees
            event = await self.client.events_insert(
                calendarId="primary",
                body=event_config,
                conferenceDataVersion=1,
                sendUpdates="all"
            )

            meet_link = _meeting_link(event)

            return True, json.dumps({
                "success": True,
                "event_id": event.get("id", ""),
                "event_title": event.get("summary", ""),
                "event_start_time": _event_when(event.get("start")),
                "event_end_time": _event_when(event.get("end")),
                "event_location": event.get("location", ""),
                "meet_link": meet_link,
                "event_attendees": event.get("attendees", []),
                "message": f"Google Meet link created and attached to calendar event. Meet link: {meet_link}"
            })
        except Exception as e:
            return _calendar_failure(e, "create the Meet link")

    @tool(
        path="/tools/calendar/delete_calendar_event",
        short_description="Delete a calendar event",
        description="Delete an existing event from Google Calendar by its event ID.",
        parameters=[
            ToolParameter(name="event_id", type=ParameterType.STRING, description="The actual event ID from Google Calendar", required=True),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="write")],
    )
    async def delete_calendar_event(
        self,
        event_id: str,
    ) -> tuple[bool, str]:
        """Delete a calendar event"""
        """
        Args:
            event_id: The ID of the event to delete
        Returns:
            tuple[bool, str]: True if the event is deleted, False otherwise
        """
        try:
            # Use GoogleCalendarDataSource method
            await self.client.events_delete(
                calendarId="primary",
                eventId=event_id
            )

            return True, json.dumps({
                "message": f"Event {event_id} deleted successfully"
            })
        except Exception as e:
            return _calendar_failure(e, "delete the calendar event")

    @tool(
        path="/tools/calendar/get_calendar_list",
        short_description="List all calendars",
        description="Retrieve the list of all available Google Calendars for the authenticated user.",
        parameters=[],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="read")],
    )
    async def get_calendar_list(self) -> tuple[bool, str]:
        """Get the list of available calendars"""
        """
        Returns:
            tuple[bool, str]: True if the calendar list is retrieved, False otherwise
        """
        try:
            # Use GoogleCalendarDataSource method
            calendars = await self.client.calendar_list_list()
            return True, json.dumps(calendars)
        except Exception as e:
            return _calendar_failure(e, "list your calendars")

    @tool(
        path="/tools/calendar/get_calendar_list_by_id",
        short_description="Get a specific calendar by ID",
        description="Retrieve details of a specific Google Calendar by its ID.",
        parameters=[
            ToolParameter(name="calendar_id", type=ParameterType.STRING, description="The ID of the calendar to get (default: 'primary')", required=False),
        ],
        tags=[Tag(key="category", value="calendar"), Tag(key="type", value="read")],
    )
    async def get_calendar_list_by_id(
        self,
        calendar_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get the current calendar by ID"""
        """
        Args:
            calendar_id: The ID of the calendar to get
        Returns:
            tuple[bool, str]: True if the calendar is retrieved, False otherwise
        """
        try:
            # Use GoogleCalendarDataSource method
            calendar = await self.client.calendars_get(
                calendarId=calendar_id or "primary"
            )
            return True, json.dumps(calendar)
        except Exception as e:
            return _calendar_failure(e, "get the calendar")
