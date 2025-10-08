import asyncio
import json
import logging
from typing import List, Optional

from app.agents.actions.google.gmail.utils import GmailUtils
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

logger = logging.getLogger(__name__)

class Gmail:
    """Gmail tool exposed to the agents using GoogleGmailDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Gmail tool"""
        """
        Args:
            client: Authenticated Gmail client
        Returns:
            None
        """
        self.client = GoogleGmailDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

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

            # Use GoogleGmailDataSource method
            message = self._run_async(self.client.users_messages_send(
                userId="me",
                body=message_body
            ))
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message": message,
            })
        except Exception as e:
            logger.error(f"Failed to send reply: {e}")
            return False, json.dumps({"error": str(e)})

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

            # Use GoogleGmailDataSource method
            draft = self._run_async(self.client.users_drafts_create(
                userId="me",
                body={"message": message_body}
            ))
            return True, json.dumps({
                "draft_id": draft.get("id", ""),
                "draft": draft,
            })
        except Exception as e:
            logger.error(f"Failed to create draft: {e}")
            return False, json.dumps({"error": str(e)})

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

            # Use GoogleGmailDataSource method
            message = self._run_async(self.client.users_messages_send(
                userId="me",
                body=message_body
            ))
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message": message,
            })
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False, json.dumps({"error": str(e)})

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
            # Use GoogleGmailDataSource method
            messages = self._run_async(self.client.users_messages_list(
                userId="me",
                q=query,
                maxResults=max_results,
                pageToken=page_token,
            ))
            return True, json.dumps(messages)
        except Exception as e:
            logger.error(f"Failed to search emails: {e}")
            return False, json.dumps({"error": str(e)})

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
            # Use GoogleGmailDataSource method
            message = self._run_async(self.client.users_messages_get(
                userId="me",
                id=message_id,
                format="full",
            ))
            return True, json.dumps(message)
        except Exception as e:
            logger.error(f"Failed to get email details for {message_id}: {e}")
            return False, json.dumps({"error": str(e)})

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
            # Use GoogleGmailDataSource method to get message details
            message = self._run_async(self.client.users_messages_get(
                userId="me",
                id=message_id,
                format="full",
            ))

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
            logger.error(f"Failed to get email attachments for {message_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gmail",
        tool_name="download_email_attachment",
        parameters=[
            ToolParameter(
                name="message_id",
                type=ParameterType.STRING,
                description="The ID of the email to download the attachment for",
                required=True
            ),
            ToolParameter(
                name="attachment_id",
                type=ParameterType.STRING,
                description="The ID of the attachment to download",
                required=True
            )
        ]
    )
    def download_email_attachment(
        self,
        message_id: str,
        attachment_id: str,
    ) -> tuple[bool, str]:
        """Download an email attachment
        Args:
            message_id: The ID of the email
            attachment_id: The ID of the attachment
        Returns:
            tuple[bool, str]: True if the attachment is downloaded, False otherwise
        """
        try:
            # Use GoogleGmailDataSource method
            attachment = self._run_async(self.client.users_messages_attachments_get(
                userId="me",
                messageId=message_id,
                id=attachment_id,
            ))
            return True, json.dumps(attachment)
        except Exception as e:
            logger.error(f"Failed to download attachment {attachment_id} from message {message_id}: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="gmail",
        tool_name="get_user_profile",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="The user ID (use 'me' for authenticated user)",
                required=False
            )
        ]
    )
    def get_user_profile(
        self,
        user_id: Optional[str] = "me",
    ) -> tuple[bool, str]:
        """Get the current user's Gmail profile"""
        """
        Args:
            user_id: The user ID (defaults to 'me' for authenticated user)
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleGmailDataSource method
            profile = self._run_async(self.client.users_get_profile(
                userId=user_id
            ))
            return True, json.dumps({
                "email_address": profile.get("emailAddress", ""),
                "messages_total": profile.get("messagesTotal", 0),
                "threads_total": profile.get("threadsTotal", 0),
                "history_id": profile.get("historyId", "")
            })
        except Exception as e:
            logger.error(f"Failed to get user profile: {e}")
            return False, json.dumps({"error": str(e)})
