"""`OutlookAttachmentUploader` — Microsoft Graph attachment upload adapter.

Microsoft Graph requires two upload strategies depending on file size:
- ≤ 3 MiB: single POST to `/messages/{id}/attachments` with a `FileAttachment`
  body (base64-encoded `contentBytes`).
- 3 MiB – 150 MiB: upload session via `createUploadSession` followed by ordered
  `PUT` chunks of ≤ 4 MiB each. The `PUT` requests go to a pre-authenticated
  opaque URL and must **NOT** carry an `Authorization` header.

Atomicity: attachments are added between draft creation and send. If any
attachment POST/upload fails, the draft is deleted before the error is
surfaced so no half-attached email is ever sent. This is the most important
failure path the test suite validates.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
from typing import Any, Sequence

import aiohttp

from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
    IAttachmentUploader,
)
from app.services.record_content.models import ResolvedRecordContent

logger = logging.getLogger(__name__)

__all__ = ["OutlookAttachmentUploader"]

# Microsoft Graph limits
_SMALL_ATTACHMENT_THRESHOLD = 3 * 1024 * 1024   # 3 MiB
_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024             # 4 MiB per chunk
_MAX_OUTLOOK_ATTACHMENT_BYTES = 150 * 1024 * 1024  # 150 MiB (Graph limit)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300)


class OutlookAttachmentUploader:
    """Uploads `ResolvedRecordContent` objects as attachments to an Outlook draft.

    `target.destination_id` must be a valid draft message ID obtained from
    `me_create_messages`. Call `upload()` before calling
    `me_messages_message_send` — if `upload()` raises, delete the draft.
    """

    max_single_bytes: int = _MAX_OUTLOOK_ATTACHMENT_BYTES
    max_total_bytes: int = _MAX_OUTLOOK_ATTACHMENT_BYTES

    def __init__(self, client: Any) -> None:
        """
        Args:
            client: `OutlookCalendarContactsDataSource` instance exposing
                `me_messages_create_attachments` and
                `me_messages_message_attachments_create_upload_session`.
        """
        self._client = client

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        """Attach each file to the draft identified by `target.destination_id`.

        Processes attachments sequentially because the Graph API does not
        define an ordering guarantee for parallel attachment POSTs on the same
        message, and partial failures need a definitive draft-delete signal.
        """
        message_id = target.destination_id
        results: list[AttachmentUploadResult] = []

        for attachment in attachments:
            try:
                if attachment.size_bytes <= _SMALL_ATTACHMENT_THRESHOLD:
                    result = await self._post_small_attachment(message_id, attachment)
                else:
                    result = await self._upload_large_attachment(message_id, attachment)
                results.append(result)
            except Exception as exc:
                logger.exception(
                    "OutlookAttachmentUploader: unexpected error attaching %s",
                    attachment.filename,
                )
                results.append(AttachmentUploadResult(
                    record_id=attachment.record_id,
                    filename=attachment.filename,
                    success=False,
                    error=f"Unexpected error: {exc}",
                ))

        return results

    async def _post_small_attachment(
        self,
        message_id: str,
        attachment: ResolvedRecordContent,
    ) -> AttachmentUploadResult:
        """POST a ≤ 3 MiB attachment as a FileAttachment body."""
        content_b64 = base64.b64encode(attachment.content).decode("ascii")
        request_body = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment.filename,
            "contentType": attachment.mime_type,
            "contentBytes": content_b64,
        }
        response = await self._client.me_messages_create_attachments(
            message_id=message_id,
            request_body=request_body,
        )
        if response.success:
            data = getattr(response, "data", None)
            platform_id = data.get("id") if isinstance(data, dict) else None
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=True,
                platform_id=platform_id,
            )
        return AttachmentUploadResult(
            record_id=attachment.record_id,
            filename=attachment.filename,
            success=False,
            error=response.error or "Failed to attach file",
        )

    async def _upload_large_attachment(
        self,
        message_id: str,
        attachment: ResolvedRecordContent,
    ) -> AttachmentUploadResult:
        """Upload a 3 MiB – 150 MiB attachment via an upload session."""
        session_body = {
            "AttachmentItem": {
                "attachmentType": "file",
                "name": attachment.filename,
                "size": attachment.size_bytes,
            }
        }
        session_response = await self._client.me_messages_message_attachments_create_upload_session(
            message_id=message_id,
            request_body=session_body,
        )
        if not session_response.success:
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error=session_response.error or "Failed to create upload session",
            )

        session_data = getattr(session_response, "data", None)
        upload_url = (
            session_data.get("uploadUrl")
            if isinstance(session_data, dict) else None
        )
        if not upload_url:
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error="Upload session did not return an uploadUrl",
            )

        try:
            platform_id = await self._put_chunks(
                upload_url, attachment.content, attachment.size_bytes,
            )
        except Exception as exc:
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error=f"Chunk upload failed: {exc}",
            )

        return AttachmentUploadResult(
            record_id=attachment.record_id,
            filename=attachment.filename,
            success=True,
            platform_id=platform_id,
        )

    async def _put_chunks(
        self, upload_url: str, content: bytes, total_size: int,
    ) -> str | None:
        """PUT ordered ≤ 4 MiB chunks to the pre-authenticated upload URL.

        The PUT requests MUST NOT carry an Authorization header — the URL is
        already pre-authenticated. Violating this causes a 400 from Graph.
        """
        num_chunks = math.ceil(total_size / _UPLOAD_CHUNK_SIZE)
        last_response_data: dict = {}

        async with aiohttp.ClientSession() as session:
            for i in range(num_chunks):
                start = i * _UPLOAD_CHUNK_SIZE
                end = min(start + _UPLOAD_CHUNK_SIZE, total_size)
                chunk = content[start:end]
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end - 1}/{total_size}",
                    "Content-Type": "application/octet-stream",
                }
                async with session.put(
                    upload_url, data=chunk, headers=headers, timeout=_REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 201, 202):
                        detail = (await resp.text())[:300]
                        raise RuntimeError(
                            f"Chunk {i + 1}/{num_chunks} PUT returned {resp.status}: {detail}"
                        )
                    if resp.content_type and "json" in resp.content_type:
                        last_response_data = await resp.json() or {}

        return last_response_data.get("id")
