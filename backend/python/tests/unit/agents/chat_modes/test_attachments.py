"""Unit tests for `app.agents.chat_modes.attachments.resolve_attachments`."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.agents.chat_modes.attachments import resolve_attachments
from app.utils.chat_helpers import CitationRefMapper

LOGGER = logging.getLogger("test")


def _granting_graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id.return_value = ["rec-1"]
    graph.check_record_access_with_details.return_value = {"id": "rec-1"}
    return graph


def _access() -> dict:
    return {"user_id": "user-1", "graph_provider": _granting_graph()}


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
            **_access(),
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
            **_access(),
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
            **_access(),
        )

        assert result.context_text == ""


class TestImageAttachments:
    async def test_image_attachment_notes_filename_and_merges_vrid(self) -> None:
        attachments = [{"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"}]

        result = await resolve_attachments(
            attachments, blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
            **_access(),
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
            **_access(),
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


class TestAttachmentAccess:
    async def test_denied_document_is_not_loaded_or_scoped(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.return_value = {"content": "secret"}
        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.return_value = ["rec-secret"]
        graph.check_record_access_with_details.return_value = None

        def _fake_record_to_message_content(record, ref_mapper=None):
            return [{"type": "text", "text": record["content"]}], ref_mapper

        monkeypatch.setattr(
            "app.agents.chat_modes.attachments.record_to_message_content",
            _fake_record_to_message_content,
        )

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-secret", "mimeType": "application/pdf", "fileName": "secret.pdf"}],
            blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
            user_id="user-1", graph_provider=graph,
        )

        blob_store.get_record_from_storage.assert_not_called()
        assert result.context_text == ""
        assert result.virtual_record_ids == []

    async def test_missing_user_never_loads_the_record(self) -> None:
        blob_store = AsyncMock()
        graph = _granting_graph()

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-1", "mimeType": "text/plain"}],
            blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
            graph_provider=graph,
        )

        blob_store.get_record_from_storage.assert_not_called()
        graph.check_record_access_with_details.assert_not_called()
        assert result.virtual_record_ids == []
        assert result.context_text == ""

    async def test_denied_image_is_left_out_of_the_retrieval_scope(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.return_value = ["rec-img"]
        graph.check_record_access_with_details.return_value = None

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"}],
            blob_store=AsyncMock(), org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
            user_id="user-1", graph_provider=graph,
        )

        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False
        assert result.context_text == ""

    async def test_allowed_document_is_kept_when_another_is_denied(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        blob_store.get_record_from_storage.return_value = {"content": "visible"}

        async def _lookup(virtual_record_id, raise_on_error=False):
            return ["rec-ok"] if virtual_record_id == "vrid-ok" else ["rec-no"]

        async def _check(_user_id, _org_id, record_id):
            return {"id": record_id} if record_id == "rec-ok" else None

        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.side_effect = _lookup
        graph.check_record_access_with_details.side_effect = _check

        def _fake_record_to_message_content(record, ref_mapper=None):
            return [{"type": "text", "text": record["content"]}], ref_mapper

        monkeypatch.setattr(
            "app.agents.chat_modes.attachments.record_to_message_content",
            _fake_record_to_message_content,
        )

        result = await resolve_attachments(
            [
                {"virtualRecordId": "vrid-no", "mimeType": "text/plain", "fileName": "hidden.txt"},
                {"virtualRecordId": "vrid-ok", "mimeType": "text/plain", "fileName": "notes.txt"},
            ],
            blob_store=blob_store, org_id="org-1", ref_mapper=CitationRefMapper(), logger=LOGGER,
            user_id="user-1", graph_provider=graph,
        )

        blob_store.get_record_from_storage.assert_awaited_once_with("vrid-ok", "org-1")
        assert result.virtual_record_ids == ["vrid-ok"]
        assert "visible" in result.context_text
        assert "hidden" not in result.context_text
