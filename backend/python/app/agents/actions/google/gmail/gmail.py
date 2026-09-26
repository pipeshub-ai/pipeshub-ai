import asyncio
import json
import logging
from dataclasses import dataclass
from email.utils import parseaddr
from http import HTTPStatus
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from pydantic import BaseModel, Field

from app.agents.actions.google.gmail.utils import GmailUtils
from app.agents.actions.util.google_api_errors import GoogleToolWording, google_error_message
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agents.actions.util.attachments import (
    attachment_record_ids_parameter,
    resolve_attachments,
)
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import (
    args_template,
    confirmation,
    entity_summary,
    list_summary,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

logger = logging.getLogger(__name__)

_MAX_SEARCH_RESULTS = 500


def _gmail_message_label(message: dict) -> str:
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    subject = next(
        (h.get("value") for h in headers if isinstance(h, dict) and h.get("name") == "Subject"),
        None,
    )
    return subject or message.get("snippet") or message.get("id") or "?"


_GMAIL_WORDING = GoogleToolWording(
    product="Gmail",
    toolset="Gmail",
    access="Gmail access",
    not_found="that email. Check the message id, or call search_emails to find the right one.",
    gone="that email has already been deleted.",
)


def _gmail_failure(error: Exception, action: str) -> tuple[bool, str]:
    logger.error("Failed to %s: %s", action, error)
    return False, json.dumps({"error": google_error_message(error, action, _GMAIL_WORDING)})


def _attachments_in(part: dict[str, Any]) -> list[dict[str, Any]]:
    """Every named part at any depth: mail clients nest attachments inside multipart/related and /mixed."""
    found: list[dict[str, Any]] = []
    if part.get("filename"):
        body = part.get("body") or {}
        found.append({
            "attachment_id": body.get("attachmentId"),
            "filename": part["filename"],
            "mime_type": part.get("mimeType"),
            "size": body.get("size"),
        })
    for child in part.get("parts") or []:
        found.extend(_attachments_in(child))
    return found


@dataclass(frozen=True)
class _ReplyContext:
    thread_id: str | None = None
    rfc_message_id: str | None = None
    references: str | None = None


def _unreadable(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": msg["id"],
        "threadId": msg.get("threadId", ""),
        "subject": "(metadata unavailable)",
        "from": "",
        "to": "",
        "date": "",
        "snippet": "",
        "labelIds": [],
        "unreadable": True,
    }


def _recipient_problem(mail_to: list[str], *others: list[str] | None) -> str | None:
    if not mail_to:
        return "Give at least one recipient email address in mail_to."
    for entry in [*mail_to, *(e for group in others for e in group or [])]:
        _, address = parseaddr(entry or "")
        if "@" not in address or " " in address.strip():
            return (
                f"'{(entry or '').strip()}' is not an email address. Find the person's address first "
                "(for example from an earlier email) and try again."
            )
    return None


def _refuse_file_paths() -> tuple[bool, str]:
    # Paths would be read from the server's own disk, so the model could mail out any file there.
    return False, json.dumps({
        "error": (
            "Files can't be attached by path. Attach PipesHub files (chat uploads, artifacts, knowledge-base "
            "files) by passing their record IDs in attachment_record_ids instead."
        )
    })


# Pydantic schemas for Gmail tools
class SendEmailInput(BaseModel):
    """Schema for sending an email"""
    mail_to: List[str] = Field(description="List of email addresses to send the email to")
    mail_subject: str = Field(description="The subject of the email")
    mail_cc: Optional[List[str]] = Field(default=None, description="List of email addresses to CC")
    mail_bcc: Optional[List[str]] = Field(default=None, description="List of email addresses to BCC")
    mail_body: Optional[str] = Field(default=None, description="The body content of the email")
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
    thread_id: Optional[str] = Field(default=None, description="The thread ID to maintain conversation context")


class DraftEmailInput(BaseModel):
    """Schema for creating a draft email"""
    mail_to: List[str] = Field(description="List of email addresses to send the email to")
    mail_subject: str = Field(description="The subject of the email")
    mail_cc: Optional[List[str]] = Field(default=None, description="List of email addresses to CC")
    mail_bcc: Optional[List[str]] = Field(default=None, description="List of email addresses to BCC")
    mail_body: Optional[str] = Field(default=None, description="The body content of the email")


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
            icon_path=IconPaths.connector_icon("gmail"),
            app_group="Google Workspace",
            app_description="Gmail OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("gmail"))
        .add_documentation_link(DocumentationLink(
            "Gmail API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/google-workspace/gmail",
            "pipeshub",
        )))\
    .build_decorator()
class Gmail:
    """Gmail tool exposed to the agents using GoogleGmailDataSource"""
    def __init__(self, client: GoogleClient, *, state: Any = None) -> None:
        """Initialize the Gmail tool.

        Args:
            client: Authenticated Gmail client.
            state: Agent runtime state (ChatState). Required for attachment resolution.
        """
        self.client = GoogleGmailDataSource(client)
        self.chat_state = state

    async def _reply_context(self, message_id: str, thread_id: str | None) -> "_ReplyContext":
        """Threading comes from the original's RFC 822 headers; Gmail's own message id is not one."""
        original = await self.client.users_messages_get(
            userId="me", id=message_id, format="metadata", metadataHeaders=["Message-ID", "References"],
        )
        found = {
            str(h.get("name", "")).lower(): h.get("value")
            for h in (original.get("payload") or {}).get("headers") or []
            if isinstance(h, dict)
        }
        rfc_message_id = found.get("message-id")
        chain = " ".join(v for v in (found.get("references"), rfc_message_id) if v)
        return _ReplyContext(
            thread_id=original.get("threadId") or thread_id,
            rfc_message_id=rfc_message_id,
            references=chain or None,
        )

    async def _resolve_in_memory_attachments(
        self,
        attachment_record_ids: Optional[List[str]],
        destination: str = "",
    ) -> Optional[List[tuple]]:
        """Resolve PipesHub record IDs to in-memory (filename, bytes, mime_type) tuples.

        Returns None when no record IDs are provided, so
        `transform_message_body` can skip the multipart path entirely.
        Raises ValueError with a user-facing message on size-cap violations.
        """
        from app.agents.actions.util.attachments import emit_attachment_audit

        if not attachment_record_ids:
            return None

        bundle = await resolve_attachments(self.chat_state, attachment_record_ids)

        state = self.chat_state or {}
        org_id = state.get("org_id", "") if hasattr(state, "get") else ""
        user_id = state.get("user_id", "") if hasattr(state, "get") else ""

        for failure in bundle.failures:
            emit_attachment_audit(
                org_id=org_id,
                user_id=user_id,
                record_id=failure.ref,
                filename=failure.ref,
                target_app="gmail",
                destination=destination,
                success=False,
                error=failure.error,
            )

        if bundle.failures and not bundle.resolved:
            raise ValueError(
                "Attachment resolution failed: "
                + "; ".join(f"{f.ref}: {f.error}" for f in bundle.failures)
            )
        if bundle.failures:
            logger.warning(
                "Gmail: some attachment_record_ids could not be resolved: %s",
                [f.to_dict() for f in bundle.failures],
            )

        for r in bundle.resolved:
            emit_attachment_audit(
                org_id=org_id,
                user_id=user_id,
                record_id=r.record_id,
                filename=r.filename,
                target_app="gmail",
                destination=destination,
                success=True,
                size_bytes=r.size_bytes,
            )

        return [
            (r.filename, r.content, r.mime_type)
            for r in bundle.resolved
        ]

    @tool(
        path="/tools/gmail/reply",
        short_description="Reply to an email message",
        description=(
            "Reply to an email message in Gmail. Sends a reply to an existing email thread. "
            "Optionally attach PipesHub records (chat uploads, artifacts, KB files) by passing "
            "their record IDs in attachment_record_ids."
        ),
        parameters=[
            ToolParameter(name="message_id", type=ParameterType.STRING, description="The ID of the email to reply to", required=True),
            ToolParameter(name="mail_to", type=ParameterType.ARRAY, description="List of email addresses to send the reply to", required=True, items={"type": "string"}),
            ToolParameter(name="mail_subject", type=ParameterType.STRING, description="The subject of the reply email", required=True),
            ToolParameter(name="mail_cc", type=ParameterType.ARRAY, description="List of email addresses to CC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_bcc", type=ParameterType.ARRAY, description="List of email addresses to BCC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_body", type=ParameterType.STRING, description="The body content of the reply email", required=False),
            ToolParameter(name="thread_id", type=ParameterType.STRING, description="The thread ID to maintain conversation context", required=False),
            attachment_record_ids_parameter(required=False),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="write")],
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
        attachment_record_ids: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Reply to an email, optionally attaching PipesHub records."""
        if mail_attachments:
            return _refuse_file_paths()
        problem = _recipient_problem(mail_to, mail_cc, mail_bcc)
        if problem:
            return False, json.dumps({"error": problem})
        try:
            context = await self._reply_context(message_id, thread_id)
        except Exception as e:
            return _gmail_failure(e, "read the email being replied to, so no reply was sent")
        try:
            destination = ", ".join(mail_to) if mail_to else ""
            in_memory = await self._resolve_in_memory_attachments(attachment_record_ids, destination=destination)
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                None,
                context.thread_id,
                context.rfc_message_id,
                in_memory_attachments=in_memory,
                references=context.references,
            )
            message = await self.client.users_messages_send(userId="me", body=message_body)
            return True, json.dumps({"message_id": message.get("id", ""), "message": message})
        except ValueError as exc:
            return False, json.dumps({"error": str(exc)})
        except Exception as e:
            return _gmail_failure(e, "send the reply")

    @tool(
        path="/tools/gmail/draft_email",
        short_description="Create a draft email",
        description=(
            "Create a draft email in Gmail. The draft is saved but not sent. "
            "Optionally attach PipesHub records by passing their record IDs in attachment_record_ids."
        ),
        parameters=[
            ToolParameter(name="mail_to", type=ParameterType.ARRAY, description="List of email addresses to send the email to", required=True, items={"type": "string"}),
            ToolParameter(name="mail_subject", type=ParameterType.STRING, description="The subject of the email", required=True),
            ToolParameter(name="mail_cc", type=ParameterType.ARRAY, description="List of email addresses to CC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_bcc", type=ParameterType.ARRAY, description="List of email addresses to BCC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_body", type=ParameterType.STRING, description="The body content of the email", required=False),
            attachment_record_ids_parameter(required=False),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="write")],
    )
    async def draft_email(
        self,
        mail_to: List[str],
        mail_subject: str,
        mail_cc: Optional[List[str]] = None,
        mail_bcc: Optional[List[str]] = None,
        mail_body: Optional[str] = None,
        mail_attachments: Optional[List[str]] = None,
        attachment_record_ids: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Draft an email, optionally attaching PipesHub records."""
        if mail_attachments:
            return _refuse_file_paths()
        problem = _recipient_problem(mail_to, mail_cc, mail_bcc)
        if problem:
            return False, json.dumps({"error": problem})
        try:
            destination = ", ".join(mail_to) if mail_to else ""
            in_memory = await self._resolve_in_memory_attachments(attachment_record_ids, destination=destination)
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                None,
                in_memory_attachments=in_memory,
            )
            draft = await self.client.users_drafts_create(
                userId="me", body={"message": message_body}
            )
            return True, json.dumps({"draft_id": draft.get("id", ""), "draft": draft})
        except ValueError as exc:
            return False, json.dumps({"error": str(exc)})
        except Exception as e:
            return _gmail_failure(e, "save the draft")

    @tool(
        path="/tools/gmail/send_email",
        short_description="Send an email via Gmail",
        description=(
            "Send an email via Gmail. Composes and delivers the message immediately. "
            "Optionally attach PipesHub records (chat uploads, artifacts, KB files) by passing "
            "their record IDs in attachment_record_ids."
        ),
        parameters=[
            ToolParameter(name="mail_to", type=ParameterType.ARRAY, description="List of email addresses to send the email to", required=True, items={"type": "string"}),
            ToolParameter(name="mail_subject", type=ParameterType.STRING, description="The subject of the email", required=True),
            ToolParameter(name="mail_cc", type=ParameterType.ARRAY, description="List of email addresses to CC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_bcc", type=ParameterType.ARRAY, description="List of email addresses to BCC", required=False, items={"type": "string"}),
            ToolParameter(name="mail_body", type=ParameterType.STRING, description="The body content of the email", required=False),
            ToolParameter(name="thread_id", type=ParameterType.STRING, description="The thread ID to maintain conversation context", required=False),
            ToolParameter(name="message_id", type=ParameterType.STRING, description="The message ID for threading", required=False),
            attachment_record_ids_parameter(required=False),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="write")],
        args_summary=lambda args: f"Sending email to {', '.join(args.get('mail_to') or []) or '?'}",
        result_summary=confirmation("Email sent"),
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
        attachment_record_ids: Optional[List[str]] = None,
    ) -> tuple[bool, str]:
        """Send an email, optionally attaching PipesHub records."""
        if mail_attachments:
            return _refuse_file_paths()
        problem = _recipient_problem(mail_to, mail_cc, mail_bcc)
        if problem:
            return False, json.dumps({"error": problem})
        context = _ReplyContext(thread_id=thread_id)
        if message_id:
            try:
                context = await self._reply_context(message_id, thread_id)
            except Exception as e:
                return _gmail_failure(e, "read the email this one answers, so nothing was sent")
        try:
            destination = ", ".join(mail_to) if mail_to else ""
            in_memory = await self._resolve_in_memory_attachments(attachment_record_ids, destination=destination)
            message_body = GmailUtils.transform_message_body(
                mail_to,
                mail_subject,
                mail_cc,
                mail_bcc,
                mail_body,
                None,
                context.thread_id,
                context.rfc_message_id,
                in_memory_attachments=in_memory,
                references=context.references,
            )
            message = await self.client.users_messages_send(userId="me", body=message_body)
            return True, json.dumps({"message_id": message.get("id", ""), "message": message})
        except ValueError as exc:
            return False, json.dumps({"error": str(exc)})
        except Exception as e:
            return _gmail_failure(e, "send the email")

    @tool(
        path="/tools/gmail/search_emails",
        short_description="Search for email messages using Gmail search syntax",
        description=(
            "Search for email messages using Gmail search syntax. "
            "Supports standard Gmail search operators (from:, to:, subject:, is:unread, etc.)."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="The search query to find emails (Gmail search syntax)", required=True),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Maximum number of emails to return", required=False, default=10),
            ToolParameter(name="page_token", type=ParameterType.STRING, description="Token for pagination", required=False),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="read")],
        args_summary=args_template('Searching Gmail: "{query}"', "query"),
        result_summary=list_summary(("messages",), lambda m: m.get("subject") or "(no subject)", "email"),
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
        if max_results is not None and not 1 <= max_results <= _MAX_SEARCH_RESULTS:
            return False, json.dumps({
                "error": (
                    f"max_results must be between 1 and {_MAX_SEARCH_RESULTS}; Gmail returns at most "
                    f"{_MAX_SEARCH_RESULTS} messages per page. Use page_token to read further pages."
                )
            })
        try:
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
            async def fetch_metadata(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
                except HttpError as e:
                    if e.resp.status == HTTPStatus.NOT_FOUND:
                        logger.debug("Gmail message %s no longer exists, skipping", msg["id"])
                        return None
                    return _unreadable(msg)
                except Exception:
                    return _unreadable(msg)

            enriched = [m for m in await asyncio.gather(*[fetch_metadata(m) for m in messages]) if m is not None]
            payload: dict[str, Any] = {
                "messages": enriched,
                "nextPageToken": next_page_token,
                "resultSizeEstimate": result_size_estimate,
            }
            unreadable = [m["id"] for m in enriched if m.get("unreadable")]
            if unreadable:
                payload["unreadable_message_ids"] = unreadable
                payload["note"] = (
                    f"The details of {len(unreadable)} of {len(enriched)} messages could not be read, so their "
                    "subject, sender and date are missing. Call get_email_details with those ids to read them."
                )
            return True, json.dumps(payload)
        except Exception as e:
            return _gmail_failure(e, "search emails")

    @tool(
        path="/tools/gmail/get_email_details",
        short_description="Get a specific email message",
        description="Get detailed information about a specific email message by its ID, including headers, body, and metadata.",
        parameters=[
            ToolParameter(name="message_id", type=ParameterType.STRING, description="The ID of the email to get details for", required=True),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="read")],
        args_summary=args_template("Fetching Gmail message {message_id}", "message_id"),
        result_summary=entity_summary(lambda e: f"Fetched email: {_gmail_message_label(e)}", path=()),
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
            return _gmail_failure(e, "read that email")

    @tool(
        path="/tools/gmail/get_email_attachments",
        short_description="Get attachments for a specific email",
        description="Get the list of attachments for a specific email message, including filenames, MIME types, and sizes.",
        parameters=[
            ToolParameter(name="message_id", type=ParameterType.STRING, description="The ID of the email to get attachments for", required=True),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="read")],
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

            return True, json.dumps(_attachments_in(message.get("payload") or {}))
        except Exception as e:
            return _gmail_failure(e, "list that email's attachments")

    @tool(
        path="/tools/gmail/get_user_profile",
        short_description="Get the authenticated user's Gmail profile",
        description="Get the authenticated user's Gmail profile including email address, total messages, and threads count.",
        parameters=[
            ToolParameter(name="user_id", type=ParameterType.STRING, description="Always 'me': only the signed-in user's own profile can be read", required=False, default="me"),
        ],
        tags=[Tag(key="category", value="email"), Tag(key="type", value="read")],
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
        if (user_id or "me").strip().lower() != "me":
            return False, json.dumps({
                "error": (
                    "get_user_profile reads only the signed-in user's own mailbox. "
                    "Call it again without user_id."
                )
            })
        try:
            profile = await self.client.users_get_profile(userId="me")
            return True, json.dumps({
                "email_address": profile.get("emailAddress", ""),
                "messages_total": profile.get("messagesTotal", 0),
                "threads_total": profile.get("threadsTotal", 0),
                "history_id": profile.get("historyId", "")
            })
        except Exception as e:
            return _gmail_failure(e, "read the Gmail profile")

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
