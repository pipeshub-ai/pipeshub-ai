import json
from typing import List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource

from app.agents.actions.google.auth.auth import gmail_auth
from app.agents.actions.google.gmail.config import GoogleGmailConfig
from app.agents.actions.google.gmail.utils import GmailUtils


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
            return False, json.dumps(str(e))

    @gmail_auth()
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
            message = self.service.users().drafts().create( # type: ignore
                userId="me",
                body=message_body,
            ).execute() # type: ignore

            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message" : message,
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @gmail_auth()
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
        Returns:
            tuple[bool, str]: True if the email is sent, False otherwise
        """
        try:
            if not GmailUtils.validate_email_list(mail_to + (mail_cc or []) + (mail_bcc or [])):
                return False, json.dumps({"error": "Invalid email addresses"})

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

            message = self.service.users().messages().send(userId="me", body=message_body).execute() # type: ignore
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message" : message,
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @gmail_auth()
    def search_emails(
        self,
        query: str,
        max_results: Optional[int] = 10,
        page_token: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Search for emails"""
        """
        Args:
            query: The query to search for
            max_results: The maximum number of results to return
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

            return True, json.dumps({
                "messages": messages.get("messages", []),
                "nextPageToken": messages.get("nextPageToken", None),
                "totalResults": messages.get("resultSizeEstimate", 0),
                "pageToken": messages.get("nextPageToken", None),
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @gmail_auth()
    def get_email_details(
        self,
        message_id: str,
    ) -> tuple[bool, str]:
        """Get the details of an email"""
        """
        Args:
            message_id: The id of the email
        Returns:
            tuple[bool, str]: True if the email is retrieved, False otherwise
        """
        try:
            message = self.service.users().messages().get( # type: ignore
                userId="me",
                id=message_id,
                format="full",
            ).execute() # type: ignore

            return True, json.dumps({
                "message": message,
                "message_id": message.get("id", ""),
                "message_body": message.get("payload", {}).get("body", {}).get("data", ""),
                "message_headers": message.get("payload", {}).get("headers", []),
                "message_attachments": message.get("payload", {}).get("parts", []),
            })
        except Exception as e:
            return False, json.dumps(str(e))

    @gmail_auth()
    def get_email_attachments(
        self,
        message_id: str,
    ) -> tuple[bool, str]:
        """Get the attachments of an email"""
        """
        Args:
            message_id: The id of the email
        Returns:
            tuple[bool, str]: True if the attachments are retrieved, False otherwise
        """
        try:
            message = self.service.users().messages().get( # type: ignore
                userId="me",
                id=message_id,
                format="metadata",
            ).execute() # type: ignore

            return True, json.dumps({
                "message": message,
                "message_id": message.get("id", ""),
                "message_attachments": message.get("payload", {}).get("parts", []),
            })
        except Exception as e:
            return False, json.dumps(str(e))

