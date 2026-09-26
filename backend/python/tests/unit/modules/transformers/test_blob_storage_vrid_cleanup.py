"""Tests for BlobStorage.delete_storage_docs_for_vrid — the cleanup path
when VRID reconciliation abandons a virtualRecordId."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.transformers.blob_storage import BlobStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_confirm_delay():
    with patch("app.modules.transformers.blob_storage.EMPTY_CONFIRM_DELAY_SECONDS", 0):
        yield


def _make_blob_storage(graph_provider=None, config_service=None):
    logger = MagicMock()
    config_service = config_service or AsyncMock()
    graph_provider = graph_provider or AsyncMock()
    lookup = getattr(graph_provider, "get_records_by_virtual_record_id", None)
    if (
        isinstance(lookup, AsyncMock)
        and lookup.side_effect is None
        and not isinstance(lookup.return_value, list)
    ):
        graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
    return BlobStorage(
        logger=logger,
        config_service=config_service,
        graph_provider=graph_provider,
    )


def _mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(
        side_effect=[
            {"scopedJwtSecret": "test-secret"},
            {"cm": {"endpoint": "http://localhost:3001"}},
            {"storageType": "local"},
        ]
    )
    return cs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeleteStorageDocsForVrid:

    @pytest.mark.asyncio
    async def test_no_graph_provider_logs_warning(self):
        bs = _make_blob_storage(graph_provider=None)
        bs.graph_provider = None
        await bs.delete_storage_docs_for_vrid("org-1", "vrid-1")
        bs.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_mapping_not_found_is_noop(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        bs = _make_blob_storage(graph_provider=gp)

        await bs.delete_storage_docs_for_vrid("org-1", "vrid-missing")

        gp.get_document.assert_awaited_once()
        gp.remove_nodes_by_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_record_and_metadata_docs(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "record_doc_id": "doc-rec-1",
            "record_metadata_doc_id": "doc-meta-1",
        })
        gp.remove_nodes_by_field = AsyncMock(return_value=1)

        cs = _mock_config_service()
        bs = _make_blob_storage(graph_provider=gp, config_service=cs)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete = MagicMock(return_value=mock_resp)

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=mock_session,
        ):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-abandoned")

        assert mock_session.delete.call_count == 2
        delete_urls = [call.args[0] for call in mock_session.delete.call_args_list]
        assert any("doc-rec-1" in url for url in delete_urls)
        assert any("doc-meta-1" in url for url in delete_urls)

        gp.remove_nodes_by_field.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deletes_record_doc_only_when_no_metadata(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "record_doc_id": "doc-rec-1",
        })
        gp.remove_nodes_by_field = AsyncMock(return_value=1)

        cs = _mock_config_service()
        bs = _make_blob_storage(graph_provider=gp, config_service=cs)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete = MagicMock(return_value=mock_resp)

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=mock_session,
        ):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-no-meta")

        assert mock_session.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_404_response_treated_as_success(self):
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "record_doc_id": "doc-already-gone",
        })
        gp.remove_nodes_by_field = AsyncMock(return_value=1)

        cs = _mock_config_service()
        bs = _make_blob_storage(graph_provider=gp, config_service=cs)

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete = MagicMock(return_value=mock_resp)

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=mock_session,
        ):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-gone")

        bs.logger.info.assert_called()
        gp.remove_nodes_by_field.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_http_error_preserves_mapping_and_raises(self):
        """When HTTP delete fails, the mapping node must be preserved so a
        retry can still find the orphaned storage docs."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "record_doc_id": "doc-rec-1",
        })
        gp.remove_nodes_by_field = AsyncMock(return_value=1)

        cs = _mock_config_service()
        bs = _make_blob_storage(graph_provider=gp, config_service=cs)

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.delete = MagicMock(return_value=mock_resp)

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session",
            return_value=mock_session,
        ):
            with pytest.raises(Exception, match="could not be deleted"):
                await bs.delete_storage_docs_for_vrid("org-1", "vrid-err")

        gp.remove_nodes_by_field.assert_not_awaited()


class TestSharedVridIsNeverDeleted:
    """Deduplicated content: another record (possibly another connector) can
    hold the abandoned VRID and read the same storage documents."""

    @pytest.mark.asyncio
    async def test_vrid_still_used_by_another_record_keeps_everything(self):
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-other-connector"])
        bs = _make_blob_storage(graph_provider=gp)

        with patch("app.modules.transformers.blob_storage.get_shared_session") as session:
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-shared")

        session.assert_not_called()
        gp.get_document.assert_not_awaited()
        gp.remove_nodes_by_field.assert_not_awaited()
        assert gp.get_records_by_virtual_record_id.await_args.kwargs["raise_on_error"] is True

    @pytest.mark.asyncio
    async def test_record_appearing_on_confirming_reread_keeps_everything(self):
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(side_effect=[[], ["rec-late"]])
        bs = _make_blob_storage(graph_provider=gp)

        await bs.delete_storage_docs_for_vrid("org-1", "vrid-racing")

        gp.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_graph_raises_and_deletes_nothing(self):
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(side_effect=RuntimeError("graph down"))
        bs = _make_blob_storage(graph_provider=gp)

        with pytest.raises(RuntimeError):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-unknown")

        gp.get_document.assert_not_awaited()
        gp.remove_nodes_by_field.assert_not_awaited()
