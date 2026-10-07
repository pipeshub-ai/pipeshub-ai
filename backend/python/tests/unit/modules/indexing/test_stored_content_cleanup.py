"""Blob and MongoDB cleanup for deleted records: the separable storage step.

Pins what makes the step safe to run and to retry:

* content a record still uses (shared virtual record id) is never purged;
* the mapping row, the durable "still to clean" marker, goes only after storage
  confirms the purge, so a failure leaves it for the orphan sweeper;
* the pipeline reports what is still pending instead of failing the vector step.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes, OriginTypes
from app.connectors.services.vector_cleanup_events import (
    MAX_VIRTUAL_RECORD_IDS_PER_EVENT,
    build_stored_document_cleanup_events,
    log_cleanup_publish_failure,
)
from app.modules.indexing.stored_content_cleanup import StoredContentCleanup
from app.modules.transformers import blob_storage as blob_module
from app.modules.transformers.blob_storage import BlobStorage, TransientStorageError
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)
from app.services.graph_db.common.utils import uploaded_document_id

MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
DOC_ID = "65f1c0ffee0123456789abcd"


def _cleanup(*, records_by_vrid=None, mapping_org="org-1", purge=None):
    graph = AsyncMock()
    records_by_vrid = records_by_vrid or {}
    # The store filters by visibility in its query; LIVE by default.
    graph.get_records_by_virtual_record_id = AsyncMock(
        side_effect=lambda virtual_record_id, raise_on_error, visibility=RecordVisibility.LIVE: [
            r for r in records_by_vrid.get(virtual_record_id, []) if matches_visibility(r, visibility)
        ]
    )
    graph.get_document = AsyncMock(
        side_effect=lambda key, collection, raise_on_error: {"orgId": mapping_org} if mapping_org else None
    )
    blob = AsyncMock()
    blob.purge_virtual_record_documents = purge or AsyncMock(return_value=1)
    blob.purge_document = AsyncMock(return_value=1)
    return StoredContentCleanup(MagicMock(), graph, blob), graph, blob


class TestReleaseVirtualRecords:
    @pytest.mark.asyncio
    async def test_purges_the_envelope_then_drops_the_mapping_row(self):
        cleanup, graph, blob = _cleanup()

        failed = await cleanup.release_virtual_records(["vr-1", "vr-1", ""], org_id="org-1")

        assert failed == []
        blob.purge_virtual_record_documents.assert_awaited_once_with("org-1", "vr-1")
        graph.delete_nodes.assert_awaited_once_with(keys=["vr-1"], collection=MAPPING)

    @pytest.mark.asyncio
    async def test_content_a_record_still_uses_is_left_alone(self):
        cleanup, graph, blob = _cleanup(records_by_vrid={"vr-shared": [{"_key": "copy-2"}]})

        failed = await cleanup.release_virtual_records(["vr-shared", "vr-gone"], org_id="org-1")

        assert failed == []
        blob.purge_virtual_record_documents.assert_awaited_once_with("org-1", "vr-gone")
        graph.delete_nodes.assert_awaited_once_with(keys=["vr-gone"], collection=MAPPING)

    @pytest.mark.asyncio
    async def test_content_a_record_in_the_trash_still_holds_waits_for_its_purge(self) -> None:
        cleanup, graph, blob = _cleanup(
            records_by_vrid={"vr-twin": [{"_key": "twin", "isDeleted": True}]}
        )

        failed = await cleanup.release_virtual_records(["vr-twin"], org_id="org-1")

        assert failed == []
        blob.purge_virtual_record_documents.assert_not_awaited()
        graph.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_purge_keeps_the_mapping_row_for_a_retry(self):
        purge = AsyncMock(side_effect=lambda org, vrid: (_ for _ in ()).throw(
            TransientStorageError("503")) if vrid == "vr-bad" else 1)
        cleanup, graph, _ = _cleanup(purge=purge)

        failed = await cleanup.release_virtual_records(["vr-ok", "vr-bad"], org_id="org-1")

        assert failed == ["vr-bad"]
        graph.delete_nodes.assert_awaited_once_with(keys=["vr-ok"], collection=MAPPING)

    @pytest.mark.asyncio
    async def test_an_unreadable_graph_purges_nothing(self):
        cleanup, graph, blob = _cleanup()
        graph.get_records_by_virtual_record_id = AsyncMock(side_effect=RuntimeError("graph down"))

        failed = await cleanup.release_virtual_records(["vr-1"], org_id="org-1")

        assert failed == ["vr-1"]
        blob.purge_virtual_record_documents.assert_not_awaited()
        graph.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_org_comes_from_the_mapping_row_when_not_given(self):
        cleanup, graph, blob = _cleanup(mapping_org="org-from-row")

        await cleanup.release_virtual_records(["vr-1"])

        graph.get_document.assert_awaited_once_with("vr-1", MAPPING, raise_on_error=True)
        blob.purge_virtual_record_documents.assert_awaited_once_with("org-from-row", "vr-1")

    @pytest.mark.asyncio
    async def test_without_a_mapping_row_there_is_nothing_to_purge(self):
        cleanup, graph, blob = _cleanup(mapping_org=None)

        failed = await cleanup.release_virtual_records(["vr-1"])

        assert failed == []
        blob.purge_virtual_record_documents.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unreadable_mapping_row_is_kept_for_a_retry(self) -> None:
        cleanup, graph, blob = _cleanup()
        graph.get_document = AsyncMock(side_effect=RuntimeError("graph 503"))

        failed = await cleanup.release_virtual_records(["vr-1"])

        assert failed == ["vr-1"]
        blob.purge_virtual_record_documents.assert_not_awaited()
        graph.delete_nodes.assert_not_awaited()


class TestAMappingRowWithoutAnOwner:
    @pytest.mark.asyncio
    async def test_is_kept_for_a_retry_not_released(self):
        """The row is the sweeper's only marker for the envelope."""
        cleanup, graph, blob = _cleanup()
        graph.get_document = AsyncMock(return_value={"id": "vr-1", "documentId": "doc"})

        failed = await cleanup.release_virtual_records(["vr-1"])

        assert failed == ["vr-1"]
        blob.purge_virtual_record_documents.assert_not_awaited()
        graph.delete_nodes.assert_not_awaited()


class TestPurgeDocuments:
    @pytest.mark.asyncio
    async def test_reports_only_the_documents_still_stored(self):
        cleanup, _, blob = _cleanup()
        blob.purge_document = AsyncMock(
            side_effect=lambda org, doc: (_ for _ in ()).throw(RuntimeError("down")) if doc == "d2" else 1
        )

        failed = await cleanup.purge_documents("org-1", ["d1", "d2", "d1"])

        assert failed == ["d2"]
        assert blob.purge_document.await_count == 2


class TestPipelineUsesTheStorageStep:
    def _pipeline(self, stored_content):
        from app.modules.indexing.run import IndexingPipeline

        registry = MagicMock()
        registry.strategy = MagicMock()
        registry.manifest_store = MagicMock()
        return IndexingPipeline(
            logger=MagicMock(),
            config_service=MagicMock(),
            graph_provider=AsyncMock(),
            collection_registry=registry,
            vector_db_service=AsyncMock(),
            stored_content=stored_content,
        )

    @pytest.mark.asyncio
    async def test_forgetting_a_mapping_goes_through_the_storage_step(self):
        stored = AsyncMock()
        stored.release_virtual_records = AsyncMock(return_value=["vr-2"])
        pipeline = self._pipeline(stored)

        pending = await pipeline._forget_virtual_record_mappings(["vr-1", "vr-2"], org_id="org-1")

        assert pending == ["vr-2"]
        stored.release_virtual_records.assert_awaited_once_with(["vr-1", "vr-2"], org_id="org-1")
        pipeline.graph_provider.delete_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_without_the_storage_step_only_the_row_is_dropped(self):
        pipeline = self._pipeline(None)

        assert await pipeline._forget_virtual_record_mappings(["vr-1"]) == []
        pipeline.graph_provider.delete_nodes.assert_awaited_once_with(keys=["vr-1"], collection=MAPPING)

    @pytest.mark.asyncio
    async def test_uploads_cannot_be_purged_without_the_storage_step(self):
        pipeline = self._pipeline(None)

        assert await pipeline.purge_stored_documents("org-1", ["d1"]) == ["d1"]

    @pytest.mark.asyncio
    async def test_bulk_delete_reports_stored_content_still_pending(self):
        stored = AsyncMock()
        stored.release_virtual_records = AsyncMock(return_value=["vr-1"])
        pipeline = self._pipeline(stored)
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        pipeline.collection_locator = MagicMock()
        pipeline.collection_locator.all_collections = AsyncMock(return_value=["records"])

        with patch("app.modules.indexing.run.asyncio.sleep", AsyncMock()):
            result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["success"] is True
        assert result["virtual_record_ids_deleted"] == 1
        assert result["stored_content_pending"] == 1
        stored.release_virtual_records.assert_awaited_once_with(["vr-1"], org_id=None)


def _session_answering(status: int, body: dict):
    calls = []

    class _Response:
        def __init__(self):
            self.status = status

        async def json(self):
            return body

        async def text(self):
            return str(body)

    class _Session:
        def delete(self, url, headers):
            calls.append(url)

            @asynccontextmanager
            async def _cm():
                yield _Response()

            return _cm()

    @asynccontextmanager
    async def borrowed():
        yield _Session()

    return borrowed, calls


class TestBlobStoragePurge:
    def _storage(self):
        storage = BlobStorage(MagicMock(), MagicMock())
        storage._get_auth_and_config = AsyncMock(return_value=({"Authorization": "x"}, "http://node:3000", "local"))
        return storage

    @pytest.mark.asyncio
    async def test_purges_a_virtual_record_path(self):
        borrowed, calls = _session_answering(200, {"purged": 2})
        with patch.object(blob_module, "_borrowed_session", borrowed):
            assert await self._storage().purge_virtual_record_documents("org-1", "vr-1") == 2
        assert calls == ["http://node:3000/api/v1/document/internal/records/vr-1/purge"]

    @pytest.mark.asyncio
    async def test_purges_a_document(self):
        borrowed, calls = _session_answering(200, {"purged": 0})
        with patch.object(blob_module, "_borrowed_session", borrowed):
            assert await self._storage().purge_document("org-1", DOC_ID) == 0
        assert calls == [f"http://node:3000/api/v1/document/internal/{DOC_ID}/purge"]

    @pytest.mark.asyncio
    async def test_a_storage_outage_raises_a_transient_error(self):
        borrowed, _ = _session_answering(503, {"message": "down"})
        with patch.object(blob_module, "_borrowed_session", borrowed), pytest.raises(TransientStorageError):
            await self._storage().purge_document("org-1", DOC_ID)

    @pytest.mark.asyncio
    async def test_a_refused_request_raises(self):
        borrowed, _ = _session_answering(400, {"message": "bad id"})
        with patch.object(blob_module, "_borrowed_session", borrowed), pytest.raises(aiohttp.ClientError):
            await self._storage().purge_document("org-1", "not-an-id")


class TestUploadedDocumentId:
    def test_an_uploaded_file(self):
        record = {"origin": OriginTypes.UPLOAD.value, "externalRecordId": DOC_ID}
        assert uploaded_document_id(record, {"isFile": True}) == DOC_ID
        assert uploaded_document_id(record) == DOC_ID

    @pytest.mark.parametrize(
        "record, type_doc",
        [
            ({"origin": OriginTypes.UPLOAD.value, "externalRecordId": DOC_ID}, {"isFile": False}),
            ({"origin": OriginTypes.CONNECTOR.value, "externalRecordId": DOC_ID}, {"isFile": True}),
            ({"origin": OriginTypes.UPLOAD.value, "externalRecordId": "folder-uuid-1"}, {"isFile": True}),
            ({"origin": OriginTypes.UPLOAD.value}, None),
        ],
        ids=["folder", "connector record", "not a storage id", "no external id"],
    )
    def test_everything_else_has_no_upload(self, record, type_doc):
        assert uploaded_document_id(record, type_doc) is None


class TestStoredDocumentCleanupEvents:
    def test_one_event_per_chunk_of_unique_ids(self):
        ids = [f"{i:024x}" for i in range(MAX_VIRTUAL_RECORD_IDS_PER_EVENT + 1)]
        events = build_stored_document_cleanup_events(org_id="org-1", document_ids=ids + ids[:3], connector_id="kb-1")

        assert [e["eventType"] for e in events] == [EventTypes.DELETE_STORED_DOCUMENTS.value] * 2
        assert sum(len(e["payload"]["documentIds"]) for e in events) == len(ids)
        assert all(e["payload"]["orgId"] == "org-1" and e["payload"]["connectorId"] == "kb-1" for e in events)

    def test_no_ids_no_events(self):
        assert build_stored_document_cleanup_events(org_id="org-1", document_ids=[], connector_id="kb-1") == []

    def test_a_failed_publish_says_files_stay_in_storage(self):
        logger = MagicMock()
        event = build_stored_document_cleanup_events(org_id="org-1", document_ids=[DOC_ID], connector_id="kb-1")[0]

        log_cleanup_publish_failure(logger, event, "KB kb-1", RuntimeError("broker down"))

        assert "1 uploaded file(s) stay in storage" in logger.error.call_args.args[0]
