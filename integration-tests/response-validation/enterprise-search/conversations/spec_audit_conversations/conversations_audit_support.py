"""Client, constants and helpers for the strict OpenAPI audit of /api/v1/conversations."""

from __future__ import annotations

import io
from typing import Any, Callable

import requests

from helper.clients.conversations_client import ConversationsClient
from helper.second_user import SecondUser

CONVERSATIONS_BASE = "/api/v1/conversations"

MISSING_CONVERSATION_ID = "0123456789abcdef01234567"
MALFORMED_CONVERSATION_ID = "not-an-object-id"

# Attachment record ids are graph keys (uuid4), not Mongo ObjectIds.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
# Decodes to "bad%id", which guardPathParams refuses before validation runs.
UNSAFE_RECORD_ID = "bad%25id"
OVERLONG_RECORD_ID = "a" * 257

UNKNOWN_RUN_ID = "11111111-1111-4111-8111-111111111111"
MALFORMED_RUN_ID = "not-a-uuid"

PROJECT_VISIBILITIES = ("private", "project")

# Set explicitly in chat.session.schema.ts; holds chat and agent sessions alike.
COLLECTION = "chatSessions"

MultipartFiles = list[tuple[str, tuple[str, io.BytesIO, str]]]
SeedConversation = Callable[..., str]
UploadAttachment = Callable[..., requests.Response]


class ConversationsAuditClient(ConversationsClient):
    """ConversationsClient plus the routes it has no method for, as the shared org admin."""

    def upload_attachments(
        self,
        files: MultipartFiles | None,
        *,
        conversation_id: str | None = None,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """POST /attachments/upload as multipart; ``files=None`` sends no file part."""
        data = {} if conversation_id is None else {"conversationId": conversation_id}
        if files is None:
            # requests only emits multipart when a file part is present.
            return self.post(
                "/attachments/upload",
                auth=auth,
                files={"conversationId": (None, conversation_id or "")},
                **kwargs,
            )
        return self.post(
            "/attachments/upload", auth=auth, files=files, data=data, **kwargs
        )

    def delete_attachment(
        self, record_id: str, *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        return self.delete(f"/attachments/{record_id}", auth=auth, **kwargs)

    def cancel_stream(
        self,
        conversation_id: str,
        run_id: str = UNKNOWN_RUN_ID,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        kwargs.setdefault("json", {"runId": run_id})
        return self.post(f"/{conversation_id}/cancel", auth=auth, **kwargs)


def attachment_files(
    name: str = "spec-audit.txt",
    content: bytes = b"Seeded by the conversations spec audit.\n",
    mimetype: str = "text/plain",
) -> MultipartFiles:
    """One ``files`` part. The default is plain text: parsed in-process, no OCR or LLM."""
    return [("files", (name, io.BytesIO(content), mimetype))]


def uploaded_record_ids(resp: requests.Response) -> list[str]:
    """Record ids from a successful upload response; empty for anything else."""
    if resp.status_code >= 300:
        return []
    try:
        attachments = resp.json().get("attachments") or []
    except (ValueError, AttributeError):
        return []
    return [a["recordId"] for a in attachments if isinstance(a, dict) and a.get("recordId")]


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a conversations route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = {"Authorization": f"Bearer {user.token}"}
    # With files, requests must set the multipart boundary itself.
    if "files" not in kwargs:
        headers["Content-Type"] = "application/json"
    return requests.request(
        method,
        f"{user.base_url}{CONVERSATIONS_BASE}{path}",
        headers=headers,
        **kwargs,
    )
