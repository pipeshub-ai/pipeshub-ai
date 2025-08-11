import json
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from app.agents.actions.google.auth.auth import calendar_auth
from app.agents.actions.google.google_calendar.config import GoogleCalendarConfig
from app.agents.tool.decorator import tool
from app.utils.time_conversion import parse_timestamp


class GoogleCalendar:
    """Google Calendar tool exposed to the agents"""
    def __init__(self, config: GoogleCalendarConfig) -> None:
        """Initialize the Google Calendar tool"""
        """
        Args:
            config: Google Calendar configuration
        Returns:
            None
        """
        self.config = config
        self.calendar_id = config.calendar_id
        self.service: Optional[Resource] = None
        self.credentials: Optional[Credentials] = None

    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="get_calendar_events")
    def get_calendar_events(
        self,
    ) -> tuple[bool, str]:
        """Get calendar events"""
        """
        Args:
            config: Google Calendar Event configuration
        Returns:
            tuple[bool, str]: True if the events are fetched, False otherwise
        """
        try:
            # TODO: Add pagination
            events = self.service.events().list(calendarId=self.calendar_id).execute() # type: ignore
            return True, json.dumps(events)
        except Exception as e:
            return False, json.dumps(str(e))


    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="create_calendar_event")
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

            event_start_time = str(parse_timestamp(event_start_time))
            event_end_time = str(parse_timestamp(event_end_time))

            event_config = {
                "summary": event_title,
                "description": event_description,
                "start": {
                    "dateTime": event_start_time,
                },
                "end": {
                    "dateTime": event_end_time,
                },
                "location": event_location,
                "organizer": {
                    "email": event_organizer,
                },
                "attendees": [
                    {
                        "email": attendee,
                    }
                    for attendee in event_attendees_emails or []
                ],
                "conferenceData": {
                    "createRequest": {
                        "requestId": event_meeting_link,
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet",
                        },
                    },
                } if event_meeting_link else None,
            }

            event = self.service.events().insert(calendarId=self.calendar_id, body=event_config).execute() # type: ignore
            return True, json.dumps(event)
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="update_calendar_event")
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
            event_title: The title of the event
            event_description: The description of the event
            event_start_time: The start time of the event
            event_end_time: The end time of the event
            event_location: The location of the event
            event_organizer: The organizer of the event
            event_attendees_emails: The attendees of the event
            event_meeting_link: The meeting link of the event
            event_timezone: The timezone of the event
            event_all_day: Whether the event is all day
        Returns:
            tuple[bool, str]: True if the event is updated, False otherwise
        """
        try:
            event_config = {}

            if event_title:
                event_config["summary"] = event_title
            if event_description:
                event_config["description"] = event_description
            if event_start_time:
                event_config["start"] = {
                    "dateTime": str(parse_timestamp(event_start_time)),
                }
            if event_end_time:
                event_config["end"] = {
                    "dateTime": str(parse_timestamp(event_end_time)),
                }
            if event_location:
                event_config["location"] = event_location
            if event_organizer:
                event_config["organizer"] = {
                    "email": event_organizer,
                }
            if event_attendees_emails:
                event_config["attendees"] = [
                    {
                        "email": attendee,
                    }
                    for attendee in event_attendees_emails
                ]
            if event_meeting_link:
                event_config["conferenceData"] = {
                    "createRequest": {
                        "requestId": event_meeting_link,
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet",
                        },
                    },
                }

            event = self.service.events().update(calendarId=self.calendar_id, eventId=event_id, body=event_config).execute() # type: ignore
            return True, json.dumps(event)
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="delete_calendar_event")
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
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute() # type: ignore
            return True, json.dumps({"message": "Event deleted successfully"})
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="get_calendar_list")
    def get_calendar_list(self) -> tuple[bool, str]:
        """Get list of calendars"""
        """
        Returns:
            tuple[bool, str]: True if the calendars are fetched, False otherwise
        """
        try:
            calendars = self.service.calendarList().list().execute() # type: ignore
            return True, json.dumps(calendars)
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
    @tool(app_name="google_calendar", tool_name="get_calendar_list_by_id")
    def get_calendar_list_by_id(
        self
    ) -> tuple[bool, str]:
        """Get calendar by ID"""
        """
        Returns:
            tuple[bool, str]: True if the calendar is fetched, False otherwise
        """
        try:
            calendar = self.service.calendars().get(calendarId=self.calendar_id).execute() # type: ignore
            return True, json.dumps(calendar)
        except Exception as e:
            return False, json.dumps(str(e))
