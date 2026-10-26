"""Unit tests for `app.agents.chat_modes.attachments.resolve_attachments`.

It only scopes retrieval to the attachments the user may read. Their content
goes on the current request through the factory, within a budget; this path
used to add a second, unbounded copy to the system prompt.
"""

import logging
from unittest.mock import AsyncMock

from app.agents.chat_modes.attachments import resolve_attachments

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
        result = await resolve_attachments(None, org_id="org-1", logger=LOGGER)
        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False

    async def test_empty_list_returns_empty_result(self) -> None:
        result = await resolve_attachments([], org_id="org-1", logger=LOGGER)
        assert result.virtual_record_ids == []


class TestDocumentAttachments:
    async def test_a_readable_document_is_scoped_and_carries_no_text(self) -> None:
        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/pdf", "fileName": "policy.pdf"}]

        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER, **_access())

        assert result.virtual_record_ids == ["vrid-1"]
        assert result.has_image_attachments is False
        assert not hasattr(result, "context_text")

    async def test_attachment_missing_virtual_record_id_is_skipped(self) -> None:
        attachments = [{"mimeType": "application/pdf", "fileName": "no-vrid.pdf"}]

        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER)

        assert result.virtual_record_ids == []


class TestImageAttachments:
    async def test_image_attachment_merges_vrid(self) -> None:
        attachments = [{"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"}]

        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER, **_access())

        assert result.has_image_attachments is True
        assert result.virtual_record_ids == ["vrid-img"]

    async def test_image_attachment_without_vrid_is_still_an_image(self) -> None:
        attachments = [{"mimeType": "image/jpeg", "fileName": "photo.jpg"}]

        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER)

        assert result.has_image_attachments is True
        assert result.virtual_record_ids == []


class TestMixedAttachments:
    async def test_doc_and_image_together_merge_scope(self) -> None:
        attachments = [
            {"virtualRecordId": "vrid-doc", "mimeType": "text/plain", "fileName": "notes.txt"},
            {"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"},
        ]
        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER, **_access())

        assert set(result.virtual_record_ids) == {"vrid-doc", "vrid-img"}
        assert result.has_image_attachments is True

    async def test_unknown_mime_type_is_ignored_entirely(self) -> None:
        attachments = [{"virtualRecordId": "vrid-1", "mimeType": "application/zip", "fileName": "archive.zip"}]

        result = await resolve_attachments(attachments, org_id="org-1", logger=LOGGER)

        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False


class TestAttachmentAccess:
    async def test_denied_document_is_not_scoped(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.return_value = ["rec-secret"]
        graph.check_record_access_with_details.return_value = None

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-secret", "mimeType": "application/pdf", "fileName": "secret.pdf"}],
            org_id="org-1", logger=LOGGER, user_id="user-1", graph_provider=graph,
        )

        assert result.virtual_record_ids == []

    async def test_missing_user_never_checks_or_scopes_the_record(self) -> None:
        graph = _granting_graph()

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-1", "mimeType": "text/plain"}],
            org_id="org-1", logger=LOGGER, graph_provider=graph,
        )

        graph.check_record_access_with_details.assert_not_called()
        assert result.virtual_record_ids == []

    async def test_denied_image_is_left_out_of_the_retrieval_scope(self) -> None:
        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.return_value = ["rec-img"]
        graph.check_record_access_with_details.return_value = None

        result = await resolve_attachments(
            [{"virtualRecordId": "vrid-img", "mimeType": "image/png", "fileName": "chart.png"}],
            org_id="org-1", logger=LOGGER, user_id="user-1", graph_provider=graph,
        )

        assert result.virtual_record_ids == []
        assert result.has_image_attachments is False

    async def test_allowed_document_is_kept_when_another_is_denied(self) -> None:
        async def _lookup(virtual_record_id, raise_on_error=False):
            return ["rec-ok"] if virtual_record_id == "vrid-ok" else ["rec-no"]

        async def _check(_user_id, _org_id, record_id):
            return {"id": record_id} if record_id == "rec-ok" else None

        graph = AsyncMock()
        graph.get_records_by_virtual_record_id.side_effect = _lookup
        graph.check_record_access_with_details.side_effect = _check

        result = await resolve_attachments(
            [
                {"virtualRecordId": "vrid-no", "mimeType": "text/plain", "fileName": "hidden.txt"},
                {"virtualRecordId": "vrid-ok", "mimeType": "text/plain", "fileName": "notes.txt"},
            ],
            org_id="org-1", logger=LOGGER, user_id="user-1", graph_provider=graph,
        )

        assert result.virtual_record_ids == ["vrid-ok"]
