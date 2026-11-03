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

def _purge_session(status: int = 200, body: dict | None = None, text: str = "") -> MagicMock:
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body if body is not None else {"purged": 2})
    resp.text = AsyncMock(return_value=text)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.delete = MagicMock(return_value=resp)
    return session


class TestDeleteStorageDocsForVrid:

    @pytest.mark.asyncio
    async def test_no_graph_provider_logs_warning(self) -> None:
        bs = _make_blob_storage(graph_provider=None)
        bs.graph_provider = None
        await bs.delete_storage_docs_for_vrid("org-1", "vrid-1")
        bs.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_purges_by_virtual_record_then_removes_mapping(self) -> None:
        """One purge by vrid removes the record and metadata documents wherever
        filed and every content revision stored for the vrid."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "record_doc_id": "doc-rec-1",
            "record_metadata_doc_id": "doc-meta-1",
            "currentRev": "0123456789abcdef",
            "manifestDocId": "doc-rev-1",
        })
        gp.remove_nodes_by_field = AsyncMock(return_value=1)
        bs = _make_blob_storage(graph_provider=gp, config_service=_mock_config_service())
        session = _purge_session()

        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-abandoned")

        session.delete.assert_called_once()
        assert session.delete.call_args.args[0] == (
            "http://localhost:3001/api/v1/document/internal/records/vrid-abandoned/purge"
        )
        gp.remove_nodes_by_field.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_mapping_still_purges_and_removes_no_node(self) -> None:
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        bs = _make_blob_storage(graph_provider=gp, config_service=_mock_config_service())
        session = _purge_session(body={"purged": 0})

        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=session):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-missing")

        session.delete.assert_called_once()
        gp.remove_nodes_by_field.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_mapping_removal_raises_so_caller_retries(self) -> None:
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"record_doc_id": "doc-1"})
        gp.remove_nodes_by_field = AsyncMock(side_effect=RuntimeError("graph down"))
        bs = _make_blob_storage(graph_provider=gp, config_service=_mock_config_service())

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session", return_value=_purge_session(),
        ), pytest.raises(RuntimeError, match="graph down"):
            await bs.delete_storage_docs_for_vrid("org-1", "vrid-1")

    @pytest.mark.asyncio
    async def test_failed_purge_preserves_mapping_and_raises(self) -> None:
        """The mapping node must survive a failed purge so a retry can still
        find the orphaned storage docs."""
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"record_doc_id": "doc-rec-1"})
        bs = _make_blob_storage(graph_provider=gp, config_service=_mock_config_service())
        session = _purge_session(status=500, text="Internal Server Error")

        with patch(
            "app.modules.transformers.blob_storage.get_shared_session", return_value=session,
        ), pytest.raises(Exception, match="failed"):
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
