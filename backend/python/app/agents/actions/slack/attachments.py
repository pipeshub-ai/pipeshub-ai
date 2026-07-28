"""`SlackAttachmentUploader` — uploads PipesHub records as Slack files.

Uses `files_upload_v2` (slack_sdk >= 3.19.0) for all uploads. When
multiple files are provided, they are batched into a single
`file_uploads` call so the message carries all files in one post.

Slack has no formal single-file size limit via the v2 API path but
enforces workspace storage quotas; the uploader enforces the global
25 MiB-per-file cap inherited from the record-content resolver.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.agents.actions.util.attachment_upload import (
    AttachmentTarget,
    AttachmentUploadResult,
)
from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES
from app.services.record_content.models import ResolvedRecordContent

logger = logging.getLogger(__name__)

__all__ = ["SlackAttachmentUploader"]

# Slack recommends batching via file_uploads list rather than parallel
# single-file calls, which avoids race conditions on the per-message post.
_BATCH_UPLOAD = True


class SlackAttachmentUploader:
    """Upload `ResolvedRecordContent` objects as Slack files.

    `target.destination_id` is the channel ID (mandatory for shared files).
    `target.thread_ts` pins the upload to a thread if set.
    `target.extra.get("initial_comment")` is posted alongside the files.
    """

    max_single_bytes: int = DEFAULT_MAX_STAGE_BYTES
    max_total_bytes: int = DEFAULT_MAX_STAGE_BYTES

    def __init__(self, client: Any) -> None:
        self._client = client

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        if not attachments:
            return []

        channel = target.destination_id or None
        thread_ts = target.thread_ts
        initial_comment = target.extra.get("initial_comment")

        if len(attachments) == 1:
            return [await self._upload_single(attachments[0], channel=channel, thread_ts=thread_ts, initial_comment=initial_comment)]

        return await self._upload_batch(
            list(attachments),
            channel=channel,
            thread_ts=thread_ts,
            initial_comment=initial_comment,
        )

    async def _upload_single(
        self,
        attachment: ResolvedRecordContent,
        *,
        channel: str | None,
        thread_ts: str | None,
        initial_comment: str | None,
    ) -> AttachmentUploadResult:
        try:
            response = await self._client.files_upload_v2(
                filename=attachment.filename,
                content=attachment.content,
                channel=channel,
                thread_ts=thread_ts,
                initial_comment=initial_comment,
            )
            if response.success:
                platform_id = self._extract_file_id(response.data)
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
                error=response.error or "files_upload_v2 returned failure",
            )
        except Exception as exc:
            logger.warning(
                "SlackAttachmentUploader: single upload failed for %s: %s",
                attachment.filename, exc,
            )
            return AttachmentUploadResult(
                record_id=attachment.record_id,
                filename=attachment.filename,
                success=False,
                error=str(exc),
            )

    async def _upload_batch(
        self,
        attachments: list[ResolvedRecordContent],
        *,
        channel: str | None,
        thread_ts: str | None,
        initial_comment: str | None,
    ) -> list[AttachmentUploadResult]:
        """Upload multiple files as a single Slack message via file_uploads list."""
        file_uploads = [
            {"filename": a.filename, "content": a.content}
            for a in attachments
        ]
        try:
            response = await self._client.files_upload_v2(
                file_uploads=file_uploads,
                channel=channel,
                initial_comment=initial_comment,
                thread_ts=thread_ts,
            )
            if response.success:
                # Batch upload: all files share one success/failure outcome.
                return [
                    AttachmentUploadResult(
                        record_id=a.record_id,
                        filename=a.filename,
                        success=True,
                    )
                    for a in attachments
                ]
            # Batch failed — report failure for each file.
            error = response.error or "files_upload_v2 batch returned failure"
            return [
                AttachmentUploadResult(
                    record_id=a.record_id,
                    filename=a.filename,
                    success=False,
                    error=error,
                )
                for a in attachments
            ]
        except Exception as exc:
            logger.warning("SlackAttachmentUploader: batch upload failed: %s", exc)
            return [
                AttachmentUploadResult(
                    record_id=a.record_id,
                    filename=a.filename,
                    success=False,
                    error=str(exc),
                )
                for a in attachments
            ]

    @staticmethod
    def _extract_file_id(data: Any) -> str | None:
        if not data or not isinstance(data, dict):
            return None
        # v2 response shape: {"files": [{"id": "F0123"}]} or {"file": {"id": "…"}}
        files = data.get("files")
        if files and isinstance(files, list):
            return files[0].get("id") if files else None
        file = data.get("file")
        if file and isinstance(file, dict):
            return file.get("id")
        return None
