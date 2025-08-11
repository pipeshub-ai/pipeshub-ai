import json
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from app.agents.actions.google.auth.auth import calendar_auth
from app.agents.actions.google.google_calendar.config import GoogleCalendarConfig
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
                "hangoutLink": event_meeting_link,
                "timezone": event_timezone,
                "allDay": event_all_day,
            }

            if event_all_day:
                event_config["start"]["date"] = event_start_time
                event_config["end"]["date"] = event_end_time
            else:
                event_config["start"]["dateTime"] = event_start_time
                event_config["end"]["dateTime"] = event_end_time

            response = self.service.events().insert(calendarId=self.calendar_id, body=event_config).execute() # type: ignore

            return True, json.dumps(response)
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
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
            event_id: The id of the event
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
            if not event_id:
                return False, json.dumps({"error": "Event ID is required"})
            existing_event = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute() # type: ignore

            if not existing_event:
                return False, json.dumps({"error": "Event not found"})

            event_config = {
                "summary": event_title or existing_event.get("summary"),
                "description": event_description or existing_event.get("description"),
                "start": {
                    "dateTime": str(parse_timestamp(event_start_time or existing_event.get("start").get("dateTime"))),
                },
                "end": {
                    "dateTime": str(parse_timestamp(event_end_time or existing_event.get("end").get("dateTime"))),
                },
                "location": event_location or existing_event.get("location"),
                "organizer": {
                    "email": event_organizer or existing_event.get("organizer").get("email"),
                },
                "attendees": [
                    {
                        "email": attendee,
                    }
                    for attendee in event_attendees_emails or [attendee.get("email") for attendee in existing_event.get("attendees", [])]
                ],
                "hangoutLink": event_meeting_link or existing_event.get("hangoutLink"),
                "timezone": event_timezone or existing_event.get("start").get("timeZone"),
                "allDay": event_all_day or existing_event.get("allDay"),
            }

            if event_all_day:
                event_config["start"]["date"] = event_start_time
                event_config["end"]["date"] = event_end_time
            else:
                event_config["start"]["dateTime"] = event_start_time
                event_config["end"]["dateTime"] = event_end_time

            response = self.service.events().update(calendarId=self.calendar_id, eventId=event_id, body=event_config).execute() # type: ignore
            return True, json.dumps(response)
        except Exception as e:
            return False, json.dumps(str(e))


    @calendar_auth()
    def delete_calendar_event(
        self,
        event_id: str,
    ) -> tuple[bool, str]:
        """Delete a calendar event"""
        """
        Args:
            event_id: The id of the event
        Returns:
            tuple[bool, str]: True if the event is deleted, False otherwise
        """
        try:
            if not event_id:
                return False, json.dumps({"error": "Event ID is required"})
            response = self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute() # type: ignore
            return True, json.dumps(response)
        except Exception as e:
            return False, json.dumps(str(e))


    @calendar_auth()
    def get_calendar_list(self) -> tuple[bool, str]:
        """Get a list of calendars"""
        """
        Returns:
            tuple[bool, str]: True if the calendars are fetched, False otherwise
        """
        try:
            calendars = self.service.events().calendarList().list().execute() # type: ignore
            return True, json.dumps(calendars)
        except Exception as e:
            return False, json.dumps(str(e))

    @calendar_auth()
    def get_calendar_list_by_id(
        self
    ) -> tuple[bool, str]:
        """Get a calendar by id"""
        """
        Returns:
            tuple[bool, str]: True if the calendar is fetched, False otherwise
        """
        try:
            calendar = self.service.events().calendarList().get(calendarId=self.calendar_id).execute() # type: ignore
            return True, json.dumps(calendar)
        except Exception as e:
            return False, json.dumps(str(e))
