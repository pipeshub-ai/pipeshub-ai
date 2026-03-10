import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.actions.google.gmail.utils import GmailUtils
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
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

logger = logging.getLogger(__name__)

# Pydantic schemas for Gmail tools
class SendEmailInput(BaseModel):
    """Schema for sending an email"""
    mail_to: List[str] = Field(description="List of email addresses to send the email to")
    mail_subject: str = Field(description="The subject of the email")
    mail_cc: Optional[List[str]] = Field(default=None, description="List of email addresses to CC")
    mail_bcc: Optional[List[str]] = Field(default=None, description="List of email addresses to BCC")
    mail_body: Optional[str] = Field(default=None, description="The body content of the email")
    mail_attachments: Optional[List[str]] = Field(default=None, description="List of file paths to attach")
    thread_id: Optional[str] = Field(default=None, description="The thread ID to maintain conversation context")
    message_id: Optional[str] = Field(default=None, description="The message ID for threading")


class ReplyInput(BaseModel):
    """Schema for replying to an email"""
    message_id: str = Field(description="The ID of the email to reply to")
    mail_to: List[str] = Field(description="List of email addresses to send the reply to")
    mail_subject: str = Field(description="The subject of the reply email")
    mail_cc: Optional[List[str]] = Field(default=None, description="List of email addresses to CC")
    mail_bcc: Optional[List[str]] = Field(default=None, description="List of email addresses to BCC")
    mail_body: Optional[str] = Field(default=None, description="The body content of the reply email")
    mail_attachments: Optional[List[str]] = Field(default=None, description="List of file paths to attach")
    thread_id: Optional[str] = Field(default=None, description="The thread ID to maintain conversation context")


class DraftEmailInput(BaseModel):
    """Schema for creating a draft email"""
    mail_to: List[str] = Field(description="List of email addresses to send the email to")
    mail_subject: str = Field(description="The subject of the email")
    mail_cc: Optional[List[str]] = Field(default=None, description="List of email addresses to CC")
    mail_bcc: Optional[List[str]] = Field(default=None, description="List of email addresses to BCC")
    mail_body: Optional[str] = Field(default=None, description="The body content of the email")
    mail_attachments: Optional[List[str]] = Field(default=None, description="List of file paths to attach")


class SearchEmailsInput(BaseModel):
    """Schema for searching emails"""
    query: str = Field(description="The search query to find emails (Gmail search syntax)")
    max_results: Optional[int] = Field(default=10, description="Maximum number of emails to return")
    page_token: Optional[str] = Field(default=None, description="Token for pagination")


class GetEmailDetailsInput(BaseModel):
    """Schema for getting email details"""
    message_id: str = Field(description="The ID of the email to get details for")


class GetEmailAttachmentsInput(BaseModel):
    """Schema for getting email attachments"""
    message_id: str = Field(description="The ID of the email to get attachments for")


class DownloadEmailAttachmentInput(BaseModel):
    """Schema for downloading an email attachment"""
    message_id: str = Field(description="The ID of the email to download the attachment for")
    attachment_id: str = Field(description="The ID of the attachment to download")


class GetUserProfileInput(BaseModel):
    """Schema for getting user profile"""
    user_id: Optional[str] = Field(default="me", description="The user ID (use 'me' for authenticated user)")


# Register Gmail toolset
@ToolsetBuilder("Gmail")\
    .in_group("Google Workspace")\
    .with_description("Gmail integration for sending, receiving, and managing emails")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Gmail",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/gmail",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "https://www.googleapis.com/auth/gmail.send",
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.modify"
                ]
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path="/assets/icons/connectors/gmail.svg",
            app_group="Google Workspace",
            app_description="Gmail OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/gmail.svg"))\
    .build_decorator()
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

    def _handle_error(self, error: Exception, operation: str = "operation") -> tuple[bool, str]:
        """Handle errors with user-friendly authentication messages.

        Args:
            error: The exception that occurred
            operation: Description of the operation that failed

        Returns:
            tuple[bool, str]: (False, error_json_string)
        """
        error_msg = str(error).lower()

        # Check for AttributeError (client not properly initialized)
        if isinstance(error, AttributeError):
            if "users" in str(error) or "client" in error_msg:
                logger.error(f"Gmail client not properly initialized - authentication may be required: {error}")
                return False, json.dumps({
                    "error": "Gmail toolset is not authenticated. Please complete the OAuth flow first. "
                             "Go to Settings > Toolsets to authenticate your Gmail account."
                })

        # Check for authentication-related errors
        if isinstance(error, ValueError) or "not authenticated" in error_msg or "oauth" in error_msg or "authentication" in error_msg:
            logger.error(f"Gmail authentication error during {operation}: {error}")
            return False, json.dumps({
                "error": "Gmail toolset is not authenticated. Please complete the OAuth flow first. "
                         "Go to Settings > Toolsets to authenticate your Gmail account."
            })

        # Generic error handling
        logger.error(f"Failed to {operation}: {error}")
        return False, json.dumps({"error": str(error)})


    @tool(
        app_name="gmail",
        tool_name="reply",
        description="Reply to an email message",
        args_schema=ReplyInput,
        when_to_use=[
            "User wants to reply to an email",
            "User mentions 'Gmail' or 'email' + wants to reply",
            "User asks to respond to email"
        ],
        when_not_to_use=[
            "User wants to send new email (use send_email)",
            "User wants to search emails (use search_emails)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Reply to email",
            "Respond to message",
            "Reply to this email"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def reply(
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
            message = await self.client.users_messages_send(
                userId="me",
                body=message_body
            )
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message": message,
            })
        except Exception as e:
            return self._handle_error(e, "send reply")

    @tool(
        app_name="gmail",
        tool_name="draft_email",
        description="Create a draft email",
        args_schema=DraftEmailInput,
        when_to_use=[
            "User wants to create a draft email",
            "User mentions 'Gmail' + wants to draft",
            "User asks to save email as draft"
        ],
        when_not_to_use=[
            "User wants to send email (use send_email)",
            "User wants to search emails (use search_emails)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Create draft email",
            "Save email as draft",
            "Draft an email"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def draft_email(
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
            draft = await self.client.users_drafts_create(
                userId="me",
                body={"message": message_body}
            )
            return True, json.dumps({
                "draft_id": draft.get("id", ""),
                "draft": draft,
            })
        except Exception as e:
            return self._handle_error(e, "create draft")

    @tool(
        app_name="gmail",
        tool_name="send_email",
        description="Send an email via Gmail",
        args_schema=SendEmailInput,
        when_to_use=[
            "User wants to send an email",
            "User mentions 'Gmail' or 'email' + wants to send",
            "User asks to send message"
        ],
        when_not_to_use=[
            "User wants to reply (use reply)",
            "User wants to search emails (use search_emails)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Send email to user@company.com",
            "Email someone",
            "Send message via Gmail"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def send_email(
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
            message = await self.client.users_messages_send(
                userId="me",
                body=message_body
            )
            return True, json.dumps({
                "message_id": message.get("id", ""),
                "message": message,
            })
        except Exception as e:
            return self._handle_error(e, "send email")

    @tool(
        app_name="gmail",
        tool_name="search_emails",
        description="Search for email messages using Gmail search syntax",
        args_schema=SearchEmailsInput,
        when_to_use=[
            "User wants to search/find emails",
            "User mentions 'Gmail' or 'email' + wants to search",
            "User asks to find emails"
        ],
        when_not_to_use=[
            "User wants to send email (use send_email)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Search for emails from user@company.com",
            "Find emails about 'project'",
            "Show my unread emails"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def search_emails(
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
            result = await self.client.users_messages_list(
                userId="me",
                q=query,
                maxResults=max_results,
                pageToken=page_token,
            )

            messages = result.get("messages", [])
            next_page_token = result.get("nextPageToken")
            result_size_estimate = result.get("resultSizeEstimate", 0)

            # Enrich each message with metadata (subject, from, date, snippet)
            async def fetch_metadata(msg: Dict[str, Any]) -> Dict[str, Any]:
                try:
                    meta = await self.client.users_messages_get(
                        userId="me",
                        id=msg["id"],
                        format="metadata",
                        metadataHeaders=["Subject", "From", "To", "Date"],
                    )
                    headers = {
                        h["name"].lower(): h["value"]
                        for h in meta.get("payload", {}).get("headers", [])
                    }
                    return {
                        "id": msg["id"],
                        "threadId": msg.get("threadId", ""),
                        "subject": headers.get("subject", "(no subject)"),
                        "from": headers.get("from", ""),
                        "to": headers.get("to", ""),
                        "date": headers.get("date", ""),
                        "snippet": meta.get("snippet", ""),
                        "labelIds": meta.get("labelIds", []),
                    }
                except Exception:
                    return {
                        "id": msg["id"],
                        "threadId": msg.get("threadId", ""),
                        "subject": "(no subject)",
                        "from": "",
                        "to": "",
                        "date": "",
                        "snippet": "",
                        "labelIds": [],
                    }

            enriched = await asyncio.gather(*[fetch_metadata(m) for m in messages])

            return True, json.dumps({
                "messages": list(enriched),
                "nextPageToken": next_page_token,
                "resultSizeEstimate": result_size_estimate,
            })
        except Exception as e:
            return self._handle_error(e, "search emails")

    @tool(
        app_name="gmail",
        tool_name="get_email_details",
        description="Get a specific email message",
        args_schema=GetEmailDetailsInput,
        when_to_use=[
            "User wants to read a specific email",
            "User mentions 'Gmail' + has message ID",
            "User asks to show email content"
        ],
        when_not_to_use=[
            "User wants to search emails (use search_emails)",
            "User wants to send email (use send_email)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get email details",
            "Show me this email",
            "Read email message"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_email_details(
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
            message = await self.client.users_messages_get(
                userId="me",
                id=message_id,
                format="full",
            )
            return True, json.dumps(message)
        except Exception as e:
            return self._handle_error(e, f"get email details for {message_id}")

    @tool(
        app_name="gmail",
        tool_name="get_email_attachments",
        description="Get attachments for a specific email",
        args_schema=GetEmailAttachmentsInput,
        when_to_use=[
            "User wants to see email attachments",
            "User mentions 'Gmail' + wants attachments",
            "User asks for files attached to email"
        ],
        when_not_to_use=[
            "User wants email content (use get_email_details)",
            "User wants to search emails (use search_emails)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get attachments from email",
            "Show email attachments",
            "What files are attached?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_email_attachments(
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
            message = await self.client.users_messages_get(
                userId="me",
                id=message_id,
                format="full",
            )

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
            return self._handle_error(e, f"get email attachments for {message_id}")

    @tool(
        app_name="gmail",
        tool_name="get_user_profile",
        description="Get the authenticated user's Gmail profile",
        args_schema=GetUserProfileInput,
        when_to_use=[
            "User wants their Gmail account info",
            "User mentions 'Gmail' + wants profile",
            "User asks about their email account"
        ],
        when_not_to_use=[
            "User wants to send email (use send_email)",
            "User wants to search emails (use search_emails)",
            "User wants info ABOUT Gmail (use retrieval)",
            "No Gmail/email mention"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Get my Gmail profile",
            "Show my email account",
            "What's my Gmail address?"
        ],
        category=ToolCategory.COMMUNICATION
    )
    async def get_user_profile(
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
            profile = await self.client.users_get_profile(
                userId=user_id
            )
            return True, json.dumps({
                "email_address": profile.get("emailAddress", ""),
                "messages_total": profile.get("messagesTotal", 0),
                "threads_total": profile.get("threadsTotal", 0),
                "history_id": profile.get("historyId", "")
            })
        except Exception as e:
            return self._handle_error(e, "get user profile")

    # @tool(
    #     app_name="gmail",
    #     tool_name="download_email_attachment",
    #     description="Download an attachment from an email",
    #     args_schema=DownloadEmailAttachmentInput,
    # )
    # def download_email_attachment(
    #     self,
    #     message_id: str,
    #     attachment_id: str,
    # ) -> tuple[bool, str]:
    #     """Download an email attachment
    #     Args:
    #         message_id: The ID of the email
    #         attachment_id: The ID of the attachment
    #     Returns:
    #         tuple[bool, str]: True if the attachment is downloaded, False otherwise
    #     """
    #     try:
    #         # Use GoogleGmailDataSource method
    #         attachment = self._run_async(self.client.users_messages_attachments_get(
    #             userId="me",
    #             messageId=message_id,
    #             id=attachment_id,
    #         ))
    #         return True, json.dumps(attachment)
    #     except Exception as e:
    #         logger.error(f"Failed to download attachment {attachment_id} from message {message_id}: {e}")
    #         return False, json.dumps({"error": str(e)})
