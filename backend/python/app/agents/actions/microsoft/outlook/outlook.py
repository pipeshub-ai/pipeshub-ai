import json
import logging
from typing import List, Optional, Tuple

from app.agents.actions.utils import run_async
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolCategory,
    ToolDefinition,
    ToolsetBuilder,
)
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
)

logger = logging.getLogger(__name__)

# Define tools
tools: List[ToolDefinition] = [
    ToolDefinition(
        name="send_mail",
        description="Send an email",
        parameters=[
            {"name": "to", "type": "string", "description": "Recipient email", "required": True},
            {"name": "subject", "type": "string", "description": "Email subject", "required": True},
            {"name": "body", "type": "string", "description": "Email body", "required": True}
        ],
        tags=["email", "send"]
    ),
    ToolDefinition(
        name="get_messages",
        description="Get email messages",
        parameters=[
            {"name": "folder_id", "type": "string", "description": "Folder ID", "required": False}
        ],
        tags=["email", "list"]
    ),
    ToolDefinition(
        name="get_message",
        description="Get message details",
        parameters=[
            {"name": "message_id", "type": "string", "description": "Message ID", "required": True}
        ],
        tags=["email", "read"]
    ),
    ToolDefinition(
        name="reply_to_message",
        description="Reply to a message",
        parameters=[
            {"name": "message_id", "type": "string", "description": "Message ID", "required": True},
            {"name": "body", "type": "string", "description": "Reply body", "required": True}
        ],
        tags=["email", "reply"]
    ),
    ToolDefinition(
        name="get_folders",
        description="Get email folders",
        parameters=[],
        tags=["folders", "list"]
    ),
    ToolDefinition(
        name="get_contacts",
        description="Get contacts",
        parameters=[],
        tags=["contacts", "list"]
    ),
    ToolDefinition(
        name="get_calendar_events",
        description="Get calendar events",
        parameters=[],
        tags=["calendar", "list"]
    ),
]


# Register Microsoft Outlook toolset
@ToolsetBuilder("Microsoft Outlook")\
    .in_group("Microsoft 365")\
    .with_description("Microsoft Outlook integration for email, calendar, and contacts")\
    .with_category(ToolCategory.APP)\
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
                    "Calendars.ReadWrite",
                    "Contacts.ReadWrite"
                ]
            ),
            fields=[
                CommonFields.client_id("Azure App Registration"),
                CommonFields.client_secret("Azure App Registration")
            ],
            icon_path="/assets/icons/connectors/outlook.svg",
            app_group="Microsoft 365",
            app_description="Microsoft Outlook OAuth application for agent integration"
        )
    ])\
    .with_tools(tools)\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/outlook.svg"))\
    .build_decorator()
class Outlook:
    """Microsoft Outlook tool exposed to the agents"""
    def __init__(self, client: MSGraphClient) -> None:
        """Initialize the Outlook tool"""
        """
        Args:
            client: Microsoft Graph client object
        Returns:
            None
        """
        self.client = OutlookCalendarContactsDataSource(client)

    @tool(
        app_name="outlook",
        tool_name="send_mail",
        description="Send an email using Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="to_recipients",
                type=ParameterType.STRING,
                description="Comma-separated list of recipient email addresses",
                required=True
            ),
            ToolParameter(
                name="subject",
                type=ParameterType.STRING,
                description="Email subject",
                required=True
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="Email body content",
                required=True
            ),
            ToolParameter(
                name="cc_recipients",
                type=ParameterType.STRING,
                description="Comma-separated list of CC recipient email addresses",
                required=False
            ),
            ToolParameter(
                name="bcc_recipients",
                type=ParameterType.STRING,
                description="Comma-separated list of BCC recipient email addresses",
                required=False
            )
        ]
    )
    def send_mail(
        self,
        to_recipients: str,
        subject: str,
        body: str,
        cc_recipients: Optional[str] = None,
        bcc_recipients: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Send an email using Microsoft Outlook"""
        """
        Args:
            to_recipients: Comma-separated list of recipient email addresses
            subject: Email subject
            body: Email body content
            cc_recipients: Comma-separated list of CC recipient email addresses
            bcc_recipients: Comma-separated list of BCC recipient email addresses
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Create draft message then send: POST /me/messages then POST /me/messages/{id}/send
            message_body = {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body,
                },
                "toRecipients": [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in to_recipients.split(',') if addr.strip()
                ],
            }
            if cc_recipients:
                message_body["ccRecipients"] = [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in cc_recipients.split(',') if addr.strip()
                ]
            if bcc_recipients:
                message_body["bccRecipients"] = [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in bcc_recipients.split(',') if addr.strip()
                ]

            create_resp = run_async(self.client.me_create_messages(request_body=message_body))
            if not getattr(create_resp, 'success', False):
                return False, create_resp.to_json()

            created = getattr(create_resp, 'data', {})
            message_id = created.get('id') if isinstance(created, dict) else None
            if not message_id:
                return False, json.dumps({"error": "Failed to create message draft"})

            response = run_async(self.client.me_messages_message_send(message_id=message_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in send_mail: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="get_messages",
        description="Get messages from Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="folder_id",
                type=ParameterType.STRING,
                description="ID of the folder to get messages from",
                required=False
            ),
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of messages to retrieve",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="OData filter expression",
                required=False
            )
        ]
    )
    def get_messages(
        self,
        folder_id: Optional[str] = None,
        top: Optional[int] = None,
        filter: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get messages from Microsoft Outlook"""
        """
        Args:
            folder_id: ID of the folder to get messages from
            top: Number of messages to retrieve
            filter: OData filter expression
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use appropriate listing depending on folder
            if folder_id:
                response = run_async(self.client.me_mail_folders_get_messages(
                    mailFolder_id=folder_id,
                    message_id="",
                    top=top,
                    filter=filter
                ))
            else:
                # Fallback: use delta to fetch recent messages when root listing is not direct
                response = run_async(self.client.me_messages_delta(top=top, search=None, filter=filter))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_messages: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="get_message",
        description="Get a specific message from Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="ID of the message",
                required=True
            )
        ]
    )
    def get_message(self, message_id: str) -> Tuple[bool, str]:
        """Get a specific message from Microsoft Outlook"""
        """
        Args:
            message_id: ID of the message
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use OutlookDataSource method for single message
            response = run_async(self.client.users_get_messages(
                user_id="me",
                message_id=message_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_message: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="reply_to_message",
        description="Reply to a message in Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="ID of the message to reply to",
                required=True
            ),
            ToolParameter(
                name="comment",
                type=ParameterType.STRING,
                description="Reply comment",
                required=True
            )
        ]
    )
    def reply_to_message(
        self,
        message_id: str,
        comment: str
    ) -> Tuple[bool, str]:
        """Reply to a message in Microsoft Outlook"""
        """
        Args:
            message_id: ID of the message to reply to
            comment: Reply comment
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Create reply draft then send reply: POST /me/messages/{id}/reply
            response = run_async(self.client.me_messages_message_reply(
                message_id=message_id,
                request_body={
                    "comment": comment
                }
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in reply_to_message: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="get_folders",
        description="Get folders from Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="folder_id",
                type=ParameterType.STRING,
                description="ID of the parent folder",
                required=False
            )
        ]
    )
    def get_folders(self, folder_id: Optional[str] = None) -> Tuple[bool, str]:
        """Get folders from Microsoft Outlook"""
        """
        Args:
            folder_id: ID of the parent folder
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # List mail folders: GET /me/mailFolders or /me/mailFolders/{id}/childFolders
            if folder_id:
                response = run_async(self.client.me_mail_folders_child_folders_get_messages(
                    mailFolder_id=folder_id,
                    message_id=""
                ))
            else:
                # Use root default folders listing via messages delta as a pragmatic fallback
                response = run_async(self.client.me_messages_delta())

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_folders: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="get_contacts",
        description="Get contacts from Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="top",
                type=ParameterType.INTEGER,
                description="Number of contacts to retrieve",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="OData filter expression",
                required=False
            )
        ]
    )
    def get_contacts(
        self,
        top: Optional[int] = None,
        filter: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get contacts from Microsoft Outlook"""
        """
        Args:
            top: Number of contacts to retrieve
            filter: OData filter expression
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use contacts listing: GET /me/contacts with OData
            response = run_async(self.client.me_contacts_list(
                top=top,
                filter=filter
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_contacts: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="outlook",
        tool_name="get_calendar_events",
        description="Get calendar events from Microsoft Outlook",
        parameters=[
            ToolParameter(
                name="calendar_id",
                type=ParameterType.STRING,
                description="ID of the calendar",
                required=False
            ),
            ToolParameter(
                name="start_datetime",
                type=ParameterType.STRING,
                description="Start datetime for events (ISO format)",
                required=False
            ),
            ToolParameter(
                name="end_datetime",
                type=ParameterType.STRING,
                description="End datetime for events (ISO format)",
                required=False
            )
        ]
    )
    def get_calendar_events(
        self,
        calendar_id: Optional[str] = None,
        start_datetime: Optional[str] = None,
        end_datetime: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get calendar events from Microsoft Outlook"""
        """
        Args:
            calendar_id: ID of the calendar
            start_datetime: Start datetime for events (ISO format)
            end_datetime: End datetime for events (ISO format)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use calendarView for time window; default to primary calendar
            response = run_async(self.client.me_calendar_view_list(
                startDateTime=start_datetime,
                endDateTime=end_datetime,
                calendar_id=calendar_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error in get_calendar_events: {e}")
            return False, json.dumps({"error": str(e)})
