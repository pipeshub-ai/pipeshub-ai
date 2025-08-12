import json
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from app.agents.actions.google.auth.auth import gmail_auth
from app.agents.actions.google.gmail.config import GoogleGmailConfig
from app.agents.actions.google.gmail.utils import GmailUtils
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter


class Gmail:
    """Gmail tool exposed to the agents"""
    def __init__(self, config: GoogleGmailConfig) -> None:
        """Initialize the Gmail tool"""
        """
        Args:
            config: Gmail configuration
        Returns:
            None
        """
        self.config = config
        self.service: Optional[Resource] = None
        self.credentials: Optional[Credentials] = None

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="reply",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="The ID of the email to reply to",
                required=True
            ),
            ToolParameter(
                name="mail_to",
                type=ParameterType.ARRAY,
                description="List of email addresses to send the reply to",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_subject",
                type=ParameterType.STRING,
                description="The subject of the reply email",
                required=True
            ),
            ToolParameter(
                name="mail_cc",
                type=ParameterType.ARRAY,
                description="List of email addresses to CC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_bcc",
                type=ParameterType.ARRAY,
                description="List of email addresses to BCC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_body",
                type=ParameterType.STRING,
                description="The body content of the reply email",
                required=False
            ),
            ToolParameter(
                name="mail_attachments",
                type=ParameterType.ARRAY,
                description="List of file paths to attach",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="thread_id",
                type=ParameterType.STRING,
                description="The thread ID to maintain conversation context",
                required=False
            )
        ]
    )
    def reply(
        self,
        message_id: str,
        mail_to: List[str],
        mail_subject: str,
        mail_cc: Optional[List[str]] = None,
        mail_bcc: Optional[List[str]] = None,
        mail_body: Optional[str] = None,
        mail_attachments: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Reply to an email"""
        """
        Args:
            message_id: The id of the email
            mail_to: List of email addresses to send the email to
            mail_subject: The subject of the email
            mail_cc: List of email addresses to send the email to
            mail_bcc: List of email addresses to send the email to
            mail_body: The body of the email
            mail_attachments: List of attachments to send with the email (file paths)
            thread_id: The thread id of the email
        Returns:
            tuple[bool, str]: True if the email is replied, False otherwise
        """
        try:
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                mail_attachments,
                thread_id,
                message_id,
            )

            message = self.service.users().messages().send( # type: ignore
                userId="me",
                body=message_body,
            ).execute() # type: ignore
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message" : message,
            })
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="draft_email",
        parameters=[
            ToolParameter(
                name="mail_to",
                type=ParameterType.ARRAY,
                description="List of email addresses to send the email to",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_subject",
                type=ParameterType.STRING,
                description="The subject of the email",
                required=True
            ),
            ToolParameter(
                name="mail_cc",
                type=ParameterType.ARRAY,
                description="List of email addresses to CC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_bcc",
                type=ParameterType.ARRAY,
                description="List of email addresses to BCC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_body",
                type=ParameterType.STRING,
                description="The body content of the email",
                required=False
            ),
            ToolParameter(
                name="mail_attachments",
                type=ParameterType.ARRAY,
                description="List of file paths to attach",
                required=False,
                items={"type": "string"}
            )
        ]
    )
    def draft_email(
        self,
        mail_to: List[str],
        mail_subject: str,
        mail_cc: Optional[List[str]] = None,
        mail_bcc: Optional[List[str]] = None,
        mail_body: Optional[str] = None,
        mail_attachments: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Draft an email"""
        """
        Args:
            mail_to: List of email addresses to send the email to
            mail_cc: List of email addresses to send the email to
            mail_bcc: List of email addresses to send the email to
            mail_subject: The subject of the email
            mail_body: The body of the email
            mail_attachments: List of attachments to send with the email (file paths)
        Returns:
            tuple[bool, str]: True if the email is drafted, False otherwise
        """
        try:
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                mail_attachments,
            )

            draft = self.service.users().drafts().create( # type: ignore
                userId="me",
                body={"message": message_body},
            ).execute() # type: ignore
            return True, json.dumps({
                "draft_id": draft.get("id", ""),
                "draft": draft,
            })
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="send_email",
        parameters=[
            ToolParameter(
                name="mail_to",
                type=ParameterType.ARRAY,
                description="List of email addresses to send the email to",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_subject",
                type=ParameterType.STRING,
                description="The subject of the email",
                required=True
            ),
            ToolParameter(
                name="mail_cc",
                type=ParameterType.ARRAY,
                description="List of email addresses to CC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_bcc",
                type=ParameterType.ARRAY,
                description="List of email addresses to BCC",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="mail_body",
                type=ParameterType.STRING,
                description="The body content of the email",
                required=False
            ),
            ToolParameter(
                name="mail_attachments",
                type=ParameterType.ARRAY,
                description="List of file paths to attach",
                required=False,
                items={"type": "string"}
            ),
            ToolParameter(
                name="thread_id",
                type=ParameterType.STRING,
                description="The thread ID to maintain conversation context",
                required=False
            ),
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="The message ID for threading",
                required=False
            )
        ]
    )
    def send_email(
        self,
        mail_to: List[str],
        mail_subject: str,
        mail_cc: Optional[List[str]] = None,
        mail_bcc: Optional[List[str]] = None,
        mail_body: Optional[str] = None,
        mail_attachments: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Send an email"""
        """
        Args:
            mail_to: List of email addresses to send the email to
            mail_cc: List of email addresses to send the email to
            mail_bcc: List of email addresses to send the email to
            mail_subject: The subject of the email
            mail_body: The body of the email
            mail_attachments: List of attachments to send with the email (file paths)
            thread_id: The thread id of the email
            message_id: The message id of the email
        Returns:
            tuple[bool, str]: True if the email is sent, False otherwise
        """
        try:
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                mail_attachments,
                thread_id,
                message_id,
            )

            message = self.service.users().messages().send( # type: ignore
                userId="me",
                body=message_body,
            ).execute() # type: ignore
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message": message,
            })
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="search_emails",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query to find emails (Gmail search syntax)",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of emails to return",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Token for pagination to get next page of results",
                required=False
            )
        ]
    )
    def search_emails(
        self,
        query: str,
        max_results: Optional[int] = 10,
        page_token: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Search for emails in Gmail"""
        """
        Args:
            query: The search query to find emails
            max_results: Maximum number of emails to return
            page_token: Token for pagination to get next page of results
        Returns:
            tuple[bool, str]: True if the emails are searched, False otherwise
        """
        try:
            messages = self.service.users().messages().list( # type: ignore
                userId="me",
                q=query,
                maxResults=max_results,
                pageToken=page_token,
            ).execute() # type: ignore
            return True, json.dumps(messages)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="get_email_details",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="The ID of the email to get details for",
                required=True
            )
        ]
    )
    def get_email_details(
        self,
        message_id: str,
    ) -> tuple[bool, str]:
        """Get detailed information about a specific email"""
        """
        Args:
            message_id: The ID of the email
        Returns:
            tuple[bool, str]: True if the email details are retrieved, False otherwise
        """
        try:
            message = self.service.users().messages().get( # type: ignore
                userId="me",
                id=message_id,
                format="full",
            ).execute() # type: ignore
            return True, json.dumps(message)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

    @gmail_auth()
    @tool(
        app_name="gmail",
        tool_name="get_email_attachments",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="The ID of the email to get attachments for",
                required=True
            )
        ]
    )
    def get_email_attachments(
        self,
        message_id: str,
    ) -> tuple[bool, str]:
        """Get attachments from a specific email"""
        """
        Args:
            message_id: The ID of the email
        Returns:
            tuple[bool, str]: True if the email attachments are retrieved, False otherwise
        """
        try:
            message = self.service.users().messages().get( # type: ignore
                userId="me",
                id=message_id,
                format="full",
            ).execute() # type: ignore

            attachments = []
            if "payload" in message and "parts" in message["payload"]:
                for part in message["payload"]["parts"]:
                    if part.get("filename"):
                        attachments.append({
                            "attachment_id": part["body"]["attachmentId"],
                            "filename": part["filename"],
                            "mime_type": part["mimeType"],
                            "size": part["body"]["size"]
                        })

            return True, json.dumps(attachments)
        except Exception as e:
            return False, json.dumps({"error": str(e)})

