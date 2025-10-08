import asyncio
import json
import logging
from typing import List, Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource
from app.utils.time_conversion import prepare_iso_timestamps

logger = logging.getLogger(__name__)

class GoogleCalendar:
    """Google Calendar tool exposed to the agents using GoogleCalendarDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Calendar tool"""
        """
        Args:
            client: Google Calendar client
        Returns:
            None
        """
        self.client = GoogleCalendarDataSource(client)

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
        app_name="calendar",
        tool_name="get_calendar_events",
        parameters=[
            ToolParameter(
                name="calendar_id",
                type=ParameterType.STRING,
                description="The ID of the calendar to use (default: 'primary')",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of events to return",
                required=False
            ),
            ToolParameter(
                name="time_min",
                type=ParameterType.STRING,
                description="Lower bound for event start time (RFC3339 format)",
                required=False
            ),
            ToolParameter(
                name="time_max",
                type=ParameterType.STRING,
                description="Upper bound for event start time (RFC3339 format)",
                required=False
            ),
            ToolParameter(
                name="order_by",
                type=ParameterType.STRING,
                description="Order by (e.g., 'startTime' or 'updated')",
                required=False
            ),
            ToolParameter(
                name="single_events",
                type=ParameterType.BOOLEAN,
                description="Whether to expand recurring events into instances",
                required=False
            ),
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Free text search terms to find events",
                required=False
            ),
            ToolParameter(
                name="show_deleted",
                type=ParameterType.BOOLEAN,
                description="Include deleted events",
                required=False
            ),
            ToolParameter(
                name="time_zone",
                type=ParameterType.STRING,
                description="Time zone used in the response",
                required=False
            ),
        ]
    )
    def get_calendar_events(
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
            events = self._run_async(self.client.events_list(
                calendarId=calendar_id or "primary",
                maxResults=max_results,
                timeMin=time_min,
                timeMax=time_max,
                orderBy=order_by,
                singleEvents=single_events,
                q=query,
                showDeleted=show_deleted,
                timeZone=time_zone,
            ))

            return True, json.dumps(events)
        except Exception as e:
            logger.error(f"Failed to get calendar events: {e}")
            return False, json.dumps({"error": str(e)})


    @tool(
        app_name="calendar",
        tool_name="create_calendar_event",
        parameters=[
            ToolParameter(
                name="event_start_time",
                type=ParameterType.STRING,
                description="The start time of the event (ISO format or timestamp)",
                required=True
            ),
            ToolParameter(
                name="event_end_time",
                type=ParameterType.STRING,
                description="The end time of the event (ISO format or timestamp)",
                required=True
            ),
            ToolParameter(
                name="event_title",
                type=ParameterType.STRING,
                description="The title/summary of the event",
                required=False
            ),
            ToolParameter(
                name="event_description",
                type=ParameterType.STRING,
                description="The description of the event",
                required=False
            ),
            ToolParameter(
                name="event_location",
                type=ParameterType.STRING,
                description="The location of the event",
                required=False
            ),
            ToolParameter(
                name="event_organizer",
                type=ParameterType.STRING,
                description="The email of the event organizer",
                required=False
            ),
            ToolParameter(
                name="event_attendees_emails",
                type=ParameterType.ARRAY,
                description="List of email addresses for event attendees",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="event_meeting_link",
                type=ParameterType.STRING,
                description="The meeting link/URL for the event",
                required=False
            ),
            ToolParameter(
                name="event_timezone",
                type=ParameterType.STRING,
                description="The timezone for the event (default: UTC)",
                required=False
            ),
            ToolParameter(
                name="event_all_day",
                type=ParameterType.BOOLEAN,
                description="Whether the event is an all-day event",
                required=False
            )
        ]
    )
    def create_calendar_event(
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
            event = self._run_async(self.client.events_insert(
                calendarId="primary",
                body=event_config
            ))

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
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="The ID of the event to update",
                required=True
            ),
            ToolParameter(
                name="event_title",
                type=ParameterType.STRING,
                description="The new title/summary for the event",
                required=False
            ),
            ToolParameter(
                name="event_description",
                type=ParameterType.STRING,
                description="The new description for the event",
                required=False
            ),
            ToolParameter(
                name="event_start_time",
                type=ParameterType.STRING,
                description="The new start time for the event (ISO format or timestamp)",
                required=False
            ),
            ToolParameter(
                name="event_end_time",
                type=ParameterType.STRING,
                description="The new end time for the event (ISO format or timestamp)",
                required=False
            ),
            ToolParameter(
                name="event_location",
                type=ParameterType.STRING,
                description="The new location for the event",
                required=False
            ),
            ToolParameter(
                name="event_organizer",
                type=ParameterType.STRING,
                description="The new organizer email for the event",
                required=False
            ),
            ToolParameter(
                name="event_attendees_emails",
                type=ParameterType.ARRAY,
                description="The new list of attendee emails for the event",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="event_meeting_link",
                type=ParameterType.STRING,
                description="The new meeting link/URL for the event",
                required=False
            ),
            ToolParameter(
                name="event_timezone",
                type=ParameterType.STRING,
                description="The new timezone for the event",
                required=False
            ),
            ToolParameter(
                name="event_all_day",
                type=ParameterType.BOOLEAN,
                description="Whether the event should be an all-day event",
                required=False
            )
        ]
    )
    def update_calendar_event(
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
            event = self._run_async(self.client.events_get(
                calendarId="primary",
                eventId=event_id
            ))

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
            updated_event = self._run_async(self.client.events_update(
                calendarId="primary",
                eventId=event_id,
                body=event
            ))

            return True, json.dumps({
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
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="The ID of the event to delete",
                required=True
            )
        ]
    )
    def delete_calendar_event(
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
            self._run_async(self.client.events_delete(
                calendarId="primary",
                eventId=event_id
            ))

            return True, json.dumps({
                "message": f"Event {event_id} deleted successfully"
            })
        except Exception as e:
            logger.error(f"Failed to delete calendar event: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="calendar",
        tool_name="get_calendar_list"
    )
    def get_calendar_list(self) -> tuple[bool, str]:
        """Get the list of available calendars"""
        """
        Returns:
            tuple[bool, str]: True if the calendar list is retrieved, False otherwise
        """
        try:
            # Use GoogleCalendarDataSource method
            calendars = self._run_async(self.client.calendar_list_list())
            return True, json.dumps(calendars)
        except Exception as e:
            logger.error(f"Failed to get calendar list: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="calendar",
        tool_name="get_calendar_list_by_id",
        parameters=[
            ToolParameter(
                name="calendar_id",
                type=ParameterType.STRING,
                description="The ID of the calendar to get (default: 'primary')",
                required=False
            )
        ]
    )
    def get_calendar_list_by_id(
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
            calendar = self._run_async(self.client.calendars_get(
                calendarId=calendar_id or "primary"
            ))
            return True, json.dumps(calendar)
        except Exception as e:
            logger.error(f"Failed to get calendar by ID: {e}")
            return False, json.dumps({"error": str(e)})
