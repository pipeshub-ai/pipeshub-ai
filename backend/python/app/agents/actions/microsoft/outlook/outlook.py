import json
import logging
from typing import Any, Dict, List, Optional

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


class CreateCalendarEventInput(BaseModel):
    """Schema for creating a calendar event"""
    subject: str = Field(description="Title/subject of the event")
    start_datetime: str = Field(description="Start datetime in ISO 8601 format (e.g. 2024-01-15T10:00:00Z)")
    end_datetime: str = Field(description="End datetime in ISO 8601 format (e.g. 2024-01-15T11:00:00Z)")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for the event (e.g. 'UTC', 'America/New_York')")
    body: Optional[str] = Field(default=None, description="Body/description of the event")
    location: Optional[str] = Field(default=None, description="Location of the event")
    attendees: Optional[List[str]] = Field(default=None, description="List of attendee email addresses")
    is_online_meeting: Optional[bool] = Field(default=False, description="Whether to create an online meeting link")


class GetCalendarEventInput(BaseModel):
    """Schema for getting a specific calendar event"""
    event_id: str = Field(description="ID of the calendar event to retrieve")


class UpdateCalendarEventInput(BaseModel):
    """Schema for updating a calendar event"""
    event_id: str = Field(description="ID of the calendar event to update")
    subject: Optional[str] = Field(default=None, description="New title/subject of the event")
    start_datetime: Optional[str] = Field(default=None, description="New start datetime in ISO 8601 format")
    end_datetime: Optional[str] = Field(default=None, description="New end datetime in ISO 8601 format")
    timezone: Optional[str] = Field(default=None, description="Timezone for the event (e.g. 'UTC', 'America/New_York')")
    body: Optional[str] = Field(default=None, description="New body/description of the event")
    location: Optional[str] = Field(default=None, description="New location of the event")
    attendees: Optional[List[str]] = Field(default=None, description="Updated list of attendee email addresses (replaces existing)")
    is_online_meeting: Optional[bool] = Field(default=None, description="Whether to create an online meeting link")


class DeleteCalendarEventInput(BaseModel):
    """Schema for deleting a calendar event"""
    event_id: str = Field(description="ID of the calendar event to delete")


class GetMailFoldersInput(BaseModel):
    """Schema for getting mail folders"""
    top: Optional[int] = Field(default=20, description="Maximum number of folders to return")


# ---------------------------------------------------------------------------
# Toolset registration
# ---------------------------------------------------------------------------

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
                    "offline_access",
                    "User.Read",
                ]
            ),
            additional_params={
                "prompt": "consent",
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

        if (
            isinstance(error, ValueError)
            or "not authenticated" in error_msg
            or "oauth" in error_msg
            or "authentication" in error_msg
            or "unauthorized" in error_msg
        ):
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
            if not create_response.success:
                return False, json.dumps({
                    "error": create_response.error or "Failed to create email draft"
                })

            data = create_response.data
            message_id: Optional[str] = None
            if isinstance(data, dict):
                message_id = data.get("id")
            elif hasattr(data, "id"):
                message_id = data.id

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
                data = response.data
                messages: List[Any] = []
                if isinstance(data, dict):
                    raw = data.get("value", [])
                    messages = [self._serialize_response(m) for m in raw]
                elif isinstance(data, list):
                    messages = [self._serialize_response(m) for m in data]
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
                return True, json.dumps(self._serialize_response(response.data))
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
                data = response.data
                folders: List[Any] = []
                if isinstance(data, dict):
                    raw = data.get("value", [])
                    folders = [self._serialize_response(f) for f in raw]
                elif isinstance(data, list):
                    folders = [self._serialize_response(f) for f in data]
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
                data = response.data
                events: List[Any] = []
                if isinstance(data, dict):
                    raw = data.get("value", [])
                    events = [self._serialize_response(ev) for ev in raw]
                elif isinstance(data, list):
                    events = [self._serialize_response(ev) for ev in data]
                return True, json.dumps({
                    "results": events,
                    "count": len(events),
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                })
            else:
                return False, json.dumps({"error": response.error or "Failed to get calendar events"})
        except Exception as e:
            return self._handle_error(e, "get calendar events")

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

            response = await self.client.me_calendar_create_events(request_body=event_body)
            if response.success:
                data = self._serialize_response(response.data)
                # Extract event ID from response for follow-up operations
                event_id = None
                if isinstance(data, dict):
                    event_id = data.get("id")
                elif hasattr(response.data, "id"):
                    event_id = response.data.id
                return True, json.dumps({
                    "message": "Calendar event created successfully",
                    "event_id": event_id,
                    "event": data,
                })
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
                return True, json.dumps(self._serialize_response(response.data))
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
    ) -> tuple[bool, str]:
        """Update an existing calendar event in Outlook."""
        try:
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

            if not event_body:
                return False, json.dumps({"error": "No fields provided to update"})

            response = await self.client.me_calendar_update_events(
                event_id=event_id,
                request_body=event_body,
            )
            if response.success:
                data = self._serialize_response(response.data)
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