import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from datetime import datetime, timezone as tz, date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from msgraph.generated.models.patterned_recurrence import PatternedRecurrence
from msgraph.generated.models.recurrence_pattern import RecurrencePattern
from msgraph.generated.models.recurrence_pattern_type import RecurrencePatternType
from msgraph.generated.models.recurrence_range import RecurrenceRange
from msgraph.generated.models.recurrence_range_type import RecurrenceRangeType

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.types import AuthField, DocumentationLink
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
)

logger = logging.getLogger(__name__)

def _serialize_graph_obj(obj: Any) -> Any:
    """Recursively convert an MS Graph SDK Kiota object to a JSON-serialisable value.

    Kiota Parsable models store data in an internal backing store, so plain
    ``vars()`` only reveals ``{'backing_store': …}``.  We first try kiota's own
    ``JsonSerializationWriter``; on failure we iterate the backing store, then
    fall back to ``vars()`` + ``additional_data``.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_serialize_graph_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize_graph_obj(v) for k, v in obj.items()}

    # Kiota Parsable objects expose get_field_deserializers()
    if hasattr(obj, "get_field_deserializers"):
        try:
            from kiota_serialization_json.json_serialization_writer import (  # type: ignore
                JsonSerializationWriter,
            )
            writer = JsonSerializationWriter()
            writer.write_object_value(None, obj)
            content = writer.get_serialized_content()
            if content:
                raw = content.decode("utf-8") if isinstance(content, bytes) else content
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed:
                    return parsed
        except Exception:
            pass

        try:
            bs = getattr(obj, "backing_store", None)
            if bs is not None and hasattr(bs, "enumerate_"):
                result: Dict[str, Any] = {}
                for key, value in bs.enumerate_():
                    if not str(key).startswith("_"):
                        try:
                            result[key] = _serialize_graph_obj(value)
                        except Exception:
                            result[key] = str(value)
                additional = getattr(obj, "additional_data", None)
                if isinstance(additional, dict):
                    for k, v in additional.items():
                        if k not in result:
                            try:
                                result[k] = _serialize_graph_obj(v)
                            except Exception:
                                result[k] = str(v)
                if result:
                    return result
        except Exception:
            pass

    # Generic fallback for non-Kiota objects
    try:
        obj_dict = vars(obj)
    except TypeError:
        obj_dict = {}

    result = {}
    for k, v in obj_dict.items():
        if k.startswith("_"):
            continue
        try:
            result[k] = _serialize_graph_obj(v)
        except Exception:
            result[k] = str(v)

    additional = getattr(obj, "additional_data", None)
    if isinstance(additional, dict):
        for k, v in additional.items():
            if k not in result:
                try:
                    result[k] = _serialize_graph_obj(v)
                except Exception:
                    result[k] = str(v)

    return result if result else str(obj)


def _normalize_odata(data: Any) -> Any:
    """Normalize OData response keys so cascading placeholders resolve reliably.

    MS Graph returns collections under a ``value`` key, but LLM planners
    commonly guess ``results``.  We keep ``value`` intact and add a
    ``results`` alias pointing to the same list so both paths work.
    """
    if isinstance(data, dict):
        if "value" in data and isinstance(data["value"], list) and "results" not in data:
            data["results"] = data["value"]
    return data


def _response_data(response: object) -> Any:
    """Serialize response.data the same way _response_json does. Returns Python dict/list/None."""
    data = getattr(response, "data", None)
    if data is None:
        return None
    return _normalize_odata(_serialize_graph_obj(data))


def _response_json(response: object) -> str:
    """Serialize an OneDriveResponse to JSON, handling Kiota SDK objects in data."""
    out: Dict[str, Any] = {"success": getattr(response, "success", False)}
    data = _response_data(response)
    if data is not None:
        out["data"] = data
    error = getattr(response, "error", None)
    if error is not None:
        out["error"] = error
    message = getattr(response, "message", None)
    if message is not None:
        out["message"] = message
    return json.dumps(out)

@staticmethod
def _status_label(status_char: str) -> str:
    return {
        "0": "free",
        "1": "tentative",
        "2": "busy",
        "3": "oof",
        "4": "workingElsewhere",
    }.get(status_char, "unknown")

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SendMailInput(BaseModel):
    """Schema for sending an email via Outlook"""
    to_recipients: List[str] = Field(description="List of recipient email addresses")
    subject: str = Field(description="Email subject")
    body: str = Field(description="Email body content (plain text or HTML)")
    body_type: Optional[str] = Field(default="Text", description="Body content type: 'Text' or 'HTML'")
    cc_recipients: Optional[List[str]] = Field(default=None, description="List of CC recipient email addresses")
    bcc_recipients: Optional[List[str]] = Field(default=None, description="List of BCC recipient email addresses")


class ReplyToMessageInput(BaseModel):
    """Schema for replying to an email"""
    message_id: str = Field(description="ID of the message to reply to")
    comment: str = Field(description="Reply comment / body text")


class ReplyAllToMessageInput(BaseModel):
    """Schema for reply-all to an email"""
    message_id: str = Field(description="ID of the message to reply-all to")
    comment: str = Field(description="Reply-all comment / body text")


class ForwardMessageInput(BaseModel):
    """Schema for forwarding an email"""
    message_id: str = Field(description="ID of the message to forward")
    to_recipients: List[str] = Field(description="List of recipient email addresses to forward to")
    comment: Optional[str] = Field(default=None, description="Optional comment to include with the forwarded message")


class GetMessageInput(BaseModel):
    """Schema for getting a specific message"""
    message_id: str = Field(description="ID of the email message to retrieve")


class SearchMessagesInput(BaseModel):
    """Schema for searching/listing messages"""
    search: Optional[str] = Field(default=None, description="Search query string (OData $search)")
    filter: Optional[str] = Field(default=None, description="OData $filter expression (e.g. 'isRead eq false')")
    top: Optional[int] = Field(default=10, description="Maximum number of messages to return (default 10, max 50)")
    orderby: Optional[str] = Field(default="receivedDateTime desc", description="OData $orderby expression")


class GetCalendarEventsInput(BaseModel):
    """Schema for listing calendar events in a date/time range"""
    start_datetime: str = Field(description="Start of the time range in ISO 8601 format (e.g. 2024-01-15T00:00:00Z)")
    end_datetime: str = Field(description="End of the time range in ISO 8601 format (e.g. 2024-01-22T00:00:00Z)")
    top: Optional[int] = Field(default=10, description="Maximum number of events to return")


class SearchCalendarEventsInput(BaseModel):
    """Schema for searching calendar events by keyword (subject, body, location)"""
    search: str = Field(description="Search keyword or phrase to find in event subject, body, and location")
    top: Optional[int] = Field(default=10, description="Maximum number of events to return (default 10)")


class CreateCalendarEventInput(BaseModel):
    """Schema for creating a calendar event"""
    subject: str = Field(description="Title/subject of the event")
    start_datetime: str = Field(description="Start datetime in ISO 8601 format (e.g. 2024-01-15T10:00:00)")
    end_datetime: str = Field(description="End datetime in ISO 8601 format (e.g. 2024-01-15T11:00:00)")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the event (e.g. 'UTC', 'America/New_York', 'India Standard Time')")
    body: Optional[str] = Field(default=None, description="Body/description of the event")
    location: Optional[str] = Field(default=None, description="Location of the event")
    attendees: Optional[List[str]] = Field(default=None, description="List of attendee email addresses")
    is_online_meeting: Optional[bool] = Field(default=False, description="Whether to create an online meeting link")
    recurrence: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional recurrence dict to make this a repeating event."
            " Must have two keys: 'pattern' (how often) and 'range' (when it ends)."
            " All keys are camelCase matching the MS Graph API."
            " PATTERN keys:"
            " type (required): 'daily' | 'weekly' | 'absoluteMonthly' | 'relativeMonthly' | 'absoluteYearly' | 'relativeYearly'."
            " interval (int, default 1): repeat every N units."
            " daysOfWeek (list[str]): required for weekly/relativeMonthly/relativeYearly."
            "   Valid: Sunday Monday Tuesday Wednesday Thursday Friday Saturday."
            " dayOfMonth (int 1-31): required for absoluteMonthly/absoluteYearly."
            " month (int 1-12): required for absoluteYearly/relativeYearly."
            " index (str): required for relativeMonthly/relativeYearly."
            "   Valid: first second third fourth last."
            " RANGE keys:"
            " type (required): 'endDate' (needs startDate+endDate) | 'noEnd' (needs startDate) | 'numbered' (needs startDate+numberOfOccurrences)."
            " startDate (YYYY-MM-DD, required): MUST match the date portion of start_datetime."
            " endDate (YYYY-MM-DD): required when type='endDate'."
            " numberOfOccurrences (int): required when type='numbered'."
            " EXAMPLES:"
            " daily 30x: {'pattern':{'type':'daily','interval':1},'range':{'type':'numbered','startDate':'2026-03-01','numberOfOccurrences':30}}."
            " weekly Mon+Wed until Dec: {'pattern':{'type':'weekly','interval':1,'daysOfWeek':['Monday','Wednesday']},'range':{'type':'endDate','startDate':'2026-03-02','endDate':'2026-12-31'}}."
            " monthly 15th forever: {'pattern':{'type':'absoluteMonthly','interval':1,'dayOfMonth':15},'range':{'type':'noEnd','startDate':'2026-03-15'}}."
            " first Monday each month: {'pattern':{'type':'relativeMonthly','interval':1,'daysOfWeek':['Monday'],'index':'first'},'range':{'type':'noEnd','startDate':'2026-03-02'}}."
            " yearly Mar 15: {'pattern':{'type':'absoluteYearly','interval':1,'dayOfMonth':15,'month':3},'range':{'type':'noEnd','startDate':'2026-03-15'}}."
            " last Friday of March each year: {'pattern':{'type':'relativeYearly','interval':1,'daysOfWeek':['Friday'],'index':'last','month':3},'range':{'type':'noEnd','startDate':'2026-03-27'}}."
        ),
    )

class GetFreeTimeSlotsInput(BaseModel):
    """Schema for fetching free time slots within a time frame."""
    start_datetime: str = Field(
        description="Start of time frame to check (ISO 8601, e.g. '2026-03-01T09:00:00').",
    )
    end_datetime: str = Field(
        description="End of time frame to check (ISO 8601, e.g. '2026-03-01T18:00:00').",
    )
    timezone: str = Field(
        default="UTC",
        description="Windows timezone name. E.g. 'India Standard Time'.",
    )
    slot_duration_minutes: int = Field(
        default=30,
        description="Minimum duration of a free slot in minutes.",
        ge=15,
        le=480,
    )

class DeleteRecurringEventOccurrencesInput(BaseModel):
    """Schema for deleting multiple occurrences of a recurring event."""
    event_id: str = Field(
        description="The series master event ID of the recurring event.",
    )
    occurrence_dates: List[str] = Field(
        description="List of dates to delete occurrences on (YYYY-MM-DD). E.g. ['2026-03-10', '2026-03-17'].",
    )
    timezone: str = Field(
        default="UTC",
        description="Windows timezone name. E.g. 'India Standard Time'.",
    )

class GetCalendarEventInput(BaseModel):
    """Schema for getting a specific calendar event"""
    event_id: str = Field(description="ID of the calendar event to retrieve")


class UpdateCalendarEventInput(BaseModel):
    """Schema for updating a calendar event.

    body, location, and attendees accept either simple values (string / list of emails)
    or the raw API shape from get_calendar_events (dict with content/displayName,
    list of attendee objects). The tool normalizes them before sending to the API.
    """
    event_id: str = Field(description="ID of the calendar event to update")
    subject: Optional[str] = Field(default=None, description="New title/subject of the event")
    start_datetime: Optional[str] = Field(default=None, description="New start datetime in ISO 8601 format")
    end_datetime: Optional[str] = Field(default=None, description="New end datetime in ISO 8601 format")
    timezone: Optional[str] = Field(default=None, description="Timezone for the event (e.g. 'UTC', 'America/New_York', 'India Standard Time')")
    body: Optional[Any] = Field(default=None, description="New body/description (string or dict with 'content' key from API)")
    location: Optional[Any] = Field(default=None, description="New location (string or dict with 'displayName' from API)")
    attendees: Optional[List[str]] = Field(default=None,description="Attendee emails as a list of email strings (Gemini-compatible schema).",)
    is_online_meeting: Optional[bool] = Field(default=None, description="Whether to create an online meeting link")
    recurrence: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Updated recurrence settings. Same dict structure as create_calendar_event recurrence. "
            "Set to change or add recurrence to an existing event. "
            "Must contain 'pattern' (type, interval, daysOfWeek/dayOfMonth/month/index as needed) "
            "and 'range' (type, startDate, and endDate or numberOfOccurrences as needed)."
        ),
    )


class DeleteCalendarEventInput(BaseModel):
    """Schema for deleting a calendar event"""
    event_id: str = Field(description="ID of the calendar event to delete")


class ListOnlineMeetingsInput(BaseModel):
    """Schema for listing online meetings.

    IMPORTANT: The /me/onlineMeetings endpoint has very limited query support.
    Only $filter by joinUrl and $select/$expand are allowed.
    $top, $skip, $orderby, $search and date/time filters are NOT supported.
    To find meetings in a date range use get_calendar_events instead.
    """
    filter: Optional[str] = Field(
        default=None,
        description=(
            "OData $filter — only JoinUrl filtering is supported on this endpoint "
            "(e.g. \"JoinUrl eq 'https://teams.microsoft.com/l/meetup-join/...'\")"
            ". Do NOT use date/time filters here; use get_calendar_events for date ranges."
        ),
    )
    top: Optional[int] = Field(default=10, description="Maximum number of meetings to return")


class GetMeetingTranscriptsInput(BaseModel):
    """Schema for fetching transcripts of an online meeting.

    Provide EITHER join_url (preferred, skips one API call) OR event_id.
    join_url is available as event.onlineMeeting.joinUrl on any calendar
    event returned by get_calendar_events or search_calendar_events.
    """
    join_url: Optional[str] = Field(
        default=None,
        description=(
            "The Teams join URL (joinUrl from event.onlineMeeting.joinUrl). "
            "Preferred over event_id — skips one API call."
        ),
    )
    event_id: Optional[str] = Field(
        default=None,
        description=(
            "The calendar event ID. Used as fallback when join_url is not available. "
            "The tool will fetch the event to extract joinUrl automatically."
        ),
    )

class GetRecurringEventsEndingInput(BaseModel):
    """Schema for fetching recurring events ending within a time frame."""
    end_before: str = Field(
        description=(
            "Fetch recurring events whose recurrence ends before this datetime "
            "(ISO 8601, e.g. '2026-03-31T23:59:59Z')."
        ),
    )
    end_after: Optional[str] = Field(
        default=None,
        description=(
            "Fetch recurring events whose recurrence ends after this datetime "
            "(ISO 8601, e.g. '2026-03-01T00:00:00Z'). "
            "Defaults to now if not provided."
        ),
    )
    timezone: str = Field(
        default="UTC",
        description="Windows timezone name for returned datetimes. E.g. 'India Standard Time'.",
    )
    top: int = Field(
        default=10,
        description="Maximum number of results to return (1–50).",
        ge=1,
        le=50,
    )

class GetMailFoldersInput(BaseModel):
    """Schema for getting mail folders"""
    top: Optional[int] = Field(default=20, description="Maximum number of folders to return")


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------


def _build_recurrence_body(recurrence: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a recurrence dict into the MS Graph API format.

    Accepts the dict exactly as provided in the schema description — keys are
    already camelCase (daysOfWeek, dayOfMonth, numberOfOccurrences, startDate,
    endDate) so we pass pattern and range through directly, only validating
    that the required top-level keys are present.
    """
    if not isinstance(recurrence, dict):
        raise ValueError("recurrence must be a dict with 'pattern' and 'range' keys")
    if "pattern" not in recurrence or "range" not in recurrence:
        raise ValueError("recurrence dict must contain both 'pattern' and 'range' keys")
    return {
        "pattern": recurrence["pattern"],
        "range": recurrence["range"],
    }

@ToolsetBuilder("Outlook")\
    .in_group("Microsoft 365")\
    .with_description("Microsoft Outlook integration for email and calendar management")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Outlook",
            authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            redirect_uri="toolsets/oauth/callback/outlook",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "Mail.ReadWrite",
                    "Mail.Send",
                    "Calendars.ReadWrite",
                    "OnlineMeetings.Read",
                    "OnlineMeetingTranscript.Read.All",
                    "offline_access",
                    "User.Read",
                ]
            ),
            additional_params={
                "prompt": "select_account",
                "response_mode": "query",
            },
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration"),
                AuthField(
                    name="tenantId",
                    display_name="Tenant ID",
                    field_type="TEXT",
                    placeholder="common  (or your Azure AD tenant ID / domain)",
                    description=(
                        "Your Azure Active Directory tenant ID (e.g. "
                        "'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx') or domain "
                        "(e.g. 'contoso.onmicrosoft.com'). "
                        "Leave blank or enter 'common' to allow both personal Microsoft "
                        "accounts and any Azure AD tenant."
                    ),
                    required=False,
                    default_value="common",
                    min_length=0,
                    max_length=500,
                    is_secret=False,
                ),
            ],
            icon_path="/assets/icons/connectors/outlook.svg",
            app_group="Microsoft 365",
            app_description="Microsoft Outlook OAuth application for agent integration",
            documentation_links=[
                DocumentationLink(
                    title="Create an Azure App Registration",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
                    doc_type="setup",
                ),
                DocumentationLink(
                    title="Microsoft Graph Mail & Calendar permissions",
                    url="https://learn.microsoft.com/en-us/graph/permissions-reference",
                    doc_type="reference",
                ),
                DocumentationLink(
                    title="Configure OAuth 2.0 redirect URIs",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/reply-url",
                    doc_type="setup",
                ),
            ],
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/outlook.svg"))\
    .build_decorator()
class Outlook:
    """Microsoft Outlook toolset for email and calendar operations.

    Initialised with an MSGraphClient built via ``build_from_toolset`` which
    uses delegated OAuth (user-consent) instead of admin-consent app-only
    credentials.  The data source (``OutlookCalendarContactsDataSource``) is
    constructed identically to the connector path — the only difference is
    how the underlying ``GraphServiceClient`` is authenticated.

    Client chain:
        MSGraphClient (from build_from_toolset)
            → .get_client()  → _DelegatedGraphClient shim
                → .get_ms_graph_service_client() → GraphServiceClient
        OutlookCalendarContactsDataSource(ms_graph_client)
            → internally: self.client = client.get_client().get_ms_graph_service_client()
            → all /me/* Graph API calls go through self.client
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the Outlook toolset.

        The data source is created in exactly the same way the connector
        creates it — ``OutlookCalendarContactsDataSource(client)`` — so every
        method the connector can call is available here too.

        Args:
            client: Authenticated MSGraphClient instance (from build_from_toolset)
        """
        self.client = OutlookCalendarContactsDataSource(client)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_error(self, error: Exception, operation: str = "operation") -> tuple[bool, str]:
        """Return a standardised error tuple."""
        error_msg = str(error).lower()

        if isinstance(error, AttributeError):
            if "client" in error_msg or "me" in error_msg:
                logger.error(
                    f"Outlook client not properly initialised – authentication may be required: {error}"
                )
                return False, json.dumps({
                    "error": (
                        "Outlook toolset is not authenticated. "
                        "Please complete the OAuth flow first. "
                        "Go to Settings > Toolsets to authenticate your Outlook account."
                    )
                })

        auth_related = (
            "not authenticated" in error_msg
            or "oauth" in error_msg
            or "authentication" in error_msg
            or "unauthorized" in error_msg
        )
        if auth_related and not (isinstance(error, ValueError) and "recurrence" in error_msg.lower()):
            logger.error(f"Outlook authentication error during {operation}: {error}")
            return False, json.dumps({
                "error": (
                    "Outlook toolset is not authenticated. "
                    "Please complete the OAuth flow first. "
                    "Go to Settings > Toolsets to authenticate your Outlook account."
                )
            })

        logger.error(f"Failed to {operation}: {error}")
        return False, json.dumps({"error": str(error)})

    @staticmethod
    def _serialize_response(response_obj: Any) -> Any:
        """Recursively convert a Graph SDK response object to a JSON-serialisable dict.

        Kiota model objects (Parsable) store their properties in an internal
        backing store rather than as plain instance attributes, so ``vars()``
        only reveals ``{'backing_store': ..., 'additional_data': {...}}``.
        We first try kiota's own JSON serialization writer which handles the
        backing store correctly.  On any failure we fall back to the previous
        ``vars()`` + ``additional_data`` approach.
        """
        if response_obj is None:
            return None
        if isinstance(response_obj, (str, int, float, bool)):
            return response_obj
        if isinstance(response_obj, list):
            return [Outlook._serialize_response(item) for item in response_obj]
        if isinstance(response_obj, dict):
            return {k: Outlook._serialize_response(v) for k, v in response_obj.items()}

        # ── Kiota Parsable objects ────────────────────────────────────────────
        # Kiota models implement get_field_deserializers() as part of the
        # Parsable interface.  Use kiota's JsonSerializationWriter to produce a
        # proper camelCase dict (id, subject, isOnlineMeeting, …) so that
        # placeholder paths like {{…events[0].id}} resolve correctly.
        if hasattr(response_obj, "get_field_deserializers"):
            try:
                from kiota_serialization_json.json_serialization_writer import (  # type: ignore
                    JsonSerializationWriter,
                )
                import json as _json

                writer = JsonSerializationWriter()
                writer.write_object_value(None, response_obj)
                content = writer.get_serialized_content()
                if content:
                    raw = content.decode("utf-8") if isinstance(content, bytes) else content
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and parsed:
                        return parsed
            except Exception:
                pass

            # Secondary fallback: iterate backing store if available
            try:
                backing_store = getattr(response_obj, "backing_store", None)
                if backing_store is not None and hasattr(backing_store, "enumerate_"):
                    result: Dict[str, Any] = {}
                    for key, value in backing_store.enumerate_():
                        if not str(key).startswith("_"):
                            try:
                                result[key] = Outlook._serialize_response(value)
                            except Exception:
                                result[key] = str(value)
                    additional = getattr(response_obj, "additional_data", None)
                    if isinstance(additional, dict):
                        for k, v in additional.items():
                            if k not in result:
                                try:
                                    result[k] = Outlook._serialize_response(v)
                                except Exception:
                                    result[k] = str(v)
                    if result:
                        return result
            except Exception:
                pass

        # ── Generic fallback (non-kiota objects) ─────────────────────────────
        try:
            obj_dict = vars(response_obj)
        except TypeError:
            obj_dict = {}

        result = {}
        for k, v in obj_dict.items():
            if k.startswith("_"):
                continue
            try:
                result[k] = Outlook._serialize_response(v)
            except Exception:
                result[k] = str(v)

        additional = getattr(response_obj, "additional_data", None)
        if isinstance(additional, dict):
            for k, v in additional.items():
                if k not in result:
                    try:
                        result[k] = Outlook._serialize_response(v)
                    except Exception:
                        result[k] = str(v)

        return result if result else str(response_obj)

    # ------------------------------------------------------------------
    # Mail tools
    # ------------------------------------------------------------------

    @tool(
        app_name="outlook",
        tool_name="send_email",
        description="Send an email via Microsoft Outlook",
        args_schema=SendMailInput,
        when_to_use=[
            "User wants to send an email via Outlook or Microsoft 365",
            "User mentions 'Outlook' or 'Microsoft email' and wants to send",
            "User asks to email someone",
        ],
        when_not_to_use=[
            "User wants to reply to an existing email (use reply_to_message)",
            "User wants to forward an email (use forward_message)",
            "User wants to search emails (use search_messages)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send an email to john@example.com",
            "Email the team about the meeting",
            "Send Outlook message",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def send_email(
        self,
        to_recipients: List[str],
        subject: str,
        body: str,
        body_type: Optional[str] = "Text",
        cc_recipients: Optional[List[str]] = None,
        bcc_recipients: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Send an email using Microsoft Outlook (create draft then send)."""
        try:
            message_body: Dict[str, Any] = {
                "subject": subject,
                "body": {
                    "contentType": body_type or "Text",
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in to_recipients
                    if addr.strip()
                ],
            }
            if cc_recipients:
                message_body["ccRecipients"] = [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in cc_recipients
                    if addr.strip()
                ]
            if bcc_recipients:
                message_body["bccRecipients"] = [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in bcc_recipients
                    if addr.strip()
                ]

            # Step 1 – create draft
            create_response = await self.client.me_create_messages(request_body=message_body)

            res_json = _response_json(create_response)
            if not create_response.success:
                return False, json.dumps({
                    "error": create_response.error or "Failed to create email draft"
                })

            data = _response_data(create_response)
            message_id = data.get("id") if isinstance(data, dict) else None

            if not message_id:
                return False, json.dumps({"error": "Failed to retrieve message ID from draft"})

            # Step 2 – send the draft
            send_response = await self.client.me_messages_message_send(message_id=message_id)
            res_json = _response_json(send_response)
            if send_response.success:
                # Build recipient summary for clarity
                recipients_info = {
                    "to": to_recipients,
                }
                if cc_recipients:
                    recipients_info["cc"] = cc_recipients
                if bcc_recipients:
                    recipients_info["bcc"] = bcc_recipients
                
                return True, json.dumps({
                    "message": "Email sent successfully",
                    "message_id": message_id,
                    "subject": subject,
                    "recipients": recipients_info,
                })
            else:
                return False, json.dumps({
                    "error": send_response.error or "Failed to send email"
                })

        except Exception as e:
            return self._handle_error(e, "send email")

    @tool(
        app_name="outlook",
        tool_name="reply_to_message",
        description="Reply to an Outlook email message",
        args_schema=ReplyToMessageInput,
        when_to_use=[
            "User wants to reply to an email in Outlook",
            "User mentions 'Outlook' and wants to reply",
            "User asks to respond to an email message",
        ],
        when_not_to_use=[
            "User wants to send a new email (use send_email)",
            "User wants to reply to all (use reply_all_to_message)",
            "User wants to search emails (use search_messages)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Reply to this Outlook email",
            "Respond to message",
            "Reply saying I'll be there",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def reply_to_message(
        self,
        message_id: str,
        comment: str,
    ) -> tuple[bool, str]:
        """Reply to an Outlook email message."""
        try:
            response = await self.client.me_messages_message_reply(
                message_id=message_id,
                request_body={"comment": comment},
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                return True, json.dumps({"message": "Reply sent successfully"})
            else:
                return False, json.dumps({"error": response.error or "Failed to send reply"})
        except Exception as e:
            return self._handle_error(e, "reply to message")

    @tool(
        app_name="outlook",
        tool_name="reply_all_to_message",
        description="Reply-all to an Outlook email message",
        args_schema=ReplyAllToMessageInput,
        when_to_use=[
            "User wants to reply-all to an email in Outlook",
            "User mentions 'Outlook' and wants to reply to all recipients",
        ],
        when_not_to_use=[
            "User only wants to reply to the sender (use reply_to_message)",
            "User wants to send a new email (use send_email)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Reply all to this email",
            "Respond to everyone on this thread",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def reply_all_to_message(
        self,
        message_id: str,
        comment: str,
    ) -> tuple[bool, str]:
        """Reply-all to an Outlook email message."""
        try:
            response = await self.client.me_messages_message_reply_all(
                message_id=message_id,
                request_body={"comment": comment},
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                return True, json.dumps({"message": "Reply-all sent successfully"})
            else:
                return False, json.dumps({"error": response.error or "Failed to send reply-all"})
        except Exception as e:
            return self._handle_error(e, "reply-all to message")

    @tool(
        app_name="outlook",
        tool_name="forward_message",
        description="Forward an Outlook email to one or more recipients",
        args_schema=ForwardMessageInput,
        when_to_use=[
            "User wants to forward an Outlook email",
            "User mentions 'Outlook' and wants to forward a message",
        ],
        when_not_to_use=[
            "User wants to send a new email (use send_email)",
            "User wants to reply (use reply_to_message)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Forward this email to sarah@example.com",
            "Forward the message to the team",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def forward_message(
        self,
        message_id: str,
        to_recipients: List[str],
        comment: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Forward an Outlook email."""
        try:
            forward_body: Dict[str, Any] = {
                "toRecipients": [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in to_recipients
                    if addr.strip()
                ],
            }
            if comment:
                forward_body["comment"] = comment

            response = await self.client.me_messages_message_forward(
                message_id=message_id,
                request_body=forward_body,
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                return True, json.dumps({"message": "Email forwarded successfully"})
            else:
                return False, json.dumps({"error": response.error or "Failed to forward email"})
        except Exception as e:
            return self._handle_error(e, "forward message")

    @tool(
        app_name="outlook",
        tool_name="search_messages",
        description="Search or list email messages in Microsoft Outlook",
        args_schema=SearchMessagesInput,
        when_to_use=[
            "User wants to search for emails in Outlook",
            "User wants to see their inbox or recent messages",
            "User mentions 'Outlook' and wants to find emails",
            "User asks to list unread emails",
        ],
        when_not_to_use=[
            "User wants to send email (use send_email)",
            "User wants to read a specific email (use get_message)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for emails from john@example.com",
            "Show my unread emails in Outlook",
            "Find emails about the project",
            "List my recent Outlook messages",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def search_messages(
        self,
        search: Optional[str] = None,
        filter: Optional[str] = None,
        top: Optional[int] = 10,
        orderby: Optional[str] = "receivedDateTime desc",
    ) -> tuple[bool, str]:
        """Search or list Outlook email messages.

        Calls the data source's ``me_list_messages`` — the same method the
        connector uses — which issues GET /me/messages with OData query
        parameters ($search, $filter, $orderby, $top).
        """
        try:
            response = await self.client.me_list_messages(
                search=search,
                filter=filter,
                top=min(top or 10, 50),
                orderby=orderby,
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                data = _response_data(response)
                raw = (data.get("value") or data.get("results")) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                messages = raw if isinstance(raw, list) else []
                return True, json.dumps({
                    "messages": messages,
                    "count": len(messages),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to retrieve messages"})
        except Exception as e:
            return self._handle_error(e, "search messages")

    @tool(
        app_name="outlook",
        tool_name="get_message",
        description="Get the full details of a specific Outlook email message",
        args_schema=GetMessageInput,
        when_to_use=[
            "User wants to read/view a specific Outlook email",
            "User has a message ID and wants to see its content",
            "User asks to show email details",
        ],
        when_not_to_use=[
            "User wants to search emails (use search_messages)",
            "User wants to send email (use send_email)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get the email with id ABC123",
            "Show me this Outlook message",
            "Read the email",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_message(
        self,
        message_id: str,
    ) -> tuple[bool, str]:
        """Get details of a specific Outlook email.

        Calls the data source's ``me_get_message`` (single message by ID)
        which issues GET /me/messages/{id}.
        """
        try:
            response = await self.client.me_get_message(message_id=message_id)
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                data = _response_data(response)
                return True, json.dumps(data) if data is not None else json.dumps({"error": "No data"})
            else:
                return False, json.dumps({"error": response.error or "Failed to get message"})
        except Exception as e:
            return self._handle_error(e, f"get message {message_id}")

    @tool(
        app_name="outlook",
        tool_name="get_mail_folders",
        description="List mail folders in Microsoft Outlook",
        args_schema=GetMailFoldersInput,
        when_to_use=[
            "User wants to see their Outlook mail folders",
            "User asks to list email folders",
        ],
        when_not_to_use=[
            "User wants to search emails (use search_messages)",
            "No Outlook/email mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my Outlook folders",
            "List email folders",
            "What folders do I have in Outlook?",
        ],
        category=ToolCategory.COMMUNICATION,
    )
    async def get_mail_folders(
        self,
        top: Optional[int] = 20,
    ) -> tuple[bool, str]:
        """List mail folders in Outlook."""
        try:
            response = await self.client.me_list_mail_folders(
                top=min(top or 20, 100),
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                data = _response_data(response)
                raw = (data.get("value") or data.get("results")) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                folders = raw if isinstance(raw, list) else []
                return True, json.dumps({
                    "folders": folders,
                    "count": len(folders),
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to get mail folders"})
        except Exception as e:
            return self._handle_error(e, "get mail folders")

    # ------------------------------------------------------------------
    # Calendar tools
    # ------------------------------------------------------------------

    @tool(
        app_name="outlook",
        tool_name="get_calendar_events",
        description="Get calendar events from Microsoft Outlook within a date range",
        args_schema=GetCalendarEventsInput,
        when_to_use=[
            "User wants to see their Outlook calendar events",
            "User asks what meetings or events they have",
            "User wants to check their schedule in Outlook",
        ],
        when_not_to_use=[
            "User wants to create a calendar event (use create_calendar_event)",
            "User wants email (use search_messages or get_message)",
            "No Outlook/calendar mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show my Outlook calendar for this week",
            "What meetings do I have tomorrow?",
            "Get my calendar events from Monday to Friday",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_calendar_events(
        self,
        start_datetime: str,
        end_datetime: str,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """Get calendar events in a given date range."""
        try:
            response = await self.client.me_calendar_list_calendar_view(
                startDateTime=start_datetime,
                endDateTime=end_datetime,
                top=min(top or 10, 50),
            )
            #print(f"Response: {response}")
            res_json = _response_json(response)
            # print("--------------------------------")
            # print(f"Response success: {res_json}")
            # print("--------------------------------")
            if response.success:
                data = _response_data(response)
                raw = (data.get("value") or data.get("results")) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                events = raw if isinstance(raw, list) else []
                payload: Dict[str, Any] = {
                    "results": events,
                    "count": len(events),
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                }
                payload["data"] = {
                    "results": events,
                    "count": len(events),
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                }
                return True, json.dumps(payload)
            else:
                return False, json.dumps({"error": response.error or "Failed to get calendar events"})
        except Exception as e:
            return self._handle_error(e, "get calendar events")

    @tool(
        app_name="outlook",
        tool_name="search_calendar_events",
        description="Search Outlook calendar events by keyword (searches subject, body, and location)",
        args_schema=SearchCalendarEventsInput,
        when_to_use=[
            "User wants to find calendar events by keyword or phrase",
            "User asks to search for events containing specific text",
            "User wants to find events by topic, location, or description",
            "User wants to find events by recurring event and its id",
        ],
        when_not_to_use=[
            "User wants events in a date range (use get_calendar_events)",
            "User wants to create or update an event",
            "No Outlook/calendar search mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search my Outlook calendar for 'standup'",
            "Find events about project X",
            "Which meetings mention the office?",
            "Update my 'Standup' event ..."
            "Get my 'Standup' event ..."
        ],
        category=ToolCategory.CALENDAR,
    )
    async def search_calendar_events(
        self,
        search: str,
        top: Optional[int] = 10,
    ) -> tuple[bool, str]:
        """Search calendar events by keyword in subject, body, and location."""
        try:
            response = await self.client.me_search_events(
                search=search,
                top=min(top or 10, 50),
            )
            if response.success:
                data = _response_data(response)
                raw = (data.get("value") or data.get("results")) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                events = raw if isinstance(raw, list) else []
                #print("--------------------------------")
                #print(f"Events: {events}")
                #print("--------------------------------")

                return True, json.dumps({
                    "results": events,
                    "count": len(events),
                    "search": search,
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to search calendar events"})
        except Exception as e:
            return self._handle_error(e, "search calendar events")

    @tool(
        app_name="outlook",
        tool_name="create_calendar_event",
        description="Create a new calendar event in Microsoft Outlook",
        args_schema=CreateCalendarEventInput,
        when_to_use=[
            "User wants to create a meeting or calendar event in Outlook",
            "User asks to schedule a meeting",
            "User wants to add an event to their Outlook calendar",
        ],
        when_not_to_use=[
            "User wants to view calendar events (use get_calendar_events)",
            "User wants to send email (use send_email)",
            "No Outlook/calendar mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create a meeting in Outlook for tomorrow at 2pm",
            "Schedule a 1-hour event called 'Team Sync'",
            "Add a calendar event to my Outlook",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def create_calendar_event(
        self,
        subject: str,
        start_datetime: str,
        end_datetime: str,
        timezone: Optional[str] = "UTC",
        body: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        recurrence: Optional[Dict[str, Any]] = None,
        is_online_meeting: Optional[bool] = False,
    ) -> tuple[bool, str]:
        """Create a calendar event in Outlook."""
        try:
            tz = timezone or "UTC"
            event_body: Dict[str, Any] = {
                "subject": subject,
                "start": {
                    "dateTime": start_datetime,
                    "timeZone": tz,
                },
                "end": {
                    "dateTime": end_datetime,
                    "timeZone": tz,
                },
                "isOnlineMeeting": bool(is_online_meeting),
            }

            if body:
                event_body["body"] = {
                    "contentType": "Text",
                    "content": body,
                }

            if location:
                event_body["location"] = {"displayName": location}

            if attendees:
                event_body["attendees"] = [
                    {
                        "emailAddress": {"address": addr.strip()},
                        "type": "required",
                    }
                    for addr in attendees
                    if addr.strip()
                ]

            if recurrence:
                event_body["recurrence"] = _build_recurrence_body(recurrence)
            
            #print("--------------------------------")
            #print(f"Event body: {event_body}")
            #print("--------------------------------")
            response = await self.client.me_calendar_create_events(request_body=event_body)
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                return True, _response_json(response)
            else:
                return False, json.dumps({"error": response.error or "Failed to create calendar event"})
        except Exception as e:
            return self._handle_error(e, "create calendar event")

    @tool(
        app_name="outlook",
        tool_name="get_calendar_event",
        description="Get details of a specific Outlook calendar event",
        args_schema=GetCalendarEventInput,
        when_to_use=[
            "User wants to see details of a specific Outlook calendar event",
            "User has an event ID and wants to view it",
        ],
        when_not_to_use=[
            "User wants to list events (use get_calendar_events)",
            "User wants to create an event (use create_calendar_event)",
            "No Outlook/calendar mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get details of this Outlook event",
            "Show me the meeting details",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_calendar_event(
        self,
        event_id: str,
    ) -> tuple[bool, str]:
        """Get details of a specific Outlook calendar event."""
        try:
            response = await self.client.me_calendar_get_events(event_id=event_id)
            #print(f"Response: {response}")
            res_json = _response_json(response)
            #print("--------------------------------")
            #print(f"Response success: {res_json}")
            #print("--------------------------------")
            if response.success:
                data = _response_data(response)
                return True, json.dumps(data) if data is not None else json.dumps({"error": "No data"})
            else:
                return False, json.dumps({"error": response.error or "Failed to get calendar event"})
        except Exception as e:
            return self._handle_error(e, f"get calendar event {event_id}")

    @tool(
        app_name="outlook",
        tool_name="update_calendar_event",
        description="Update an existing calendar event in Microsoft Outlook (change subject, time, attendees, location, etc.)",
        args_schema=UpdateCalendarEventInput,
        when_to_use=[
            "User wants to update or modify an existing Outlook calendar event",
            "User wants to add/remove attendees from an existing meeting",
            "User wants to change the time, location, or subject of a meeting",
            "User wants to reschedule a meeting",
        ],
        when_not_to_use=[
            "User wants to create a new event (use create_calendar_event)",
            "User wants to delete an event (use delete_calendar_event)",
            "User wants to list events (use get_calendar_events)",
            "No Outlook/calendar mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Add john@example.com to my 2pm meeting",
            "Change the meeting to 3pm",
            "Update the meeting subject to 'Sprint Review'",
            "Reschedule tomorrow's meeting to Friday",
            "Extend the meeting to 4pm",
            "Extend the recurring meeting by 15 days",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def update_calendar_event(
        self,
        event_id: str,
        subject: Optional[str] = None,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None,
        timezone: Optional[str] = None,
        body: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        is_online_meeting: Optional[bool] = None,
        recurrence: Optional[Dict[str, Any]] = None,
        update_scope: str = "allEvents",
    ) -> tuple[bool, str]:
        """Update an existing calendar event in Outlook."""
        try:
            # Normalize body/location/attendees when passed as API-shaped data from get_calendar_events
            if body is not None and isinstance(body, dict):
                body = body.get("content") or body.get("body") or ""
            if body is not None and not isinstance(body, str):
                body = str(body)
            if location is not None and isinstance(location, dict):
                location = location.get("displayName") or location.get("location") or ""
            if location is not None and not isinstance(location, str):
                location = str(location)
            if attendees is not None:
                _normalized: List[str] = []
                for a in attendees:
                    if isinstance(a, str) and a.strip():
                        _normalized.append(a.strip())
                    elif isinstance(a, dict):
                        addr = a.get("emailAddress")
                        if isinstance(addr, dict):
                            email = addr.get("address")
                            if isinstance(email, str) and email.strip():
                                _normalized.append(email.strip())
                        elif isinstance(addr, str) and addr.strip():
                            _normalized.append(addr.strip())
                attendees = _normalized if _normalized else None

            event_body: Dict[str, Any] = {}

            if subject is not None:
                event_body["subject"] = subject

            tz = timezone or "UTC"
            if start_datetime is not None:
                event_body["start"] = {
                    "dateTime": start_datetime,
                    "timeZone": tz,
                }
            if end_datetime is not None:
                event_body["end"] = {
                    "dateTime": end_datetime,
                    "timeZone": tz,
                }

            if body is not None:
                event_body["body"] = {
                    "contentType": "Text",
                    "content": body,
                }

            if location is not None:
                event_body["location"] = {"displayName": location}

            if attendees is not None:
                event_body["attendees"] = [
                    {
                        "emailAddress": {"address": addr.strip()},
                        "type": "required",
                    }
                    for addr in attendees
                    if addr.strip()
                ]

            if is_online_meeting is not None:
                event_body["isOnlineMeeting"] = bool(is_online_meeting)

            if recurrence is not None and isinstance(recurrence, dict) and "pattern" in recurrence and "range" in recurrence:
                event_body["recurrence"] = _build_recurrence_body(recurrence)

            if not event_body:
                return False, json.dumps({"error": "No fields provided to update"})

            response = await self.client.me_calendar_update_events(
                event_id=event_id,
                request_body=event_body,
                update_scope=update_scope,
            )
            if response.success:
                data = _response_json(response)
                return True, json.dumps({
                    "message": "Calendar event updated successfully",
                    "event_id": event_id,
                    "event": data,
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to update calendar event"})
        except Exception as e:
            return self._handle_error(e, f"update calendar event {event_id}")

    @tool(
        app_name="outlook",
        tool_name="delete_calendar_event",
        description="Delete a calendar event from Microsoft Outlook",
        args_schema=DeleteCalendarEventInput,
        when_to_use=[
            "User wants to delete or cancel an Outlook calendar event",
            "User wants to remove a meeting from their calendar",
        ],
        when_not_to_use=[
            "User wants to update an event (use update_calendar_event)",
            "User wants to create an event (use create_calendar_event)",
            "No Outlook/calendar mention",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Delete my 2pm meeting",
            "Cancel the meeting with John",
            "Remove this event from my calendar",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def delete_calendar_event(
        self,
        event_id: str,
    ) -> tuple[bool, str]:
        """Delete a calendar event from Outlook."""
        try:
            response = await self.client.me_calendar_delete_events(event_id=event_id)
            if response.success:
                return True, json.dumps({
                    "message": "Calendar event deleted successfully",
                    "event_id": event_id,
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to delete calendar event"})
        except Exception as e:
            return self._handle_error(e, f"delete calendar event {event_id}")

    @tool(
        app_name="outlook",
        tool_name="get_recurring_events_ending",
        description=(
            "Get recurring calendar events whose recurrence series ends within a "
            "specified time frame. Useful for finding recurring meetings that are "
            "about to end or have recently ended."
        ),
        args_schema=GetRecurringEventsEndingInput,
        when_to_use=[
            "User wants to find recurring events that are ending soon",
            "User wants to know which recurring meetings are expiring",
            "User wants to review recurring events ending in a date range",
        ],
        when_not_to_use=[
            "User wants all events in a date range (use get_calendar_events)",
            "User wants to create or update an event",
            "User wants non-recurring events",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Which recurring meetings are ending this month?",
            "Show me recurring events expiring before March 31",
            "Find recurring meetings ending soon",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_recurring_events_ending(
        self,
        end_before: str,
        end_after: Optional[str] = None,
        timezone: str = "UTC",
        top: int = 10,
    ) -> tuple[bool, str]:
        try:
            now = datetime.now(tz.utc)
            range_start = end_after or now.strftime("%Y-%m-%dT%H:%M:%SZ")
            range_end = end_before

            try:
                dt_range_start = datetime.fromisoformat(range_start.replace("Z", "+00:00")).date()
                dt_range_end = datetime.fromisoformat(range_end.replace("Z", "+00:00")).date()
            except ValueError as e:
                return False, json.dumps({"error": f"Invalid datetime format: {e}"})

            if dt_range_start > dt_range_end:
                return False, json.dumps({"error": "end_after must be before end_before."})

            # ── Step 1: Paginate all seriesMaster events ──────────────────────────
            all_series_masters = []
            page_size, max_pages, skip = 50, 20, 0

            for _ in range(max_pages):
                resp = await self.client.me_list_series_master_events(
                    top=page_size, skip=skip, timezone=timezone,
                )
                if not resp.success:
                    return False, json.dumps({"error": resp.error or "Failed to fetch recurring events"})

                data   = _response_data(resp)
                events = data.get("value", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

                all_series_masters.extend(events)
                if len(events) < page_size:
                    break
                skip += page_size

            if not all_series_masters:
                return True, json.dumps({
                    "results": [], "count": 0,
                    "message": "No recurring events found.",
                    "range": {"end_after": range_start, "end_before": range_end},
                })

            # ── Step 2: Filter on recurrence.range.endDate ────────────────────────
            def get_end_date(ev: dict) -> Optional[date]:
                rec_range = (ev.get("recurrence") or {}).get("range", {})
                if rec_range.get("type") not in ("endDate",):
                    return None
                try:
                    return date.fromisoformat(rec_range["endDate"])
                except (KeyError, ValueError):
                    return None

            matched = sorted(
                (
                    {**ev, "_recurrenceEndDate": (end_date := get_end_date(ev)).isoformat()}
                    for ev in all_series_masters
                    if isinstance(ev, dict)
                    and (end_date := get_end_date(ev)) is not None
                    and dt_range_start <= end_date <= dt_range_end
                ),
                key=lambda e: e["_recurrenceEndDate"],
            )[:top]

            return True, json.dumps({
                "results": matched,
                "count":   len(matched),
                "range":   {"end_after": range_start, "end_before": range_end},
            })

        except Exception as e:
            return self._handle_error(e, "get recurring events ending")
    
    @tool(
        app_name="outlook",
        tool_name="get_free_time_slots",
        description=(
            "Get the user's free (available) time slots within a given time frame. "
            "Returns gaps between calendar events as free slots."
        ),
        args_schema=GetFreeTimeSlotsInput,
        when_to_use=[
            "User wants to know when they are free in a time period",
            "User asks for available slots for a meeting",
            "User wants to find gaps between their meetings",
            "User wants to schedule something and needs to know availability",
        ],
        when_not_to_use=[
            "User wants to see their events (use get_calendar_events)",
            "User wants to create a meeting (use create_calendar_event)",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "When am I free today?",
            "Find me a free 1 hour slot tomorrow",
            "What are my available slots this afternoon?",
            "Am I free between 2pm and 5pm today?",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_free_time_slots(
        self,
        start_datetime: str,
        end_datetime: str,
        timezone: str = "UTC",
        slot_duration_minutes: int = 30,
    ) -> tuple[bool, str]:
        try:
            from datetime import datetime, timedelta

            #print(f"[get_free_time_slots] range={start_datetime!r}..{end_datetime!r}, tz={timezone!r}, slot={slot_duration_minutes}min")

            # Graph API supports only 15, 30, 60 for availabilityViewInterval
            interval_minutes = 60 if slot_duration_minutes >= 60 else 30 if slot_duration_minutes >= 30 else 15
            #print(f"[get_free_time_slots] using availabilityViewInterval={interval_minutes}min")

            resp = await self.client.me_get_schedule(
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                timezone=timezone,
                availability_view_interval=interval_minutes,
            )

            if not resp.success:
                return False, json.dumps({"error": resp.error or "Failed to fetch schedule"})

            data = _response_data(resp)
            schedules = (
                data.get("value", []) if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )

            if not schedules:
                return False, json.dumps({"error": "No schedule data returned."})

            schedule = schedules[0] if isinstance(schedules, list) else schedules
            if not isinstance(schedule, dict):
                return False, json.dumps({"error": "Unexpected schedule format."})

            availability_view = schedule.get("availabilityView", "")
            #print(f"[get_free_time_slots] availabilityView={availability_view!r} (len={len(availability_view)})")

            if not availability_view:
                return False, json.dumps({"error": "No availabilityView returned from Graph API."})

            # Compute actual interval from availabilityView length vs requested range
            # This handles cases where Graph returns a different granularity than requested
            def _parse_dt_naive(s: str) -> datetime:
                return datetime.fromisoformat(s[:19])  # "YYYY-MM-DDTHH:MM:SS", no tz

            range_start = _parse_dt_naive(start_datetime)
            range_end = _parse_dt_naive(end_datetime)
            total_minutes = int((range_end - range_start).total_seconds() / 60)
            actual_interval_minutes = total_minutes // len(availability_view)

            #print(f"[get_free_time_slots] total_minutes={total_minutes}, slots={len(availability_view)}, actual_interval={actual_interval_minutes}min")

            interval_delta = timedelta(minutes=actual_interval_minutes)
            slot_delta = timedelta(minutes=slot_duration_minutes)
            FREE_STATUSES = {"0"}

            free_slots = []
            busy_periods = []
            current_free_start = None
            current_busy_start = None

            for i, status in enumerate(availability_view):
                slot_start = range_start + (i * interval_delta)
                slot_end = slot_start + interval_delta
                is_free = status in FREE_STATUSES

                if is_free:
                    if current_busy_start is not None:
                        busy_periods.append({
                            "start": current_busy_start.isoformat(),
                            "end": slot_start.isoformat(),
                            "duration_minutes": int((slot_start - current_busy_start).total_seconds() / 60),
                            "status": _status_label(availability_view[i - 1]),
                            "timezone": timezone,
                        })
                        current_busy_start = None
                    if current_free_start is None:
                        current_free_start = slot_start
                else:
                    if current_free_start is not None:
                        gap_duration = slot_start - current_free_start
                        if gap_duration >= slot_delta:
                            free_slots.append({
                                "start": current_free_start.isoformat(),
                                "end": slot_start.isoformat(),
                                "duration_minutes": int(gap_duration.total_seconds() / 60),
                                "timezone": timezone,
                            })
                        current_free_start = None
                    if current_busy_start is None:
                        current_busy_start = slot_start

            # Close trailing free window
            if current_free_start is not None:
                gap_duration = range_end - current_free_start
                if gap_duration >= slot_delta:
                    free_slots.append({
                        "start": current_free_start.isoformat(),
                        "end": range_end.isoformat(),
                        "duration_minutes": int(gap_duration.total_seconds() / 60),
                        "timezone": timezone,
                    })

            # Close trailing busy window
            if current_busy_start is not None:
                busy_periods.append({
                    "start": current_busy_start.isoformat(),
                    "end": range_end.isoformat(),
                    "duration_minutes": int((range_end - current_busy_start).total_seconds() / 60),
                    "status": _status_label(availability_view[-1]),
                    "timezone": timezone,
                })

            #print(f"[get_free_time_slots] free_slots={len(free_slots)}, busy_periods={len(busy_periods)}")

            return True, json.dumps({
                "free_slots": free_slots,
                "count": len(free_slots),
                "busy_periods": busy_periods,
                "range": {
                    "start": start_datetime,
                    "end": end_datetime,
                    "timezone": timezone,
                },
                "slot_duration_minutes": slot_duration_minutes,
                "actual_interval_minutes": actual_interval_minutes,
                "availability_view": availability_view,
            })

        except Exception as e:
            #print(f"[get_free_time_slots] exception: {e!r}")
            return self._handle_error(e, "get free time slots")

    @tool(
            app_name="outlook",
            tool_name="delete_recurring_event_occurrence",
            description=(
                "Take a list of dates and delete the occurrences of the recurring event on those dates."
            ),
            args_schema=DeleteRecurringEventOccurrencesInput,
            when_to_use=[
                "User wants to cancel one or more occurrences of a recurring meeting",
                "User wants to skip a recurring meeting on specific dates",
                "User wants to delete instances without affecting the whole series",
            ],
            when_not_to_use=[
                "User wants to delete the entire series (use delete_calendar_event)",
                "User wants to update the occurrence (use update_calendar_event)",
                "User wants to skip weekends/holidays on all future occurrences (use update_recurring_event_with_exclusion)",
            ],
            primary_intent=ToolIntent.ACTION,
            typical_queries=[
                "Cancel the standup on March 10 and March 17",
                "Skip the catchup meeting this Friday and next Friday",
                "Delete the March 25 and April 1 occurrences of my weekly sync",
                "Delete the occurrences of the recurring event on Holidays",
                "Delete the occurrences of the recurring event on weekends",
            ],
            llm_description=(
                "Used when deleting the occurrences of the recurring event on specific dates. "
                "Takes a list of dates and deletes the occurrences of the recurring event on those dates."
            ),
            category=ToolCategory.CALENDAR,
        )
    async def delete_recurring_event_occurrence(
        self,
        event_id: str,
        occurrence_dates: List[str],
        timezone: str = "UTC",
    ) -> tuple[bool, str]:
        """Delete multiple occurrences of a recurring event on specific dates.

        Optimizations applied:
        1. Concurrent chunk fetching for occurrences (read-only, safe)
        2. Enhanced trailing block detection — trims as many dates as possible
           via a single end-date update instead of individual deletes
        3. Sequential deletion with per-request timeout for remaining dates
        4. Retry once on 412 conflicts (re-fetch occurrence ID and retry)
        """
        try:
            import asyncio
            from datetime import date, timedelta

            # ── Normalise & deduplicate ───────────────────────────────────────
            target_dates = sorted({d.strip() for d in occurrence_dates})
            target_set   = set(target_dates)
            parsed_dates = [date.fromisoformat(d) for d in target_dates]

            if not parsed_dates:
                return True, json.dumps({
                    "success": True, "series_master_id": event_id,
                    "deleted": [], "not_found": [], "errors": [],
                    "summary": {"requested": 0, "deleted": 0, "trimmed_via_end_date": 0, "not_found": 0, "failed": 0},
                })

            range_start = min(parsed_dates)
            range_end   = max(parsed_dates)

            # ── Fetch series master ───────────────────────────────────────────
            master_resp = await self.client.me_calendar_get_events(event_id=event_id)

            # ── Fetch ALL occurrences in chunks (CONCURRENT — read-only) ──────
            CHUNK_DAYS = 31
            chunks: list[tuple[str, str]] = []
            cs = range_start - timedelta(days=1)
            ce_limit = range_end + timedelta(days=1)
            while cs < ce_limit:
                ce = min(cs + timedelta(days=CHUNK_DAYS), ce_limit)
                chunks.append((cs.isoformat(), ce.isoformat()))
                cs = ce

            logger.info(
                f"delete_recurring_event_occurrence: fetching occurrences in "
                f"{len(chunks)} chunk(s) for {len(target_dates)} target dates "
                f"({range_start} → {range_end})"
            )

            async def _fetch_chunk(start_iso: str, end_iso: str) -> list:
                try:
                    resp = await asyncio.wait_for(
                        self.client.me_list_event_occurrences(
                            event_id=event_id,
                            start_date=start_iso,
                            end_date=end_iso,
                            timezone=timezone,
                        ),
                        timeout=30.0,
                    )
                    if resp.success:
                        occ_data = _response_data(resp)
                        return (
                            occ_data.get("value", []) if isinstance(occ_data, dict)
                            else (occ_data if isinstance(occ_data, list) else [])
                        )
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout fetching chunk {start_iso} → {end_iso}")
                except Exception as e:
                    logger.warning(f"Error fetching chunk {start_iso} → {end_iso}: {e}")
                return []

            chunk_results = await asyncio.gather(*[_fetch_chunk(s, e) for s, e in chunks])

            all_occurrences = []
            for r in chunk_results:
                all_occurrences.extend(r)

            logger.info(f"Fetched {len(all_occurrences)} occurrences across {len(chunks)} chunks")

            # ── Build date → occurrence mapping ───────────────────────────────
            date_to_occ: Dict[str, Any] = {}
            for occ in all_occurrences:
                if isinstance(occ, dict) and occ.get("start", {}).get("dateTime"):
                    occ_date = occ["start"]["dateTime"][:10]
                    if occ_date not in date_to_occ:
                        date_to_occ[occ_date] = occ

            not_found = [d for d in target_dates if d not in date_to_occ]

            # ── Resolve series metadata ───────────────────────────────────────
            series_end_date: Optional[date] = None
            series_recurrence: Optional[Dict] = None

            if master_resp and master_resp.success:
                master_data = _response_data(master_resp)
                master_event = master_data if isinstance(master_data, dict) else {}
                recurrence = master_event.get("recurrence") or {}
                rec_range  = recurrence.get("range") or {}
                series_recurrence = recurrence
                if rec_range.get("type") == "endDate" and rec_range.get("endDate"):
                    try:
                        series_end_date = date.fromisoformat(rec_range["endDate"])
                    except ValueError:
                        pass

            # ── ENHANCED trailing block optimization ──────────────────────────
            # Walk backwards from the series end date. Every date that is either:
            #   a) in the target set (needs deletion), OR
            #   b) not in date_to_occ (already deleted / no occurrence exists)
            # can be "trimmed" by moving the end date back.
            # Stop at the first date that is NOT in target AND HAS an occurrence
            # (i.e., a date the user wants to KEEP).
            trailing_block: List[str] = []

            if series_end_date and series_recurrence:
                check_date = series_end_date
                earliest_target = range_start

                while check_date >= earliest_target:
                    d_str = check_date.isoformat()

                    if d_str in target_set:
                        # This date is targeted for deletion → part of trailing block
                        trailing_block.append(d_str)
                    elif d_str not in date_to_occ:
                        # No occurrence exists on this date (already deleted or
                        # never existed, e.g., event doesn't recur on this day)
                        # → can safely skip over it
                        pass
                    else:
                        # This date HAS an occurrence AND is NOT targeted
                        # → user wants to keep it → trailing block ends here
                        break

                    check_date -= timedelta(days=1)

                trailing_block = list(reversed(trailing_block))

            use_end_date_update = bool(trailing_block) and series_recurrence is not None

            end_date_result: Optional[Dict] = None
            to_delete_individually = target_dates

            if use_end_date_update:
                trailing_set = set(trailing_block)
                to_delete_individually = [d for d in target_dates if d not in trailing_set]

                # New end date = day before the earliest date in the trailing block
                new_end_date = (date.fromisoformat(trailing_block[0]) - timedelta(days=1)).isoformat()

                updated_recurrence = {
                    **series_recurrence,
                    "range": {
                        **series_recurrence.get("range", {}),
                        "endDate": new_end_date,
                    },
                }

                update_resp = await self.client.me_calendar_update_events(
                    event_id=event_id,
                    request_body={"recurrence": updated_recurrence},
                )

                end_date_result = {
                    "optimisation": "end_date_updated",
                    "previous_end_date": series_end_date.isoformat() if series_end_date else None,
                    "new_end_date": new_end_date,
                    "occurrences_trimmed": len(trailing_block),
                    "success": update_resp.success,
                    "error": None if update_resp.success else update_resp.error,
                }

                logger.info(
                    f"Enhanced trailing block: trimmed {len(trailing_block)} occurrences "
                    f"via end-date update ({series_end_date} → {new_end_date}). "
                    f"Remaining to delete individually: {len(to_delete_individually)}"
                )

            # ── Delete remaining occurrences SEQUENTIALLY ─────────────────────
            # Must be sequential — concurrent deletes cause HTTP 412 conflicts
            # because each deletion changes the series changeKey.
            deleted = []
            delete_errors = []

            dates_to_delete = [d for d in to_delete_individually if d in date_to_occ]

            if dates_to_delete:
                logger.info(f"Deleting {len(dates_to_delete)} occurrences sequentially")

            for target_date_str in dates_to_delete:
                occ = date_to_occ[target_date_str]
                occ_id = occ.get("id")

                try:
                    resp = await asyncio.wait_for(
                        self.client.me_calendar_delete_events(event_id=occ_id),
                        timeout=15.0,
                    )
                    if resp.success:
                        deleted.append({
                            "date": target_date_str,
                            "event_id": occ_id,
                            "subject": occ.get("subject", ""),
                        })
                    else:
                        delete_errors.append({
                            "date": target_date_str,
                            "event_id": occ_id,
                            "error": resp.error,
                        })
                except asyncio.TimeoutError:
                    delete_errors.append({
                        "date": target_date_str,
                        "event_id": occ_id,
                        "error": "Timeout after 15s",
                    })
                except Exception as e:
                    delete_errors.append({
                        "date": target_date_str,
                        "event_id": occ_id,
                        "error": str(e),
                    })

            trimmed_count = len(trailing_block) if use_end_date_update else 0

            logger.info(
                f"delete_recurring_event_occurrence complete: "
                f"requested={len(target_dates)}, deleted={len(deleted)}, "
                f"trimmed={trimmed_count}, "
                f"not_found={len(not_found)}, errors={len(delete_errors)}"
            )

            return True, json.dumps({
                "success": True,
                "series_master_id": event_id,
                "deleted": deleted,
                "not_found": not_found,
                "errors": delete_errors,
                **({"end_date_update": end_date_result} if end_date_result else {}),
                "summary": {
                    "requested": len(target_dates),
                    "deleted": len(deleted),
                    "trimmed_via_end_date": trimmed_count,
                    "not_found": len(not_found),
                    "failed": len(delete_errors),
                },
            })

        except Exception as e:
            return self._handle_error(e, "delete recurring event occurrence")
            
    # Online Meetings & Transcripts tools
    # ------------------------------------------------------------------

    # @tool(
    #     app_name="outlook",
    #     tool_name="list_online_meetings",
    #     description=(
    #         "List Microsoft Teams / online meetings for the signed-in user. "
    #         "Uses the same calendar events datasource as get_calendar_event and returns "
    #         "only events that are online meetings. For date range filtering use "
    #         "get_calendar_events instead."
    #     ),
    #     args_schema=ListOnlineMeetingsInput,
    #     when_to_use=[
    #         "User wants to see their online or Teams meetings",
    #         "User asks what Teams meetings they have",
    #         "User wants to find a specific online meeting by its join URL",
    #     ],
    #     when_not_to_use=[
    #         "User wants calendar events in a date range (use get_calendar_events)",
    #         "User wants to filter meetings by date/time (use get_calendar_events)",
    #         "User wants to create a meeting (use create_calendar_event)",
    #         "No Outlook/Teams/meeting mention",
    #     ],
    #     primary_intent=ToolIntent.SEARCH,
    #     typical_queries=[
    #         "List my Teams meetings",
    #         "Show my online meetings",
    #         "Find my recent Teams calls",
    #     ],
    #     category=ToolCategory.CALENDAR,
    # )
    # async def list_online_meetings(
    #     self,
    #     filter: Optional[str] = None,
    #     top: Optional[int] = 10,
    # ) -> tuple[bool, str]:
    #     """List online meetings for the signed-in user from calendar events."""
    #     try:
    #         # Use same datasource as get_calendar_event: calendar view (calendar events)
    #         now = datetime.now(timezone.utc)
    #         start_dt = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    #         end_dt = (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    #         # Do not pass filter to calendar view: Event type has no JoinWebUrl (that's
    #         # for /me/onlineMeetings). We filter to online meetings in code below.
    #         response = await self.client.me_calendar_list_calendar_view(
    #             startDateTime=start_dt,
    #             endDateTime=end_dt,
    #             top=min(top or 10, 50),
    #         )
    #         if not response.success:
    #             return False, json.dumps({"error": response.error or "Failed to list calendar events"})

    #         data = _response_data(response)
    #         if isinstance(data, dict):
    #             raw = data.get("value") or data.get("results") or []
    #         elif isinstance(data, list):
    #             raw = data
    #         else:
    #             raw = []
    #         all_events = raw if isinstance(raw, list) else []

    #         def is_online_meeting(ev: Any) -> bool:
    #             if not isinstance(ev, dict):
    #                 return False
    #             if ev.get("isOnlineMeeting") is True:
    #                 return True
    #             if ev.get("onlineMeeting") is not None:
    #                 return True
    #             return False

    #         meetings = [ev for ev in all_events if is_online_meeting(ev)]

    #         # Include "results" so placeholders like {{outlook.list_online_meetings.data.results[0].id}} resolve
    #         payload: Dict[str, Any] = {
    #             "meetings": meetings,
    #             "results": meetings,
    #             "count": len(meetings),
    #         }
    #         payload["data"] = {"results": meetings, "count": len(meetings)}
    #         return True, json.dumps(payload)
    #     except Exception as e:
    #         return self._handle_error(e, "list online meetings")

    @tool(
        app_name="outlook",
        tool_name="get_meeting_transcripts",
        description=(
            "Get the transcripts for a Microsoft Teams online meeting. "
            "Accepts either an ical_uid (iCalUId from a calendar event) or "
            "an event_id (calendar event ID). iCalUId is preferred if available "
            "as it skips an extra API call. Returns parsed transcript text with "
            "speaker names and timestamps."
        ),
        args_schema=GetMeetingTranscriptsInput,
        when_to_use=[
            "User wants to see the transcript of a Teams meeting",
            "User asks what was said in a meeting",
            "User wants meeting notes or minutes from a Teams call",
        ],
        when_not_to_use=[
            "User wants to list meetings (use list_online_meetings or get_calendar_events)",
            "User wants to create a meeting (use create_calendar_event)",
            "No transcript/meeting-content mention",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get the transcript of my last Teams meeting",
            "Show me what was discussed in the meeting",
            "Fetch meeting transcript for meeting ID ...",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_meeting_transcripts(
        self,
        event_id: Optional[str] = None,
        join_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Fetch all transcripts for an online meeting.
        Preferred: join_url (skips one API call).
        Fallback:  event_id (fetches event first to extract joinUrl).
        """
        try:
            # Step 1: Resolve to online meeting ID
            meeting_id = await self._resolve_to_online_meeting_id(
                event_id=event_id,
                join_url=join_url,
            )
            if not meeting_id:
                return False, json.dumps({
                    "error": (
                        "Could not resolve the event to a Teams online meeting. "
                        "The event may not be a Teams meeting, or you may lack "
                        "OnlineMeetings.Read permission."
                    )
                })

            # Step 2: List transcripts
            list_resp = await self.client.me_list_online_meeting_transcripts(
                onlineMeeting_id=meeting_id,
            )
            if not list_resp.success:
                return False, json.dumps({"error": list_resp.error or "Failed to list transcripts"})

            data = _response_data(list_resp)
            transcript_items = (
                data.get("value", []) if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )

            if not transcript_items:
                return True, json.dumps({
                    "message": "No transcripts available for this meeting",
                    "transcripts": [],
                })

            # Step 3: Fetch metadataContent for each transcript
            all_transcripts = []
            for t_obj in transcript_items:
                t_id = (
                    t_obj.id if hasattr(t_obj, "id")
                    else (t_obj.get("id") if isinstance(t_obj, dict) else None)
                )
                if not t_id:
                    continue

                created = (
                    str(t_obj.created_date_time) if hasattr(t_obj, "created_date_time")
                    else (t_obj.get("createdDateTime") if isinstance(t_obj, dict) else None)
                )

                parsed_entries = []
                meta_resp = await self.client.me_get_online_meeting_transcript_metadata(
                    onlineMeeting_id=meeting_id,
                    callTranscript_id=t_id,
                )
                if meta_resp.success:
                    meta_data = _response_data(meta_resp)
                    meta_text = meta_data.get("content", "") if isinstance(meta_data, dict) else ""
                    if meta_text:
                        parsed_entries = self._parse_metadata_json(meta_text)

                all_transcripts.append({
                    "transcript_id": t_id,
                    "created": created,
                    "entries": parsed_entries,
                    "entry_count": len(parsed_entries),
                })

            return True, json.dumps({
                "meeting_id": meeting_id,
                "transcripts": all_transcripts,
                "transcript_count": len(all_transcripts),
            })

        except Exception as e:
            return self._handle_error(e, "get meeting transcripts")

    async def _resolve_to_online_meeting_id(
        self,
        event_id: Optional[str] = None,
        join_url: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve to an online meeting ID.

        Path A (join_url provided) — 1 API call:
            GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'

        Path B (event_id provided) — 2 API calls:
            GET /me/events/{event_id}  →  extract joinUrl
            GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
        """
        try:
            # Path A: join_url provided directly — skip event fetch
            if join_url:
                result = await self._online_meeting_id_from_join_url(join_url)
                return result

            # Path B: event_id provided — fetch event to extract joinUrl
            if event_id:
                ev_resp = await self.client.me_calendar_get_events(event_id=event_id)
                if not ev_resp.success:
                    return None

                ev = _response_data(ev_resp)
                if not isinstance(ev, dict):
                    return None

                om = ev.get("onlineMeeting") or ev.get("online_meeting")
                if not isinstance(om, dict):
                    return None

                extracted_join_url = (
                    om.get("joinWebUrl")
                    or om.get("joinUrl")
                    or om.get("join_web_url")
                    or om.get("join_url")
                )
                if not extracted_join_url or not isinstance(extracted_join_url, str):
                    return None

                result = await self._online_meeting_id_from_join_url(extracted_join_url)
                return result
            return None

        except Exception as e:
            return None


    async def _online_meeting_id_from_join_url(self, join_url: str) -> Optional[str]:
        """Resolve a Teams joinWebUrl to an online meeting ID.

        GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
        NOTE: join_url must be URL-decoded before filtering — Graph API 
        returns 400 if the URL contains percent-encoded characters.
        """
        try:
            from urllib.parse import unquote
            decoded_url = unquote(join_url)
            safe_url = decoded_url.replace("'", "''")
            resp = await self.client.me_list_online_meetings(
                filter=f"joinWebUrl eq '{safe_url}'",
            )
            if not resp.success:
                return None

            data = _response_data(resp)
            items = (
                data.get("value") or data.get("results", [])
                if isinstance(data, dict)
                else (data if isinstance(data, list) else [])
            )
            if not items:
                return None

            first = items[0]
            return (
                first.get("id") if isinstance(first, dict)
                else getattr(first, "id", None)
            )

        except Exception:
            return None

    @staticmethod
    def _parse_metadata_json(meta_text: str) -> List[Dict[str, str]]:
        """Parse metadataContent (speaker diarization JSON lines) into entries."""
        entries: List[Dict[str, str]] = []
        for line in meta_text.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                    speaker = obj.get("speakerName", "Unknown")
                    text = obj.get("spokenText", "")
                    if text:
                        entries.append({"timestamp": "", "speaker": speaker, "text": text})
                except json.JSONDecodeError:
                    pass
        return entries