"""Unit tests for `app.agents.chat_modes.attachments.resolve_attachments`."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.agents.chat_modes.attachments import resolve_attachments
from app.utils.chat_helpers import CitationRefMapper

LOGGER = logging.getLogger("test")


class TestNoAttachments:
    async def test_none_returns_empty_result(self) -> None:
        result = await resolve_attachments(
            None, blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )
        assert result.context_text == ""
        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False

    async def test_empty_list_returns_empty_result(self) -> None:
        result = await resolve_attachments(
            [], blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )
        assert result.context_text == ""


class TestDocumentAttachments:
    async def test_pdf_attachment_resolves_into_context_text(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.return_value = {"recordId": "r1", "content": "policy text"}
        ref_mapper = CitationRefMapper()

        def _fake_record_to_message_content(record, ref_mapper=None):
            return [{"type": "text", "text": f"<record>{record['content']}</record>"}], ref_mapper

        monkeypatch.setattr(
            "app.agents.chat_modes.attachments.record_to_message_content",
            _fake_record_to_message_content,
        )

        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/pdf", "fileName": "policy.pdf"}]
        result = await resolve_attachments(
            attachments, blob_store=blob_store, org_id="org-1", ref_mapper=ref_mapper, logger=LOGGER,
        )

        blob_store.get_record_from_storage.assert_awaited_once_with("vrid-1", "org-1")
        assert "policy text" in result.context_text
        assert result.virtual_record_ids == ["vrid-1"]
        assert result.has_image_attachments is False

    async def test_attachment_missing_virtual_record_id_is_skipped(self) -> None:
        blob_store = AsyncMock()
        attachments = [{"mimeType": "application/pdf", "fileName": "no-vrid.pdf"}]

        result = await resolve_attachments(
            attachments, blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        blob_store.get_record_from_storage.assert_not_called()
        assert result.context_text == ""
        assert result.virtual_record_ids == []

    async def test_blob_store_failure_is_tolerated_not_raised(self) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.side_effect = RuntimeError("storage down")
        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/pdf", "fileName": "policy.pdf"}]

        result = await resolve_attachments(
            attachments, blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        # The vrid is still recorded (widens retrieval scope) even though the
        # blob fetch failed -- one bad attachment must not fail the whole turn.
        assert result.virtual_record_ids == ["vrid-1"]
        assert result.context_text == ""

    async def test_missing_record_from_storage_is_skipped(self) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.return_value = None
        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/pdf", "fileName": "policy.pdf"}]

        result = await resolve_attachments(
            attachments, blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        assert result.context_text == ""


class TestImageAttachments:
    async def test_image_attachment_notes_filename_and_merges_vrid(self) -> None:
        attachments = [{"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"}]

        result = await resolve_attachments(
            attachments, blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        assert result.has_image_attachments is True
        assert "chart.png" in result.context_text
        assert result.virtual_record_ids == ["vrid-img"]

    async def test_image_attachment_without_vrid_still_noted_in_text(self) -> None:
        attachments = [{"mimeType": "image/jpeg", "fileName": "photo.jpg"}]

        result = await resolve_attachments(
            attachments, blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        assert result.has_image_attachments is True
        assert result.virtual_record_ids == []
        assert "photo.jpg" in result.context_text


class TestMixedAttachments:
    async def test_doc_and_image_together_merge_scope_and_text(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.return_value = {"recordId": "r1", "content": "doc body"}

        def _fake_record_to_message_content(record, ref_mapper=None):
            return [{"type": "text", "text": record["content"]}], ref_mapper

        monkeypatch.setattr(
            "app.agents.chat_modes.attachments.record_to_message_content",
            _fake_record_to_message_content,
        )

        attachments = [
            {"virtualRecordId": "vrid-doc", "mimeType": "text/plain", "fileName": "notes.txt"},
            {"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"},
        ]
        result = await resolve_attachments(
            attachments, blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        assert set(result.virtual_record_ids) == {"vrid-doc", "vrid-img"}
        assert "doc body" in result.context_text
        assert "chart.png" in result.context_text
        assert result.has_image_attachments is True

    async def test_unknown_mime_type_is_ignored_entirely(self) -> None:
        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/zip", "fileName": "archive.zip"}]

        result = await resolve_attachments(
            attachments, blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
        )

        assert result.context_text == ""
        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False
