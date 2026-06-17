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
        c.data_source.get_img_bytes = AsyncMock(return_value=img_res)

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        assert "data:image/" in result

    async def test_image_skipped_when_over_4mb_limit(self) -> None:
        c, helper = _make_attachment_helper()
        text = f"![img](/uploads/{_HASH32}/img.png)"

        big_bytes = b"x" * (4 * 1024 * 1024 + 1)
        img_res = MagicMock(success=True, data=big_bytes, error=None)
        c.data_source.get_img_bytes = AsyncMock(return_value=img_res)

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        # Image should be skipped but cleaned text returned
        assert "data:image/" not in result

    async def test_non_image_not_embedded(self) -> None:
        c, helper = _make_attachment_helper()
        text = f"[report.pdf](/uploads/{_HASH32}/report.pdf)"
        c.data_source.get_img_bytes = AsyncMock()

        result = await helper.embed_images_as_base64(text, "https://gitlab.com/api/v4/projects/1")
        c.data_source.get_img_bytes.assert_not_called()

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
