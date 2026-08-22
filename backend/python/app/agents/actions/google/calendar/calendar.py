import ast
import json
import logging
import uuid
from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
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
from app.utils.time_conversion import prepare_iso_timestamps

logger = logging.getLogger(__name__)


def _calendar_event_label(event: dict) -> str:
    return event.get("summary") or event.get("id") or "?"


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
            events = await self.client.events_list(
                calendarId=calendar_id or "primary",
                maxResults=max_results,
                timeMin=time_min,
                timeMax=time_max,
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
            logger.error(f"Failed to get calendar events: {e}")
            return False, json.dumps({"error": str(e)})


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

            event_start_time_iso, event_end_time_iso = prepare_iso_timestamps(event_start_time, event_end_time)

            event_config = {
                "summary": event_title,
                "description": event_description,
                "start": {
                    "dateTime": event_start_time_iso,
                },
                "end": {
                    "dateTime": event_end_time_iso,
                },
                "location": event_location,
                "organizer": {
                    "email": event_organizer,
                },
                "attendees": [{"email": email} for email in event_attendees_emails] if event_attendees_emails else [],
                "timeZone": event_timezone,
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

            if event_all_day:
                event_config["start"] = {"date": event_start_time_iso.split("T")[0]}
                event_config["end"] = {"date": event_end_time_iso.split("T")[0]}

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
                "event_start_time": event.get("start", {}).get("dateTime", ""),
                "event_end_time": event.get("end", {}).get("dateTime", ""),
                "event_location": event.get("location", ""),
                "event_organizer": event.get("organizer", {}).get("email", ""),
                "event_attendees": event.get("attendees", []),
                "event_meeting_link": event.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", ""),
                "event_timezone": event.get("timeZone", ""),
                "event_all_day": event_all_day,
            })
        except Exception as e:
            logger.error(f"Failed to create calendar event: {e}")
            return False, json.dumps({"error": str(e)})

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
        try:
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
            if event_timezone:
                event["timeZone"] = event_timezone

            if event_start_time and event_end_time:
                event_start_time_iso, event_end_time_iso = prepare_iso_timestamps(event_start_time, event_end_time)
                if event_all_day:
                    event["start"] = {"date": event_start_time_iso.split("T")[0]}
                    event["end"] = {"date": event_end_time_iso.split("T")[0]}
                else:
                    event["start"] = {"dateTime": event_start_time_iso}
                    event["end"] = {"dateTime": event_end_time_iso}

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
                "event_start_time": updated_event.get("start", {}).get("dateTime", ""),
                "event_end_time": updated_event.get("end", {}).get("dateTime", ""),
                "event_location": updated_event.get("location", ""),
                "event_organizer": updated_event.get("organizer", {}).get("email", ""),
                "event_attendees": updated_event.get("attendees", []),
                "event_meeting_link": updated_event.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", ""),
                "event_timezone": updated_event.get("timeZone", ""),
                "event_all_day": event_all_day,
            })
        except Exception as e:
            logger.error(f"Failed to update calendar event: {e}")
            return False, json.dumps({"error": str(e)})

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

            event_start_time_iso, event_end_time_iso = prepare_iso_timestamps(event_start_time, event_end_time)

            event_config = {
                "summary": event_title or "Meeting",
                "description": event_description,
                "start": {
                    "dateTime": event_start_time_iso,
                    "timeZone": event_timezone or "UTC",
                },
                "end": {
                    "dateTime": event_end_time_iso,
                    "timeZone": event_timezone or "UTC",
                },
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

            # Extract the Meet link from the response
            meet_link = ""
            conference_data = event.get("conferenceData", {})
            entry_points = conference_data.get("entryPoints", [])
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri", "")
                    break
            # Fallback: check hangoutLink field (older API responses)
            if not meet_link:
                meet_link = event.get("hangoutLink", "")

            return True, json.dumps({
                "success": True,
                "event_id": event.get("id", ""),
                "event_title": event.get("summary", ""),
                "event_start_time": event.get("start", {}).get("dateTime", ""),
                "event_end_time": event.get("end", {}).get("dateTime", ""),
                "event_location": event.get("location", ""),
                "meet_link": meet_link,
                "event_attendees": event.get("attendees", []),
                "message": f"Google Meet link created and attached to calendar event. Meet link: {meet_link}"
            })
        except Exception as e:
            logger.error(f"Failed to create Meet link: {e}")
            return False, json.dumps({"error": str(e)})

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
            logger.error(f"Failed to delete calendar event: {e}")
            return False, json.dumps({"error": str(e)})

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
            logger.error(f"Failed to get calendar list: {e}")
            return False, json.dumps({"error": str(e)})

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
            logger.error(f"Failed to get calendar by ID: {e}")
            return False, json.dumps({"error": str(e)})
