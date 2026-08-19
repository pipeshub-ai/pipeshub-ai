"""Batched virtual-record → document mapping resolution.

The batch must be indistinguishable from calling the per-id path once per id,
including its per-id fallback for ids the batch does not return.

Mapping nodes are keyed by the virtual record id -- that is the field the
per-id path matches and the one that carries the index.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.transformers.blob_storage import BlobStorage

COLLECTION = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value


def _make_blob_storage(graph_provider=None) -> BlobStorage:
    return BlobStorage(
        logger=MagicMock(),
        config_service=MagicMock(),
        graph_provider=graph_provider if graph_provider is not None else MagicMock(),
    )


def _node(vrid: str, doc_id: str, size: int = 100, metadata_id: str | None = None) -> dict:
    node = {"id": vrid, "record_doc_id": doc_id, "fileSizeBytes": size}
    if metadata_id:
        node["record_metadata_doc_id"] = metadata_id
    return node


class TestBatchLookup:
    @pytest.mark.asyncio
    async def test_resolves_all_ids_in_one_query(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(
            return_value=[_node("vr-1", "doc-1"), _node("vr-2", "doc-2", 200)]
        )
        graph.get_document = AsyncMock()
        blob = _make_blob_storage(graph)

        out = await blob.get_document_ids_by_virtual_record_ids(["vr-1", "vr-2"])

        assert out == {
            "vr-1": {"record_doc_id": "doc-1", "fileSizeBytes": 100},
            "vr-2": {"record_doc_id": "doc-2", "fileSizeBytes": 200},
        }
        graph.get_nodes_by_field_in.assert_awaited_once_with(COLLECTION, "id", ["vr-1", "vr-2"])
        graph.get_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_shape_matches_the_per_id_path(self) -> None:
        """Batch and single-id resolution must return identical dicts."""
        node = _node("vr-1", "doc-1", 42, metadata_id="meta-1")

        graph_batch = MagicMock()
        graph_batch.get_nodes_by_field_in = AsyncMock(return_value=[node])
        graph_batch.get_document = AsyncMock()

        graph_single = MagicMock()
        graph_single.get_nodes_by_filters = AsyncMock(return_value=[node])
        graph_single.get_document = AsyncMock()

        batched = await _make_blob_storage(graph_batch).get_document_ids_by_virtual_record_ids(["vr-1"])
        single = await _make_blob_storage(graph_single).get_document_id_by_virtual_record_id("vr-1")

        assert batched["vr-1"] == single
        assert single["record_metadata_doc_id"] == "meta-1"

    @pytest.mark.asyncio
    async def test_documentid_alias_is_honoured(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(
            return_value=[{"id": "vr-1", "documentId": "legacy-doc", "fileSizeBytes": 7}]
        )
        graph.get_document = AsyncMock()

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1"])

        assert out["vr-1"]["record_doc_id"] == "legacy-doc"

    @pytest.mark.asyncio
    async def test_ids_missing_from_batch_fall_back_per_id(self) -> None:
        """An id with no mapping row at all still costs its per-id query."""
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[_node("vr-1", "doc-1")])
        graph.get_document = AsyncMock(return_value={"record_doc_id": "doc-2", "fileSizeBytes": 55})

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1", "vr-2"])

        assert out["vr-1"]["record_doc_id"] == "doc-1"
        assert out["vr-2"]["record_doc_id"] == "doc-2"
        graph.get_document.assert_awaited_once_with("vr-2", COLLECTION)

    @pytest.mark.asyncio
    async def test_unresolvable_ids_are_absent_not_empty(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[])
        graph.get_document = AsyncMock(return_value=None)

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-missing"])

        assert out == {}, "callers must be able to tell 'no mapping' from 'not looked up'"

    @pytest.mark.asyncio
    async def test_batch_query_failure_degrades_to_per_id(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("bolt down"))
        graph.get_document = AsyncMock(return_value={"record_doc_id": "doc-1", "fileSizeBytes": 1})

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1"])

        assert out["vr-1"]["record_doc_id"] == "doc-1"

    @pytest.mark.asyncio
    async def test_one_failed_fallback_does_not_sink_the_batch(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[])
        graph.get_document = AsyncMock(
            side_effect=[RuntimeError("boom"), {"record_doc_id": "doc-2", "fileSizeBytes": 2}]
        )

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1", "vr-2"])

        assert "vr-1" not in out
        assert out["vr-2"]["record_doc_id"] == "doc-2"

    @pytest.mark.asyncio
    async def test_duplicates_and_blanks_are_collapsed(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[_node("vr-1", "doc-1")])
        graph.get_document = AsyncMock()

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(
            ["vr-1", "vr-1", "", None]
        )

        assert out == {"vr-1": {"record_doc_id": "doc-1", "fileSizeBytes": 100}}
        graph.get_nodes_by_field_in.assert_awaited_once_with(COLLECTION, "id", ["vr-1"])

    @pytest.mark.asyncio
    async def test_empty_input_makes_no_queries(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock()
        graph.get_document = AsyncMock()

        assert await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids([]) == {}
        graph.get_nodes_by_field_in.assert_not_called()
        graph.get_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_ids_are_chunked(self) -> None:
        chunk = BlobStorage.VIRTUAL_RECORD_LOOKUP_CHUNK_SIZE
        vrids = [f"vr-{i}" for i in range(chunk + 10)]

        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(
            side_effect=lambda _c, _f, values: [_node(v, f"doc-{v}") for v in values]
        )
        graph.get_document = AsyncMock()

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(vrids)

        assert len(out) == len(vrids)
        assert graph.get_nodes_by_field_in.await_count == 2
        assert len(graph.get_nodes_by_field_in.await_args_list[0].args[2]) == chunk
        assert len(graph.get_nodes_by_field_in.await_args_list[1].args[2]) == 10
        graph.get_document.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_graph_provider_raises(self) -> None:
        blob = _make_blob_storage(graph_provider=None)
        blob.graph_provider = None
        with pytest.raises(Exception, match="GraphProvider not initialized"):
            await blob.get_document_ids_by_virtual_record_ids(["vr-1"])


class TestBatchKeyField:
    """The batch matched on a ``virtualRecordId`` property that mapping nodes do
    not carry, so it returned nothing and every id fell through to the per-id
    path -- with an unindexed scan added on top. These pin the key field."""

    @pytest.mark.asyncio
    async def test_batches_on_the_indexed_key_not_a_property(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[])
        graph.get_document = AsyncMock(return_value=None)

        await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1"])

        field = graph.get_nodes_by_field_in.await_args_list[0].args[1]
        assert field == "id", "mapping nodes are keyed by virtual record id"

    @pytest.mark.asyncio
    async def test_nodes_shaped_like_production_resolve_without_any_fallback(self) -> None:
        """Real rows carry id/documentId/record_metadata_doc_id and no
        virtualRecordId; every one of these used to miss the batch."""
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[
            {"id": "vr-1", "documentId": "d1", "fileSizeBytes": 10,
             "record_metadata_doc_id": "m1"},
            {"id": "vr-2", "documentId": "d2", "fileSizeBytes": 20,
             "record_metadata_doc_id": "m2"},
        ])
        graph.get_document = AsyncMock()

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(
            ["vr-1", "vr-2"]
        )

        graph.get_document.assert_not_called()
        assert out["vr-1"] == {
            "record_doc_id": "d1", "fileSizeBytes": 10, "record_metadata_doc_id": "m1",
        }
        assert out["vr-2"]["record_doc_id"] == "d2"

    @pytest.mark.asyncio
    async def test_field_lookup_is_off_by_default(self, monkeypatch) -> None:
        """The field is unindexed and nothing writes it, so by default a missed
        id must go straight to the per-id path -- not pay a full label scan."""
        monkeypatch.delenv("PIPESHUB_VRID_FIELD_LOOKUP", raising=False)
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[])
        graph.get_document = AsyncMock(return_value={"documentId": "d1", "fileSizeBytes": 3})

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1"])

        fields = [c.args[1] for c in graph.get_nodes_by_field_in.await_args_list]
        assert fields == ["id"], "only the indexed key should be queried"
        graph.get_document.assert_awaited_once_with("vr-1", COLLECTION)
        assert out["vr-1"]["record_doc_id"] == "d1"

    @pytest.mark.asyncio
    async def test_field_lookup_runs_when_explicitly_enabled(self, monkeypatch) -> None:
        """Deployments that dual-write the field can opt back in."""
        monkeypatch.setenv("PIPESHUB_VRID_FIELD_LOOKUP", "true")
        graph = MagicMock()

        async def _batch(collection, field, values):
            if field == "id":
                return []
            return [{"virtualRecordId": "vr-1", "documentId": "d1", "fileSizeBytes": 3}]

        graph.get_nodes_by_field_in = AsyncMock(side_effect=_batch)
        graph.get_document = AsyncMock()

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-1"])

        assert [c.args[1] for c in graph.get_nodes_by_field_in.await_args_list] == [
            "id", "virtualRecordId",
        ]
        graph.get_document.assert_not_called()
        assert out["vr-1"]["record_doc_id"] == "d1"

    @pytest.mark.asyncio
    async def test_neither_shape_matching_still_falls_back_per_id(self) -> None:
        graph = MagicMock()
        graph.get_nodes_by_field_in = AsyncMock(return_value=[])
        graph.get_document = AsyncMock(return_value={"documentId": "d9", "fileSizeBytes": 1})

        out = await _make_blob_storage(graph).get_document_ids_by_virtual_record_ids(["vr-9"])

        assert out["vr-9"]["record_doc_id"] == "d9"
        graph.get_document.assert_awaited_once_with("vr-9", COLLECTION)
