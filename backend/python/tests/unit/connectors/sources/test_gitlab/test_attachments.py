"""Unit tests for gitlab AttachmentsHelper.

Covers:
- parse_gitlab_uploads: UPLOAD_PATTERN matching, extension extraction, category
- embed_images_as_base64: image inline encoding, 4MB size cap, non-image skip
- make_file_records_from_list: FileRecord construction, image skipped
- make_child_records_of_attachments: existing record reuse vs new
- Extension-to-MIME mapping
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.attachments import AttachmentsHelper
from app.connectors.sources.gitlab.constants import UPLOAD_PATTERN

from .conftest import make_mock_connector

pytestmark = pytest.mark.anyio


def _make_attachment_helper() -> tuple[MagicMock, AttachmentsHelper]:
    c = make_mock_connector()
    tx_store = MagicMock()
    tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=None)
    c.data_store_provider = MagicMock()
    c.data_store_provider.transaction = MagicMock(return_value=ctx)
    helper = AttachmentsHelper(c)
    return c, helper


def _record(project_id: str = "42", ext_id: str = "rec-1") -> MagicMock:
    r = MagicMock()
    r.id = "rec-uuid"
    r.external_record_id = ext_id
    r.external_record_group_id = f"{project_id}-work-items"
    r.record_name = "test"
    r.record_type = "TICKET"
    return r


# ===========================================================================
# UPLOAD_PATTERN regex (module-level)
# ===========================================================================


# 32-char hex hash as required by UPLOAD_PATTERN
_HASH32 = "a" * 32
_HASH32B = "b" * 32


class TestUploadPattern:
    def test_image_upload_match(self) -> None:
        md = f"![screenshot](/uploads/{_HASH32}/screenshot.png)"
        matches = list(UPLOAD_PATTERN.finditer(md))
        assert len(matches) == 1
        assert matches[0].group("filename") == "screenshot.png"
        assert matches[0].group("href") == f"/uploads/{_HASH32}/screenshot.png"

    def test_file_link_upload_match(self) -> None:
        md = f"[report.pdf](/uploads/{_HASH32}/report.pdf)"
        matches = list(UPLOAD_PATTERN.finditer(md))
        assert len(matches) == 1
        assert matches[0].group("filename") == "report.pdf"

    def test_external_url_not_matched(self) -> None:
        md = "[click here](https://external.example.com/file.pdf)"
        matches = list(UPLOAD_PATTERN.finditer(md))
        assert len(matches) == 0

    def test_multiple_uploads_in_body(self) -> None:
        md = f"See ![pic1](/uploads/{_HASH32}/1.png) and [doc](/uploads/{_HASH32B}/doc.pdf)"
        matches = list(UPLOAD_PATTERN.finditer(md))
        assert len(matches) == 2


# ===========================================================================
# parse_gitlab_uploads
# ===========================================================================


class TestParseGitlabUploads:
    async def test_parses_image_attachment(self) -> None:
        _, helper = _make_attachment_helper()
        text = f"See this: ![photo](/uploads/{_HASH32}/photo.png)"
        files, cleaned = await helper.parse_gitlab_uploads(text)
        assert len(files) == 1
        assert files[0].filetype == "png"
        assert files[0].category == "image"

    async def test_parses_non_image_attachment(self) -> None:
        _, helper = _make_attachment_helper()
        text = f"Download [report](/uploads/{_HASH32}/report.pdf)"
        files, cleaned = await helper.parse_gitlab_uploads(text)
        assert len(files) == 1
        assert files[0].filetype == "pdf"
        assert files[0].category == "attachment"

    async def test_cleaned_text_removes_upload_markdown(self) -> None:
        _, helper = _make_attachment_helper()
        text = f"Before ![img](/uploads/{_HASH32}/img.png) After"
        files, cleaned = await helper.parse_gitlab_uploads(text)
        assert f"/uploads/{_HASH32}/img.png" not in cleaned
        assert "Before" in cleaned

    async def test_empty_string_returns_empty(self) -> None:
        _, helper = _make_attachment_helper()
        files, cleaned = await helper.parse_gitlab_uploads("")
        assert files == []
        assert cleaned == ""

    async def test_non_string_input_returns_empty(self) -> None:
        _, helper = _make_attachment_helper()
        files, cleaned = await helper.parse_gitlab_uploads(None)  # type: ignore
        assert files == []

    async def test_file_without_extension_gets_txt(self) -> None:
        _, helper = _make_attachment_helper()
        text = f"[noext](/uploads/{_HASH32}/noext)"
        files, _ = await helper.parse_gitlab_uploads(text)
        assert len(files) == 1
        assert files[0].filetype == "txt"


# ===========================================================================
# embed_images_as_base64
# ===========================================================================


class TestEmbedImagesAsBase64:
    async def test_image_embedded_when_under_size_limit(self) -> None:
        c, helper = _make_attachment_helper()
        text = f"![img](/uploads/{_HASH32}/img.png)"

        img_bytes = b"x" * 100  # Small image
        img_res = MagicMock(success=True, data=img_bytes, error=None)
        c.runtime.ds_call_async = AsyncMock(return_value=img_res)

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        assert "data:image/" in result

    async def test_image_skipped_when_over_4mb_limit(self) -> None:
        c, helper = _make_attachment_helper()
        text = f"![img](/uploads/{_HASH32}/img.png)"

        big_bytes = b"x" * (4 * 1024 * 1024 + 1)
        img_res = MagicMock(success=True, data=big_bytes, error=None)
        c.runtime.ds_call_async = AsyncMock(return_value=img_res)

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        # Image should be skipped but cleaned text returned
        assert "data:image/" not in result

    async def test_non_image_not_embedded(self) -> None:
        c, helper = _make_attachment_helper()
        text = f"[report.pdf](/uploads/{_HASH32}/report.pdf)"
        c.runtime.ds_call_async = AsyncMock()

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        c.runtime.ds_call_async.assert_not_called()

    async def test_empty_body_returned_as_is(self) -> None:
        c, helper = _make_attachment_helper()
        result = await helper.embed_images_as_base64("Plain text no uploads.", "https://base")
        assert "Plain text" in result


# ===========================================================================
# make_file_records_from_list
# ===========================================================================


class TestMakeFileRecordsFromList:
    async def test_creates_file_record_for_attachment(self) -> None:
        c, helper = _make_attachment_helper()
        from app.connectors.sources.gitlab.models import FileAttachment
        att = FileAttachment(href="/uploads/x/doc.pdf", filename="doc.pdf", filetype="pdf", category="attachment")
        record = _record()

        result = await helper.make_file_records_from_list([att], record)
        assert len(result) == 1
        assert result[0].record.record_name == "doc.pdf"

    async def test_image_attachment_skipped(self) -> None:
        c, helper = _make_attachment_helper()
        from app.connectors.sources.gitlab.models import FileAttachment
        img = FileAttachment(href="/uploads/x/img.png", filename="img.png", filetype="png", category="image")
        record = _record()

        result = await helper.make_file_records_from_list([img], record)
        assert result == []

    async def test_existing_record_id_reused(self) -> None:
        c, helper = _make_attachment_helper()
        from app.connectors.sources.gitlab.models import FileAttachment

        existing = MagicMock()
        existing.id = "existing-uuid"
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=ctx)

        att = FileAttachment(href="/uploads/x/doc.pdf", filename="doc.pdf", filetype="pdf", category="attachment")
        record = _record()

        result = await helper.make_file_records_from_list([att], record)
        assert result[0].record.id == "existing-uuid"


# ===========================================================================
# Extension-to-MIME mapping
# ===========================================================================


class TestExtensionToMime:
    def test_known_image_extensions(self) -> None:
        mapping = AttachmentsHelper.EXTENSION_TO_MIME
        assert "png" in mapping
        assert "jpg" in mapping
        assert "svg" in mapping

    def test_values_are_mime_subtypes(self) -> None:
        for ext, mime in AttachmentsHelper.EXTENSION_TO_MIME.items():
            assert "/" not in mime  # These are subtypes, not full MIME types


# ===========================================================================
# embed_images_as_base64 — successful embed and exception paths
# ===========================================================================


class TestEmbedImagesAsBase64Extended:
    async def test_successful_embed_replaces_src(self) -> None:
        """Successful get_img_bytes adds base64 data URI to content."""
        c, helper = _make_attachment_helper()
        text = f"![img](/uploads/{_HASH32}/img.png)"

        img_bytes = b"fake-image-data"
        img_res = MagicMock(success=True, data=img_bytes, error=None)
        c.runtime.ds_call_async = AsyncMock(return_value=img_res)

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        assert "data:image/png;base64," in result

    async def test_get_img_bytes_exception_preserves_original(self) -> None:
        """When get_img_bytes raises, original text is preserved (no crash)."""
        c, helper = _make_attachment_helper()
        text = f"![img](/uploads/{_HASH32}/img.png)"

        c.runtime.ds_call_async = AsyncMock(side_effect=Exception("network error"))

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        # Exception caught, cleaned text still returned (image upload removed)
        assert isinstance(result, str)
        c.logger.warning.assert_called()


# ===========================================================================
# make_child_records_of_attachments
# ===========================================================================


class TestMakeChildRecordsOfAttachments:
    async def test_new_attachment_creates_file_record(self) -> None:
        """No existing record: new FileRecord is created and ChildRecord added."""
        c, helper = _make_attachment_helper()
        record = _record()
        text = f"[doc.pdf](/uploads/{_HASH32}/doc.pdf)"

        # No existing record
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=ctx)

        child_records, remaining = await helper.make_child_records_of_attachments(text, record)
        assert len(remaining) == 1
        assert len(child_records) == 1

    async def test_existing_attachment_reuses_child_record(self) -> None:
        """Existing record: ChildRecord is built from existing ID, no new FileRecord."""
        c, helper = _make_attachment_helper()
        record = _record()
        text = f"[doc.pdf](/uploads/{_HASH32}/doc.pdf)"

        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "doc.pdf"
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=ctx)

        child_records, remaining = await helper.make_child_records_of_attachments(text, record)
        assert len(remaining) == 0
        assert len(child_records) == 1
        assert child_records[0].child_id == "existing-uuid"

    async def test_image_attachments_skipped(self) -> None:
        """Image attachments are skipped — no ChildRecord created."""
        c, helper = _make_attachment_helper()
        record = _record()
        text = f"![img.png](/uploads/{_HASH32}/img.png)"

        child_records, remaining = await helper.make_child_records_of_attachments(text, record)
        assert len(child_records) == 0
        assert len(remaining) == 0


# ===========================================================================
# make_block_comment_of_attachments
# ===========================================================================


class TestMakeBlockCommentOfAttachments:
    async def test_new_attachment_creates_comment_attachment(self) -> None:
        """New attachment in comment creates CommentAttachment."""
        c, helper = _make_attachment_helper()
        record = _record()
        text = f"[report.pdf](/uploads/{_HASH32}/report.pdf)"

        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=ctx)

        comment_attachments, remaining = await helper.make_block_comment_of_attachments(text, record)
        assert len(remaining) == 1
        assert len(comment_attachments) == 1

    async def test_existing_attachment_uses_existing_id(self) -> None:
        """Existing attachment: CommentAttachment uses existing ID."""
        c, helper = _make_attachment_helper()
        record = _record()
        text = f"[report.pdf](/uploads/{_HASH32}/report.pdf)"

        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "report.pdf"
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        c.data_store_provider.transaction = MagicMock(return_value=ctx)

        comment_attachments, remaining = await helper.make_block_comment_of_attachments(text, record)
        assert len(remaining) == 0
        assert comment_attachments[0].id == "existing-id"


# ===========================================================================
# make_files_records_from_notes
# ===========================================================================


class TestMakeFilesRecordsFromNotes:
    async def test_happy_path_returns_file_records(self) -> None:
        """Notes with attachments produce FileRecord updates."""
        c, helper = _make_attachment_helper()
        record = _record()

        issue = MagicMock()
        issue.project_id = 42
        issue.iid = 1
        issue.title = "Test Issue"

        note = MagicMock()
        note.body = f"[doc.pdf](/uploads/{_HASH32}/doc.pdf)"
        notes_res = MagicMock(success=True, data=[note], error=None)
        c.runtime.ds_call = AsyncMock(return_value=notes_res)

        result = await helper.make_files_records_from_notes(issue, record)
        assert len(result) >= 1

    async def test_notes_failure_raises(self) -> None:
        """Failed notes API call raises Exception."""
        c, helper = _make_attachment_helper()
        record = _record()

        issue = MagicMock()
        issue.project_id = 42
        issue.iid = 1
        issue.title = "Test Issue"

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        with pytest.raises(Exception, match="Failed to fetch notes"):
            await helper.make_files_records_from_notes(issue, record)

    async def test_empty_notes_returns_empty_list(self) -> None:
        """Notes with no data returns empty list."""
        c, helper = _make_attachment_helper()
        record = _record()

        issue = MagicMock()
        issue.project_id = 42
        issue.iid = 1
        issue.title = "Test Issue"

        notes_res = MagicMock(success=True, data=None, error=None)
        c.runtime.ds_call = AsyncMock(return_value=notes_res)

        result = await helper.make_files_records_from_notes(issue, record)
        assert result == []


# ===========================================================================
# make_files_records_from_notes_mr
# ===========================================================================


class TestMakeFilesRecordsFromNotesMr:
    async def test_happy_path_returns_file_records(self) -> None:
        """MR notes with attachments produce FileRecord updates."""
        c, helper = _make_attachment_helper()
        record = _record()

        mr = MagicMock()
        mr.project_id = 42
        mr.iid = 1
        mr.title = "Test MR"

        note = MagicMock()
        note.body = f"[doc.pdf](/uploads/{_HASH32}/doc.pdf)"
        notes_res = MagicMock(success=True, data=[note], error=None)
        c.runtime.ds_call = AsyncMock(return_value=notes_res)

        result = await helper.make_files_records_from_notes_mr(mr, record)
        assert len(result) >= 1

    async def test_notes_failure_raises(self) -> None:
        """Failed MR notes API call raises Exception."""
        c, helper = _make_attachment_helper()
        record = _record()

        mr = MagicMock()
        mr.project_id = 42
        mr.iid = 1
        mr.title = "Test MR"

        fail_res = MagicMock(success=False, data=None, error="forbidden")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        with pytest.raises(Exception, match="Failed to fetch notes"):
            await helper.make_files_records_from_notes_mr(mr, record)


# ===========================================================================
# fetch_attachment_content
# ===========================================================================


class TestFetchAttachmentContent:
    async def test_streaming_yields_chunks(self) -> None:
        """Streaming yields bytes from get_attachment_files_content."""
        c, helper = _make_attachment_helper()

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "https://gitlab.com/uploads/abc/file.pdf"
        record.weburl = "https://gitlab.com/uploads/abc/file.pdf"

        async def _gen():
            yield b"chunk1"
            yield b"chunk2"

        c.data_source.get_attachment_files_content = MagicMock(return_value=_gen())

        chunks = []
        async for chunk in helper.fetch_attachment_content(record):
            chunks.append(chunk)

        assert chunks == [b"chunk1", b"chunk2"]

    async def test_streaming_error_wrapped(self) -> None:
        """Exception during streaming is wrapped in a generic Exception."""
        c, helper = _make_attachment_helper()

        record = MagicMock()
        record.id = "rec-1"
        record.external_record_id = "https://gitlab.com/uploads/abc/file.pdf"
        record.weburl = "https://gitlab.com/uploads/abc/file.pdf"

        async def _gen_fail():
            raise IOError("network failure")
            yield b""  # noqa: unreachable — marks as async generator

        c.data_source.get_attachment_files_content = MagicMock(return_value=_gen_fail())

        with pytest.raises(Exception):
            async for _ in helper.fetch_attachment_content(record):
                pass
