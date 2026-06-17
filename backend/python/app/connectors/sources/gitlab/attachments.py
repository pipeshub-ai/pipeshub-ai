"""
Attachment utilities for the GitLab connector.

Responsibilities:
- Parse GitLab upload markdown syntax (``![...](...)`` and ``[...](...)``).
- Embed images inline as base64 for content blocks.
- Build FileRecord RecordUpdates from attachment lists.
- Build ChildRecord / CommentAttachment references for embedding in blocks.
- Stream raw attachment bytes via ``get_attachment_files_content``.
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
)
from app.models.entities import FileRecord, Record, RecordGroupType, RecordType
from app.models.blocks import ChildRecord, ChildType, CommentAttachment
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .constants import IMAGE_EXTENSIONS, UPLOAD_PATTERN
from .models import FileAttachment, GitlabLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class AttachmentsHelper:
    """Attachment parsing and file-record building for ``GitLabConnector``."""

    EXTENSION_TO_MIME: dict[str, str] = {
        "png": "png", "jpg": "jpeg", "jpeg": "jpeg",
        "gif": "gif", "webp": "webp", "bmp": "bmp", "svg": "svg+xml",
    }

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Markdown parsing
    # ------------------------------------------------------------------

    async def parse_gitlab_uploads(self, text: str) -> tuple[list[FileAttachment], str]:
        """Parse GitLab upload markdown and return (attachments, cleaned_text)."""
        if not isinstance(text, str):
            return [], ""
        files: list[FileAttachment] = []
        cleaned_text = text
        for match in list(UPLOAD_PATTERN.finditer(text)):
            full_match = match.group("full")
            href = match.group("href")
            filename = unquote(match.group("filename"))
            if "." not in filename or filename.endswith("."):
                extension = "txt"
            else:
                extension = filename.rsplit(".", 1)[-1].lower()
            category = (
                GitlabLiterals.IMAGE.value
                if extension in IMAGE_EXTENSIONS
                else GitlabLiterals.ATTACHMENT.value
            )
            try:
                files.append(FileAttachment(href=href, filename=filename, filetype=extension, category=category))
            except Exception as e:
                self.logger.warning("Skipping malformed attachment: %s", e)
                continue
            cleaned_text = cleaned_text.replace(full_match, "")
        cleaned_text = re.sub(r"\n\s*\n+", "\n\n", cleaned_text).strip()
        return files, cleaned_text

    # ------------------------------------------------------------------
    # Image embedding
    # ------------------------------------------------------------------

    async def embed_images_as_base64(self, body_content: str, base_project_url: str) -> str:
        """Replace GitLab image attachments with inline base64 data URIs."""
        attachments, markdown_content_clean = await self.parse_gitlab_uploads(body_content)
        if not attachments:
            return markdown_content_clean
        _MAX_IMAGE_BYTES = 4 * 1024 * 1024
        for attach in attachments:
            if attach.category != GitlabLiterals.IMAGE.value:
                continue
            full_attachment_url = f"{base_project_url}{attach.href}"
            try:
                response = await self.c.data_source.get_img_bytes(full_attachment_url)
                if response.success and response.data:
                    if len(response.data) > _MAX_IMAGE_BYTES:
                        self.logger.debug(
                            "Skipping image %s: size %d bytes exceeds limit",
                            attach.href, len(response.data),
                        )
                        continue
                    fmt = self.EXTENSION_TO_MIME.get(attach.filetype, "png")
                    raw_b64 = await asyncio.to_thread(base64.b64encode, response.data)
                    base64_data = raw_b64.decode(GitlabLiterals.UTF_8.value)
                    md_image_data = f"![Image](data:image/{fmt};base64,{base64_data})"
                    markdown_content_clean += md_image_data
            except Exception as e:
                self.logger.warning("Error embedding image from %s: %s", attach.href, e)
                continue
        return markdown_content_clean

    # ------------------------------------------------------------------
    # File record creation
    # ------------------------------------------------------------------

    async def make_file_records_from_list(
        self, attachments: list[FileAttachment], record: Record
    ) -> list[RecordUpdate]:
        """Build FileRecord RecordUpdates for non-image attachments."""
        c = self.c
        project_id = record.external_record_group_id.split("-")[0]
        base_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"
        list_records_new: list[RecordUpdate] = []
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            full_attachment_url = f"{base_url}{attach.href}"
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=full_attachment_url
                )
            filerecord = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                org_id=c.data_entities_processor.org_id,
                record_name=attach.filename,
                record_type=RecordType.FILE.value,
                external_record_id=full_attachment_url,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR,
                weburl=full_attachment_url,
                record_group_type=RecordGroupType.PROJECT.value,
                parent_external_record_id=record.external_record_id,
                parent_record_type=record.record_type,
                external_record_group_id=record.external_record_group_id,
                mime_type=getattr(MimeTypes, attach.filetype.upper(), MimeTypes.UNKNOWN).value,
                extension=attach.filetype.lower(),
                is_file=True,
                inherit_permissions=True,
                preview_renderable=True,
                version=0,
                size_in_bytes=0,
                source_created_at=get_epoch_timestamp_in_ms(),
                source_updated_at=get_epoch_timestamp_in_ms(),
            )
            list_records_new.append(RecordUpdate(
                record=filerecord, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=full_attachment_url,
            ))
        return list_records_new

    async def make_child_records_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[ChildRecord], list[RecordUpdate]]:
        """Build ChildRecord list and remaining file RecordUpdates from markdown body."""
        c = self.c
        attachments, _ = await self.parse_gitlab_uploads(markdown_raw)
        child_records: list[ChildRecord] = []
        remaining_attachments: list[RecordUpdate] = []
        project_id = record.external_record_group_id.split("-")[0]
        base_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            full_attachment_url = f"{base_url}{attach.href}"
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=full_attachment_url
                )
            if existing_record:
                child_records.append(ChildRecord(
                    child_id=existing_record.id, child_type=ChildType.RECORD,
                    child_name=existing_record.record_name,
                ))
            else:
                remaining = await self.make_file_records_from_list([attach], record)
                remaining_attachments.extend(remaining)
                if remaining:
                    child_records.append(ChildRecord(
                        child_id=remaining[0].record.id, child_type=ChildType.RECORD,
                        child_name=remaining[0].record.record_name,
                    ))
        return child_records, remaining_attachments

    async def make_block_comment_of_attachments(
        self, markdown_raw: str, record: Record
    ) -> tuple[list[CommentAttachment], list[RecordUpdate]]:
        """Build CommentAttachment list and remaining file RecordUpdates for MR review comments."""
        c = self.c
        attachments, _ = await self.parse_gitlab_uploads(markdown_raw)
        comment_attachments: list[CommentAttachment] = []
        remaining_attachments: list[RecordUpdate] = []
        project_id = record.external_record_group_id.split("-")[0]
        base_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"
        for attach in attachments:
            if attach.category == GitlabLiterals.IMAGE.value:
                continue
            full_attachment_url = f"{base_url}{attach.href}"
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=full_attachment_url
                )
            if existing_record:
                comment_attachments.append(CommentAttachment(name=existing_record.record_name, id=existing_record.id))
            else:
                remaining = await self.make_file_records_from_list([attach], record)
                remaining_attachments.extend(remaining)
                if remaining:
                    comment_attachments.append(CommentAttachment(
                        name=remaining[0].record.record_name, id=remaining[0].record.id,
                    ))
        return comment_attachments, remaining_attachments

    # ------------------------------------------------------------------
    # Note attachment extraction
    # ------------------------------------------------------------------

    async def make_files_records_from_notes(self, issue: Any, record: Record) -> list[RecordUpdate]:
        """Extract file records from the notes (comments) of a GitLab issue."""
        c = self.c
        notes_res = await c.runtime.ds_call(
            c.data_source.list_issue_notes,
            project_id=int(issue.project_id), issue_iid=issue.iid, get_all=True,
        )
        if not notes_res.success:
            raise Exception(f"Failed to fetch notes for issue {issue.title}: {notes_res.error}")
        if not notes_res.data:
            return []
        record_updates_batch: list[RecordUpdate] = []
        for note in notes_res.data:
            note_content = getattr(note, "body", "") or ""
            attachments, _ = await self.parse_gitlab_uploads(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(attachments=attachments, record=record)
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
        return record_updates_batch

    async def make_files_records_from_notes_mr(self, mr: Any, record: Record) -> list[RecordUpdate]:
        """Extract file records from the notes (comments) of a GitLab merge request."""
        c = self.c
        notes_res = await c.runtime.ds_call(
            c.data_source.list_merge_request_notes,
            project_id=int(mr.project_id), mr_iid=mr.iid, get_all=True,
        )
        if not notes_res.success:
            raise Exception(f"Failed to fetch notes for merge request {mr.title}: {notes_res.error}")
        if not notes_res.data:
            return []
        record_updates_batch: list[RecordUpdate] = []
        for note in notes_res.data:
            note_content = getattr(note, "body", "") or ""
            attachments, _ = await self.parse_gitlab_uploads(note_content)
            if attachments:
                file_record_updates = await self.make_file_records_from_list(attachments=attachments, record=record)
                if file_record_updates:
                    record_updates_batch.extend(file_record_updates)
        return record_updates_batch

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def fetch_attachment_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """Stream raw attachment bytes from GitLab."""
        c = self.c
        try:
            attachment_id = record.external_record_id
            if not attachment_id:
                raise Exception(f"No attachment ID available for record {record.id}")
            record_url = record.weburl
            if not record_url:
                raise ValueError(f"No record URL available for record {record.id}")
            async for chunk in c.data_source.get_attachment_files_content(record_url):
                yield chunk
        except Exception as e:
            raise Exception(f"Error fetching attachment content for record {record.id}: {e}") from e
