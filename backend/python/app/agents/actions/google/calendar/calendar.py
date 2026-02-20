import json
import logging
from typing import List, Optional

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
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource
from app.utils.time_conversion import prepare_iso_timestamps

logger = logging.getLogger(__name__)

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


class DeleteCalendarEventInput(BaseModel):
    """Schema for deleting a calendar event"""
    event_id: str = Field(description="The actual event ID from Google Calendar")


class GetCalendarListByIdInput(BaseModel):
    """Schema for getting a calendar by ID"""
    calendar_id: Optional[str] = Field(default=None, description="The ID of the calendar to get (default: 'primary')")


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
                    "https://www.googleapis.com/auth/calendar.events"
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
            icon_path="/assets/icons/connectors/calendar.svg",
            app_group="Google Workspace",
            app_description="Calendar OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/calendar.svg"))\
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
        app_name="calendar",
        tool_name="get_calendar_events",
        description="Get upcoming calendar events",
        args_schema=GetCalendarEventsInput,
        when_to_use=[
            "User wants to see calendar events/meetings",
            "User mentions 'Calendar' + wants events",
            "User asks about upcoming meetings/events"
        ],
        when_not_to_use=[
            "User wants to create event (use create_calendar_event)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my calendar events",
            "What meetings do I have?",
            "Get calendar events for tomorrow"
        ],
        category=ToolCategory.CALENDAR
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

            return True, json.dumps(events)
        except Exception as e:
            logger.error(f"Failed to get calendar events: {e}")
            return False, json.dumps({"error": str(e)})


    @tool(
        app_name="calendar",
        tool_name="create_calendar_event",
        description="Create a new calendar event",
        args_schema=CreateCalendarEventInput,
        when_to_use=[
            "User wants to create/schedule a meeting/event",
            "User mentions 'Calendar' + wants to create event",
            "User asks to schedule something"
        ],
        when_not_to_use=[
            "User wants to see events (use get_calendar_events)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a calendar event",
            "Schedule a meeting",
            "Add event to calendar"
        ],
        category=ToolCategory.CALENDAR
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
            event = await self.client.events_insert(
                calendarId="primary",
                body=event_config
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
        app_name="calendar",
        tool_name="update_calendar_event",
        description="Update a calendar event",
        args_schema=UpdateCalendarEventInput,
        when_to_use=[
            "User wants to modify/edit an event",
            "User mentions 'Calendar' + wants to update event",
            "User asks to change event details"
        ],
        when_not_to_use=[
            "User wants to create event (use create_calendar_event)",
            "User wants to see events (use get_calendar_events)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Update calendar event",
            "Change event time",
            "Edit meeting details"
        ],
        category=ToolCategory.CALENDAR
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
            updated_event = await self.client.events_update(
                calendarId="primary",
                eventId=event_id,
                body=event
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
        app_name="calendar",
        tool_name="delete_calendar_event",
        description="Delete a calendar event",
        args_schema=DeleteCalendarEventInput,
        when_to_use=[
            "User wants to delete/cancel an event",
            "User mentions 'Calendar' + wants to delete",
            "User asks to remove event"
        ],
        when_not_to_use=[
            "User wants to create event (use create_calendar_event)",
            "User wants to see events (use get_calendar_events)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Delete calendar event",
            "Cancel meeting",
            "Remove event from calendar"
        ],
        category=ToolCategory.CALENDAR
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
        app_name="calendar",
        tool_name="get_calendar_list",
        description="List all calendars",
        when_to_use=[
            "User wants to list all calendars",
            "User mentions 'Calendar' + wants calendars",
            "User asks for available calendars"
        ],
        when_not_to_use=[
            "User wants events (use get_calendar_events)",
            "User wants to create event (use create_calendar_event)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "List all calendars",
            "Show me available calendars",
            "What calendars do I have?"
        ],
        category=ToolCategory.CALENDAR
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
        app_name="calendar",
        tool_name="get_calendar_list_by_id",
        description="Get a specific calendar by ID",
        args_schema=GetCalendarListByIdInput,
        when_to_use=[
            "User wants details about a specific calendar",
            "User mentions 'Calendar' + has calendar ID",
            "User asks about a calendar"
        ],
        when_not_to_use=[
            "User wants all calendars (use get_calendar_list)",
            "User wants events (use get_calendar_events)",
            "User wants info ABOUT calendars (use retrieval)",
            "No Calendar mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get calendar by ID",
            "Show calendar details",
            "What is this calendar?"
        ],
        category=ToolCategory.CALENDAR
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
