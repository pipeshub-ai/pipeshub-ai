"""The virtual-record mapping is looked up by its key, and the legacy field it
falls back to is indexed on both graph backends, so neither path scans every
mapping node."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value


def _blob_storage(graph: AsyncMock) -> BlobStorage:
    return BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=graph)


class TestLookupOrder:
    @pytest.mark.asyncio
    async def test_the_keyed_node_wins_over_a_legacy_node_for_the_same_vrid(self) -> None:
        """An install that re-indexed after the key change holds both nodes; the
        field match would answer with whichever it reads first."""
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"id": "vr-1", "record_doc_id": "doc-new"})
        graph.get_nodes_by_filters = AsyncMock(
            return_value=[{"id": "vr-1_doc-old", "virtualRecordId": "vr-1", "documentId": "doc-old"}]
        )

        result = await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-1")

        assert result["record_doc_id"] == "doc-new"
        graph.get_nodes_by_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_keyed_metadata_stub_does_not_hide_a_legacy_mapping(self) -> None:
        """save_reconciliation_metadata upserts {_key: vrid, record_metadata_doc_id}
        when the keyed read misses, which it does for a legacy-keyed mapping."""
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"id": "vr-1", "record_metadata_doc_id": "meta"})
        graph.get_nodes_by_filters = AsyncMock(
            return_value=[{"id": "vr-1_doc-old", "virtualRecordId": "vr-1", "documentId": "doc-old"}]
        )

        result = await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-1")

        assert result["record_doc_id"] == "doc-old"

    @pytest.mark.asyncio
    async def test_a_keyed_stub_alone_is_still_returned(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"id": "vr-1", "record_metadata_doc_id": "meta"})
        graph.get_nodes_by_filters = AsyncMock(return_value=[])

        result = await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-1")

        assert result == {"record_doc_id": None, "fileSizeBytes": None, "record_metadata_doc_id": "meta"}

    @pytest.mark.asyncio
    async def test_of_several_legacy_mappings_the_newest_with_a_document_wins(self) -> None:
        """Older releases wrote one node per upload, so a vrid can hold several."""
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        graph.get_nodes_by_filters = AsyncMock(return_value=[
            {"id": "vr-1_a", "virtualRecordId": "vr-1", "documentId": "doc-a", "createdAt": 1},
            {"id": "vr-1_c", "virtualRecordId": "vr-1", "createdAt": 9},
            {"id": "vr-1_b", "virtualRecordId": "vr-1", "documentId": "doc-b", "createdAt": 5},
        ])

        result = await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-1")

        assert result["record_doc_id"] == "doc-b"

    @pytest.mark.asyncio
    async def test_no_mapping_under_either_lookup_is_none(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        graph.get_nodes_by_filters = AsyncMock(return_value=[])

        assert await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-x") is None
        graph.get_document.assert_awaited_once_with("vr-x", MAPPING)
        graph.get_nodes_by_filters.assert_awaited_once_with(MAPPING, {"virtualRecordId": "vr-x"})

    @pytest.mark.asyncio
    async def test_a_failed_keyed_lookup_propagates(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError, match="graph down"):
            await _blob_storage(graph).get_document_id_by_virtual_record_id("vr-1")
        graph.get_nodes_by_filters.assert_not_awaited()


class TestFallbackFieldIsIndexed:
    def test_neo4j(self) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        statements = provider._generate_performance_indexes()
        assert (
            "CREATE INDEX virtualrecordtodocidmapping_virtual_record_id IF NOT EXISTS "
            "FOR (n:Virtualrecordtodocidmapping) ON (n.virtualRecordId)"
        ) in statements

    @pytest.mark.asyncio
    async def test_arango(self) -> None:
        provider = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
        provider.http_client = AsyncMock()
        await provider._ensure_indexes()
        fields = [
            tuple(c.args[1]) for c in provider.http_client.ensure_persistent_index.await_args_list
            if c.args[0] == MAPPING
        ]
        assert fields == [("virtualRecordId",)]
