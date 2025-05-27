"""Google Calendar User Service module for interacting with Google Calendar API"""

# pylint: disable=E1101, W0718
import os
import pickle
from datetime import datetime, timezone
from typing import Dict, List

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.connectors.sources.google.common.scopes import GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
from app.connectors.utils.decorators import exponential_backoff
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter


class GCalUserService:
    """GCalUserService class for interacting with Google Calendar API"""

    def __init__(
        self,
        logger,
        config: ConfigurationService,
        rate_limiter: GoogleAPIRateLimiter,
        credentials=None,
    ):
        self.logger = logger
        self.config_service = config
        self.service = None
        self.credentials = credentials
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter

    async def connect_individual_user(self) -> bool:
        """Connect using OAuth2 credentials for individual user"""
        try:
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES

            creds = None
            if os.path.exists("token.pickle"):
                with open("token.pickle", "rb") as token:
                    creds = pickle.load(token)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    credentials_path = await self.config_service.get_config(
                        config_node_constants.GOOGLE_AUTH_CREDENTIALS_PATH.value
                    )
                    flow = InstalledAppFlow.from_client_secrets_file(
                        credentials_path, SCOPES
                    )
                    creds = flow.run_local_server(port=8090)
                with open("token.pickle", "wb") as token:
                    pickle.dump(creds, token)

            self.service = build("calendar", "v3", credentials=creds)
            self.logger.info("✅ GCalUserService connected successfully")
            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to connect to Individual Calendar Service: %s", str(e)
            )
            return False

    async def connect_enterprise_user(self, org_id, user_id) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            self.logger.info("🚀 Connecting to Enterprise Calendar Service")
            self.org_id = org_id
            self.user_id = user_id
            self.service = build(
                "calendar", "v3", credentials=self.credentials, cache_discovery=False
            )
            self.logger.info("✅ GCalUserService connected successfully")
            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to connect to Enterprise Calendar Service: %s", str(e)
            )
            return False

    @exponential_backoff()
    async def list_calendars(self) -> List[Dict]:
        """List all calendars for the user"""
        try:
            self.logger.info("🚀 Listing calendars")
            calendars = []
            page_token = None

            while True:
                async with self.google_limiter:
                    results = (
                        self.service.calendarList().list(pageToken=page_token).execute()
                    )

                    calendars.extend(
                        [
                            {
                                "_key": calendar.get("id"),
                                "calendarId": calendar.get("id"),
                                "name": calendar.get("summary"),
                                "description": calendar.get("description", ""),
                                "timezone": calendar.get("timeZone"),
                                "accessRole": calendar.get("accessRole"),
                                "primary": calendar.get("primary", False),
                                "deleted": calendar.get("deleted", False),
                            }
                            for calendar in results.get("items", [])
                        ]
                    )

                    page_token = results.get("nextPageToken")
                    if not page_token:
                        break

            self.logger.info("✅ Found %s calendars", len(calendars))
            return calendars

        except Exception as e:
            self.logger.error("❌ Failed to list calendars: %s", str(e))
            return []

    @exponential_backoff()
    async def list_events(self, calendar_id: str = "primary") -> List[Dict]:
        """List all events in a calendar"""
        try:
            self.logger.info(f"🚀 Listing events for calendar: {calendar_id}")
            events = []
            page_token = None
            time_min = datetime.now(timezone.utc).isoformat()

            while True:
                async with self.google_limiter:
                    results = (
                        self.service.events()
                        .list(
                            calendarId=calendar_id,
                            timeMin=time_min,
                            singleEvents=True,
                            orderBy="startTime",
                            pageToken=page_token,
                        )
                        .execute()
                    )

                    events.extend(
                        [
                            {
                                "_key": event.get("id"),
                                "eventId": event.get("id"),
                                "calendarId": calendar_id,
                                "summary": event.get("summary"),
                                "description": event.get("description", ""),
                                "location": event.get("location"),
                                "creator": event.get("creator", {}),
                                "organizer": event.get("organizer", {}),
                                "start": event.get("start"),
                                "end": event.get("end"),
                                "status": event.get("status"),
                                "attendees": event.get("attendees", []),
                                "created": event.get("created"),
                                "updated": event.get("updated"),
                            }
                            for event in results.get("items", [])
                        ]
                    )

                    page_token = results.get("nextPageToken")
                    if not page_token:
                        break

            self.logger.info("✅ Found %s events", len(events))
            return events

        except Exception as e:
            self.logger.error("❌ Failed to list events: %s", str(e))
            return []

    @exponential_backoff()
    async def get_freebusy(
        self, calendar_ids: List[str], time_min: datetime, time_max: datetime
    ) -> Dict:
        """Get free/busy information for calendars in a given time range"""
        try:
            self.logger.info("🚀 Getting freebusy information")
            async with self.google_limiter:
                body = {
                    "timeMin": time_min.isoformat(),
                    "timeMax": time_max.isoformat(),
                    "timeZone": "UTC",
                    "items": [{"id": calendar_id} for calendar_id in calendar_ids],
                }

                results = self.service.freebusy().query(body=body).execute()

                calendars = {}
                for calendar_id, busy_info in results.get("calendars", {}).items():
                    calendars[calendar_id] = {
                        "busy": [
                            {"start": busy.get("start"), "end": busy.get("end")}
                            for busy in busy_info.get("busy", [])
                        ]
                    }

                self.logger.info("✅ Freebusy information fetched successfully")
                return calendars

        except Exception as e:
            self.logger.error("❌ Failed to get freebusy information: %s", str(e))
            return {}

    async def disconnect(self):
        """Disconnect and cleanup Calendar service"""
        try:
            self.logger.info("🔄 Disconnecting Calendar service")
            if self.service:
                self.service.close()
                self.service = None
            self.credentials = None
            self.logger.info("✅ Calendar service disconnected successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect Calendar service: {str(e)}")
            return False
