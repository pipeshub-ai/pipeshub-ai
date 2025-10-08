import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.microsoft.microsoft import MSGraphClient
from app.sources.external.microsoft.outlook.outlook import (
    OutlookCalendarContactsDataSource,
)

logger = logging.getLogger(__name__)


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

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

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

            create_resp = self._run_async(self.client.me_create_messages(request_body=message_body))
            if not getattr(create_resp, 'success', False):
                return False, create_resp.to_json()

            created = getattr(create_resp, 'data', {})
            message_id = created.get('id') if isinstance(created, dict) else None
            if not message_id:
                return False, json.dumps({"error": "Failed to create message draft"})

            response = self._run_async(self.client.me_messages_message_send(message_id=message_id))

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
                response = self._run_async(self.client.me_mail_folders_get_messages(
                    mailFolder_id=folder_id,
                    message_id="",
                    top=top,
                    filter=filter
                ))
            else:
                # Fallback: use delta to fetch recent messages when root listing is not direct
                response = self._run_async(self.client.me_messages_delta(top=top, search=None, filter=filter))

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
            response = self._run_async(self.client.users_get_messages(
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
            response = self._run_async(self.client.me_messages_message_reply(
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
                response = self._run_async(self.client.me_mail_folders_child_folders_get_messages(
                    mailFolder_id=folder_id,
                    message_id=""
                ))
            else:
                # Use root default folders listing via messages delta as a pragmatic fallback
                response = self._run_async(self.client.me_messages_delta())

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
            response = self._run_async(self.client.me_contacts_list(
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
            response = self._run_async(self.client.me_calendar_view_list(
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
