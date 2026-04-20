import contextlib
import json
import logging
from collections import OrderedDict
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
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
from app.connectors.core.registry.types import AuthField, DocumentationLink
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
        return {key: _serialize_graph_obj(value) for key, value in obj.items()}

    # Kiota Parsable objects expose get_field_deserializers()
    if hasattr(obj, "get_field_deserializers"):
        try:
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
            backing_store_ref = getattr(obj, "backing_store", None)
            if backing_store_ref is not None and hasattr(backing_store_ref, "enumerate_"):
                result: Dict[str, Any] = {}
                for key, value in backing_store_ref.enumerate_():
                    if not str(key).startswith("_"):
                        try:
                            result[key] = _serialize_graph_obj(value)
                        except Exception:
                            result[key] = str(value)
                additional = getattr(obj, "additional_data", None)
                if isinstance(additional, dict):
                    for key, value in additional.items():
                        if key not in result:
                            try:
                                result[key] = _serialize_graph_obj(value)
                            except Exception:
                                result[key] = str(value)
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
    for key, value in obj_dict.items():
        if key.startswith("_"):
            continue
        try:
            result[key] = _serialize_graph_obj(value)
        except Exception:
            result[key] = str(value)

    additional = getattr(obj, "additional_data", None)
    if isinstance(additional, dict):
        for key, value in additional.items():
            if key not in result:
                try:
                    result[key] = _serialize_graph_obj(value)
                except Exception:
                    result[key] = str(value)

    return result if result else str(obj)


def _normalize_odata(data: Any) -> Any:
    """Normalize OData response keys so cascading placeholders resolve reliably.

    MS Graph returns collections under a ``value`` key, but LLM planners
    commonly guess ``results``.  We keep ``value`` intact and add a
    ``results`` alias pointing to the same list so both paths work.
    """
    if isinstance(data, dict) and "value" in data and isinstance(data["value"], list) and "results" not in data:
        data["results"] = data["value"]
    return data


def _response_data(response: object) -> Any:
    """Serialize response.data the same way _response_data does. Returns Python dict/list/None."""
    data = getattr(response, "data", None)
    if data is None:
        return None
    return _normalize_odata(_serialize_graph_obj(data))

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


class GetRecurringEventsInput(BaseModel):
    """Schema for fetching all recurring events with occurrences in a date range."""
    start_date: Optional[str] = Field(
        default=None,
        description=(
            "Start of the date range in ISO 8601 format (e.g. '2026-03-09T00:00:00Z'). "
            "Defaults to now if not provided."
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description=(
            "End of the date range in ISO 8601 format (e.g. '2026-04-08T23:59:59Z'). "
            "Defaults to 30 days from now if not provided."
        ),
    )
    timezone: str = Field(
        default="UTC",
        description="Windows timezone name for returned datetimes. E.g. 'India Standard Time'.",
    )
    top: int = Field(
        default=50,
        description="Maximum number of recurring event series to return (1–100).",
        ge=1,
        le=100,
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
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/outlook.svg")
        .add_documentation_link(DocumentationLink(
                    title="Create an Azure App Registration",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
                    doc_type="setup",
        ))
        .add_documentation_link(DocumentationLink(
                    title="Microsoft Graph Mail & Calendar permissions",
                    url="https://learn.microsoft.com/en-us/graph/permissions-reference",
                    doc_type="reference",
        ))
        .add_documentation_link(DocumentationLink(
                    title="Configure OAuth 2.0 redirect URIs",
                    url="https://learn.microsoft.com/en-us/entra/identity-platform/reply-url",
                    doc_type="setup",
        ))
        .add_documentation_link(DocumentationLink(
            title="Pipeshub Documentation",
            url="https://docs.pipeshub.com/toolsets/overview",
            doc_type="pipeshub",
    )))\
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

        if isinstance(error, AttributeError) and ("client" in error_msg or "me" in error_msg):
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
            return {key: Outlook._serialize_response(value) for key, value in response_obj.items()}

        # ── Kiota Parsable objects ────────────────────────────────────────────
        # Kiota models implement get_field_deserializers() as part of the
        # Parsable interface.  Use kiota's JsonSerializationWriter to produce a
        # proper camelCase dict (id, subject, isOnlineMeeting, …) so that
        # placeholder paths like {{…events[0].id}} resolve correctly.
        if hasattr(response_obj, "get_field_deserializers"):
            try:
                writer = JsonSerializationWriter()
                writer.write_object_value(None, response_obj)
                content = writer.get_serialized_content()
                if content:
                    raw = content.decode("utf-8") if isinstance(content, bytes) else content
                    parsed = json.loads(raw)
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
                        for key, value in additional.items():
                            if key not in result:
                                try:
                                    result[key] = Outlook._serialize_response(value)
                                except Exception:
                                    result[key] = str(value)
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
        for key, value in obj_dict.items():
            if key.startswith("_"):
                continue
            try:
                result[key] = Outlook._serialize_response(value)
            except Exception:
                result[key] = str(value)

        additional = getattr(response_obj, "additional_data", None)
        if isinstance(additional, dict):
            for key, value in additional.items():
                if key not in result:
                    try:
                        result[key] = Outlook._serialize_response(value)
                    except Exception:
                        result[key] = str(value)

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
            event_timezone = timezone or "UTC"
            event_body: Dict[str, Any] = {
                "subject": subject,
                "start": {
                    "dateTime": start_datetime,
                    "timeZone": event_timezone,
                },
                "end": {
                    "dateTime": end_datetime,
                    "timeZone": event_timezone,
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
            response = await self.client.me_calendar_create_events(request_body=event_body)
            data = _response_data(response)
            if response.success:
                return True, json.dumps(data)
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
                for attendee_item in attendees:
                    if isinstance(attendee_item, str) and attendee_item.strip():
                        _normalized.append(attendee_item.strip())
                    elif isinstance(attendee_item, dict):
                        addr = attendee_item.get("emailAddress")
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

            event_timezone = timezone or "UTC"
            if start_datetime is not None:
                event_body["start"] = {
                    "dateTime": start_datetime,
                    "timeZone": event_timezone,
                }
            if end_datetime is not None:
                event_body["end"] = {
                    "dateTime": end_datetime,
                    "timeZone": event_timezone,
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
                data = _response_data(response)
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
        tool_name="get_recurring_events",
        description=(
            "Get all recurring calendar events that have occurrences in a date range "
            "(defaults to the next 30 days). Returns each recurring series with its "
            "upcoming occurrences grouped together. Unlike get_recurring_events_ending, "
            "this returns ALL recurring events active in the window, not just those "
            "whose series is ending."
        ),
        args_schema=GetRecurringEventsInput,
        when_to_use=[
            "User wants to see all their recurring events or meetings",
            "User asks what recurring meetings they have coming up",
            "User wants to list recurring calendar events in a date range",
            "User wants to know which meetings repeat on their calendar",
        ],
        when_not_to_use=[
            "User wants all events including non-recurring (use get_calendar_events)",
            "User only wants recurring events that are ending soon (use get_recurring_events_ending)",
            "User wants to create or update an event",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Show me all my recurring meetings",
            "What recurring events do I have in the next month?",
            "List all repeating meetings on my calendar",
            "Which meetings recur this week?",
        ],
        category=ToolCategory.CALENDAR,
    )
    async def get_recurring_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        timezone: str = "UTC",
        top: int = 10,
    ) -> tuple[bool, str]:
        """Get all recurring events with occurrences in a date range (default: next 30 days)."""
        try:
            top = min(top or 10, 50)
            now = datetime.now(dt_timezone.utc)
            range_start = start_date or now.strftime("%Y-%m-%dT%H:%M:%SZ")
            range_end = end_date or (now + timedelta(days=30)).strftime("%Y-%m-%dT23:59:59Z")

            series_map: dict = OrderedDict()
            page_size = 50
            skip = 0
            max_pages = 5

            for _ in range(max_pages):
                resp = await self.client.me_calendar_list_calendar_view(
                    startDateTime=range_start,
                    endDateTime=range_end,
                    top=page_size,
                    skip=skip,
                )
                if not resp.success:
                    return False, json.dumps({
                        "error": resp.error or "Failed to fetch calendar view"
                    })

                data = _response_data(resp)
                events = (
                    data.get("value", []) if isinstance(data, dict)
                    else (data if isinstance(data, list) else [])
                )

                for event in events:
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type", "")
                    series_id = event.get("seriesMasterId")

                    if event_type == "seriesMaster":
                        series_id = event.get("id")
                    elif event_type != "occurrence":
                        continue

                    if not series_id:
                        continue

                    if series_id not in series_map:
                        series_map[series_id] = {
                            "seriesMasterId": series_id,
                            "subject": event.get("subject"),
                            "organizer": event.get("organizer"),
                            "recurrence": event.get("recurrence"),
                            "isOnlineMeeting": event.get("isOnlineMeeting"),
                            "onlineMeeting": event.get("onlineMeeting"),
                            "location": event.get("location"),
                            "occurrences": [],
                        }

                    series_map[series_id]["occurrences"].append({
                        "id": event.get("id"),
                        "start": event.get("start"),
                        "end": event.get("end"),
                        "subject": event.get("subject"),
                        "isCancelled": event.get("isCancelled", False),
                    })

                if len(events) < page_size or len(series_map) >= top:
                    break
                skip += page_size

            results = list(series_map.values())[:top]

            return True, json.dumps({
                "results": results,
                "count": len(results),
                "total_occurrences": sum(len(series["occurrences"]) for series in results),
                "range": {
                    "start": range_start,
                    "end": range_end,
                    "timezone": timezone,
                },
            })

        except Exception as e:
            return self._handle_error(e, "get recurring events")

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
            top = min(top or 10, 50)
            now = datetime.now(dt_timezone.utc)
            range_start = end_after or now.strftime("%Y-%m-%dT%H:%M:%SZ")
            range_end = end_before

            try:
                dt_range_start = datetime.fromisoformat(range_start.replace("Z", "+00:00")).date()
                dt_range_end = datetime.fromisoformat(range_end.replace("Z", "+00:00")).date()
            except ValueError as e:
                return False, json.dumps({"error": f"Invalid datetime format: {e}"})

            if dt_range_start > dt_range_end:
                return False, json.dumps({"error": "end_after must be before end_before."})

            def get_end_date(event: dict) -> Optional[date]:
                rec_range = (event.get("recurrence") or {}).get("range", {})
                if rec_range.get("type") not in ("endDate",):
                    return None
                try:
                    return date.fromisoformat(rec_range["endDate"])
                except (KeyError, ValueError):
                    return None

            matched: list = []
            page_size = 50
            skip = 0
            max_pages = 5

            for _ in range(max_pages):
                resp = await self.client.me_list_series_master_events(
                    top=page_size, skip=skip, timezone=timezone,
                )
                if not resp.success:
                    return False, json.dumps({"error": resp.error or "Failed to fetch recurring events"})

                data = _response_data(resp)
                events = data.get("value", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

                if not events:
                    break

                for event in events:
                    if not isinstance(event, dict):
                        continue
                    end_date = get_end_date(event)
                    if end_date is not None and dt_range_start <= end_date <= dt_range_end:
                        matched.append({**event, "_recurrenceEndDate": end_date.isoformat()})

                if len(events) < page_size or len(matched) >= top:
                    break
                skip += page_size

            if not matched:
                return True, json.dumps({
                    "results": [], "count": 0,
                    "message": "No recurring events found.",
                    "range": {"end_after": range_start, "end_before": range_end},
                })

            matched.sort(key=lambda event: event["_recurrenceEndDate"])
            results = matched[:top]

            return True, json.dumps({
                "results": results,
                "count": len(results),
                "range": {"end_after": range_start, "end_before": range_end},
            })

        except Exception as e:
            return self._handle_error(e, "get recurring events ending")

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
        """Delete specific occurrences of a recurring event by date.
        Strategy: fetch ALL occurrences from series start to end, find the last
        occurrence NOT in the delete list, update end date ONLY if the series end
        date falls within the deletion range, then delete remaining occurrences.
        """
        try:
            # ── 1. Normalize, deduplicate, sort ──────────────────────────────
            target_dates = sorted({date_str.strip() for date_str in occurrence_dates})
            if not target_dates:
                return False, json.dumps({"error": "No occurrence dates provided."})

            logger.info(
                "Deleting recurring event occurrences: event_id=%s, requested_dates=%d",
                event_id, len(target_dates),
            )

            parsed_targets: List[date] = []
            for date_string in target_dates:
                try:
                    parsed_targets.append(date.fromisoformat(date_string))
                except ValueError:
                    return False, json.dumps({"error": f"Invalid date format: {date_string}"})

            # ── 2. Fetch series master for recurrence range ──────────────────
            master_resp = await self.client.me_calendar_get_events(event_id=event_id)
            if not master_resp or not master_resp.success:
                return False, json.dumps({
                    "error": (master_resp.error if master_resp else None)
                    or "Failed to fetch series master",
                })

            master_data = _response_data(master_resp)
            master_event = master_data if isinstance(master_data, dict) else {}
            recurrence = master_event.get("recurrence") or {}
            rec_range = recurrence.get("range") or {}

            series_start: Optional[date] = None
            series_end: Optional[date] = None
            if rec_range.get("startDate"):
                with contextlib.suppress(ValueError):
                    series_start = date.fromisoformat(rec_range["startDate"])
            if rec_range.get("type") == "endDate" and rec_range.get("endDate"):
                with contextlib.suppress(ValueError):
                    series_end = date.fromisoformat(rec_range["endDate"])

            # ── 3. Drop dates outside the series scope ───────────────────────
            valid_dates: List[str] = []
            out_of_scope: List[str] = []
            for d_str, d_parsed in zip(target_dates, parsed_targets):
                if (series_start and d_parsed < series_start) or (
                    series_end and d_parsed > series_end
                ):
                    out_of_scope.append(d_str)
                else:
                    valid_dates.append(d_str)

            if out_of_scope:
                logger.info(
                    "Out-of-scope dates skipped: %s (series range: %s to %s)",
                    out_of_scope, series_start, series_end,
                )

            if not valid_dates:
                return True, json.dumps({
                    "success": True,
                    "series_master_id": event_id,
                    "deleted": [],
                    "out_of_scope": out_of_scope,
                    "errors": [],
                    "summary": {
                        "requested": len(target_dates),
                        "deleted": 0,
                        "trimmed_via_end_date": 0,
                        "out_of_scope": len(out_of_scope),
                        "failed": 0,
                    },
                })

            delete_set = set(valid_dates)

            # ── 4. Fetch ALL occurrences from series start to series end ─────
            # We must fetch the full series, not just the target window, so we
            # can correctly identify the true last kept occurrence across the
            # entire series — not just within the deletion window.
            fetch_start = (
                (series_start - timedelta(days=1)).isoformat()
                if series_start
                else (date.fromisoformat(valid_dates[0]) - timedelta(days=1)).isoformat()
            )
            fetch_end = (
                (series_end + timedelta(days=1)).isoformat()
                if series_end
                else (date.fromisoformat(valid_dates[-1]) + timedelta(days=1)).isoformat()
            )

            occurrences_resp = await self.client.me_list_event_occurrences(
                event_id=event_id,
                start_date=fetch_start,
                end_date=fetch_end,
                timezone=timezone,
            )
            if not occurrences_resp.success:
                return False, json.dumps({
                    "error": occurrences_resp.error or "Failed to fetch occurrences",
                })

            occ_data = _response_data(occurrences_resp)
            occurrences = (
                occ_data.get("value", []) if isinstance(occ_data, dict)
                else (occ_data if isinstance(occ_data, list) else [])
            )

            date_to_occ: Dict[str, Any] = {}
            for occurrence in occurrences:
                if isinstance(occurrence, dict) and occurrence.get("start", {}).get("dateTime"):
                    date_to_occ[occurrence["start"]["dateTime"][:10]] = occurrence
            all_occ_dates = sorted(date_to_occ.keys())

            # ── 5. Update end date ONLY if series end falls in deletion range ─
            # Example: series ends 2024-05-19, delete list has 2024-05-18 and
            # 2024-05-19 → end date must move. But if the series ends 2024-06-01
            # and we're only deleting 2024-05-18 and 2024-05-19, no end date
            # update is needed — just delete those occurrences individually.
            last_kept: Optional[str] = None
            for occ_date in reversed(all_occ_dates):
                if occ_date not in delete_set:
                    last_kept = occ_date
                    break

            end_date_result: Optional[Dict] = None
            trimmed_dates: List[str] = []

            series_end_in_delete_range = (
                series_end is not None
                and series_end.isoformat() in delete_set
            )

            if series_end_in_delete_range and recurrence and last_kept:
                logger.info(
                    "Applying end-date optimization: moving series end from %s to %s",
                    series_end, last_kept,
                )
                trimmed_dates = [
                    occ_date
                    for occ_date in all_occ_dates
                    if occ_date > last_kept and occ_date in delete_set
                ]
                updated_recurrence = {
                    **recurrence,
                    "range": {**rec_range, "endDate": last_kept},
                }
                update_resp = await self.client.me_calendar_update_events(
                    event_id=event_id,
                    request_body={"recurrence": updated_recurrence},
                )
                end_date_result = {
                    "previous_end_date": series_end.isoformat(),
                    "new_end_date": last_kept,
                    "occurrences_trimmed": trimmed_dates,
                    "success": update_resp.success,
                    "error": None if update_resp.success else update_resp.error,
                }
                # Remove trimmed dates from delete_set — no need to delete individually
                delete_set -= set(trimmed_dates)

            # ── 6. Delete remaining occurrences individually ─────────────────
            deleted: list = []
            delete_errors: list = []
            not_found: list = []

            for target_date in sorted(delete_set):
                occurrence = date_to_occ.get(target_date)
                if not occurrence:
                    not_found.append(target_date)
                    continue
                occurrence_id = occurrence.get("id")
                delete_resp = await self.client.me_calendar_delete_events(event_id=occurrence_id)
                if delete_resp.success:
                    deleted.append({
                        "date": target_date,
                        "event_id": occurrence_id,
                        "subject": occurrence.get("subject", ""),
                    })
                else:
                    delete_errors.append({
                        "date": target_date,
                        "event_id": occurrence_id,
                        "error": delete_resp.error,
                    })

            logger.info(
                "Delete recurring occurrences complete: event_id=%s, "
                "deleted=%d, trimmed_via_end_date=%d, out_of_scope=%d, "
                "not_found=%d, failed=%d",
                event_id, len(deleted), len(trimmed_dates),
                len(out_of_scope), len(not_found), len(delete_errors),
            )

            return True, json.dumps({
                "success": True,
                "series_master_id": event_id,
                "deleted": deleted,
                "not_found": not_found,
                "out_of_scope": out_of_scope,
                "errors": delete_errors,
                **({"end_date_update": end_date_result} if end_date_result else {}),
                "summary": {
                    "requested": len(target_dates),
                    "deleted": len(deleted),
                    "trimmed_via_end_date": len(trimmed_dates),
                    "out_of_scope": len(out_of_scope),
                    "not_found": len(not_found),
                    "failed": len(delete_errors),
                },
            })

        except Exception as e:
            return self._handle_error(e, "delete recurring event occurrence")


    # ------------------------------------------------------------------
    # Transcripts tools
    # ------------------------------------------------------------------

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
                return await self._online_meeting_id_from_join_url(join_url)

            # Path B: event_id provided — fetch event to extract joinUrl
            if event_id:
                ev_resp = await self.client.me_calendar_get_events(event_id=event_id)
                if not ev_resp.success:
                    return None

                event_data = _response_data(ev_resp)
                if not isinstance(event_data, dict):
                    return None

                online_meeting_data = event_data.get("onlineMeeting")
                if not isinstance(online_meeting_data, dict):
                    return None

                extracted_join_url = online_meeting_data.get("joinUrl")
                if not extracted_join_url or not isinstance(extracted_join_url, str):
                    return None

                return await self._online_meeting_id_from_join_url(extracted_join_url)
            return None

        except Exception:
            return None


    async def _online_meeting_id_from_join_url(self, join_url: str) -> Optional[str]:
        """Resolve a Teams joinWebUrl to an online meeting ID.

        GET /me/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
        NOTE: join_url must be URL-decoded before filtering — Graph API
        returns 400 if the URL contains percent-encoded characters.
        """
        try:

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
        for raw_line in meta_text.strip().splitlines():
            line = raw_line.strip()
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
