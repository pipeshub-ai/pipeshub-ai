"""Batched lookups on the linked-record enrichment path.

Enriching a search hit's related records used to cost three calls per record: a
blob fetch (which repeated its own virtual-record mapping query), a base graph
doc, and a type-specific graph doc. These guard that batching them is
indistinguishable from the per-record path, including when a batch misses or
fails outright.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.utils import chat_helpers as ch

RECORDS = CollectionNames.RECORDS.value


def _graph_provider(nodes_by_collection: dict | None = None) -> MagicMock:
    """Graph provider whose batch call answers from a collection → nodes map."""
    nodes_by_collection = nodes_by_collection or {}
    provider = MagicMock()

    async def _batch(collection, field, values, **_kw) -> list[dict]:
        return [n for n in nodes_by_collection.get(collection, []) if n["id"] in values]

    provider.get_nodes_by_field_in = AsyncMock(side_effect=_batch)
    provider.get_document = AsyncMock(return_value=None)
    provider.get_virtual_record_ids_for_record_ids = AsyncMock(return_value={})
    return provider


class TestBaseDocBatching:
    @pytest.mark.asyncio
    async def test_one_query_replaces_one_per_record(self) -> None:
        ids = [f"r{i}" for i in range(5)]
        provider = _graph_provider({RECORDS: [{"id": i, "recordName": i} for i in ids]})
        doc_index: dict = {}

        await ch._resolve_target_metadata(
            set(ids), doc_index, provider, set(ids), None, None, "org",
        )

        assert provider.get_nodes_by_field_in.await_count == 1
        assert provider.get_document.await_count == 0
        assert set(doc_index) == set(ids)

    @pytest.mark.asyncio
    async def test_ids_missing_from_the_batch_are_simply_absent(self) -> None:
        """Matches today's behaviour when get_document returned None."""
        provider = _graph_provider({RECORDS: [{"id": "r1", "recordName": "r1"}]})
        doc_index: dict = {}

        await ch._resolve_target_metadata(
            {"r1", "r2"}, doc_index, provider, {"r1", "r2"}, None, None, "org",
        )

        assert "r1" in doc_index
        assert "r2" not in doc_index

    @pytest.mark.asyncio
    async def test_batch_failure_falls_back_to_per_id(self) -> None:
        provider = _graph_provider()
        provider.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("graph down"))
        provider.get_document = AsyncMock(return_value={"id": "r1", "recordName": "fallback"})
        doc_index: dict = {}

        await ch._resolve_target_metadata(
            {"r1"}, doc_index, provider, {"r1"}, None, None, "org",
        )

        assert provider.get_document.await_count == 1
        assert doc_index["r1"]["recordName"] == "fallback"


class TestTypeDocBatching:
    @pytest.mark.asyncio
    async def test_grouped_by_collection_not_per_record(self) -> None:
        doc_index = {
            "t1": {"id": "t1", "recordType": "TICKET"},
            "t2": {"id": "t2", "recordType": "TICKET"},
            "f1": {"id": "f1", "recordType": "FILE"},
        }
        provider = _graph_provider({
            "tickets": [{"id": "t1", "status": "open"}, {"id": "t2", "status": "done"}],
            "files": [{"id": "f1", "extension": "pdf"}],
        })

        resolved = await ch._fetch_type_specific_docs_batched(
            provider, list(doc_index), doc_index,
        )

        # one query per collection, two collections
        assert provider.get_nodes_by_field_in.await_count == 2
        assert resolved["t1"]["status"] == "open"
        assert resolved["f1"]["extension"] == "pdf"

    @pytest.mark.asyncio
    async def test_unmapped_record_types_are_never_queried(self) -> None:
        """CONFLUENCE_PAGE/WEBPAGE/DATASOURCE have no collection_map entry and
        took the `if not collection: return None` branch before batching."""
        doc_index = {
            "c1": {"id": "c1", "recordType": "CONFLUENCE_PAGE"},
            "w1": {"id": "w1", "recordType": "WEBPAGE"},
        }
        provider = _graph_provider()

        resolved = await ch._fetch_type_specific_docs_batched(
            provider, list(doc_index), doc_index,
        )

        assert provider.get_nodes_by_field_in.await_count == 0
        assert resolved == {}

    @pytest.mark.asyncio
    async def test_failing_collection_does_not_lose_the_others(self) -> None:
        doc_index = {
            "t1": {"id": "t1", "recordType": "TICKET"},
            "f1": {"id": "f1", "recordType": "FILE"},
        }
        provider = _graph_provider()

        async def _batch(collection, field, values, **_kw) -> list[dict]:
            if collection == "tickets":
                raise RuntimeError("tickets unavailable")
            return [{"id": "f1", "extension": "pdf"}]

        provider.get_nodes_by_field_in = AsyncMock(side_effect=_batch)

        resolved = await ch._fetch_type_specific_docs_batched(
            provider, list(doc_index), doc_index,
        )

        assert "t1" not in resolved
        assert resolved["f1"]["extension"] == "pdf"


class TestBlobLookupPassthrough:
    @pytest.mark.asyncio
    async def test_prefetched_lookup_is_forwarded_to_the_blob_fetch(self) -> None:
        """Without this the fetch repeats the mapping query the caller batched."""
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(return_value={"summary": "s"})
        doc_index = {"r1": {"id": "r1", "recordType": "FILE", "recordName": "n"}}

        await ch._build_linked_record_context_metadata(
            "r1", _graph_provider(), doc_index, None,
            vrid="v1", blob_store=blob_store, org_id="org",
            lookup_result={"_key": "doc-1"},
        )

        assert blob_store.get_record_from_storage.await_args.kwargs["lookup_result"] == {
            "_key": "doc-1"
        }

    @pytest.mark.asyncio
    async def test_type_doc_when_supplied_skips_the_per_record_query(self) -> None:
        provider = _graph_provider()
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=None)
        doc_index = {"t1": {"id": "t1", "recordType": "TICKET", "recordName": "n"}}

        await ch._build_linked_record_context_metadata(
            "t1", provider, doc_index, None,
            vrid="v1", blob_store=blob_store, org_id="org",
            type_doc={"id": "t1", "status": "open"},
        )

        assert provider.get_document.await_count == 0

    @pytest.mark.asyncio
    async def test_missing_type_doc_still_falls_back_to_the_query(self) -> None:
        provider = _graph_provider()
        provider.get_document = AsyncMock(return_value={"id": "t1", "status": "open"})
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=None)
        doc_index = {"t1": {"id": "t1", "recordType": "TICKET", "recordName": "n"}}

        await ch._build_linked_record_context_metadata(
            "t1", provider, doc_index, None,
            vrid="v1", blob_store=blob_store, org_id="org",
            type_doc=None,
        )

        assert provider.get_document.await_count == 1


class TestMainRecordPathTypeDocs:
    """The main record path resolved type-specific metadata per record too."""

    @pytest.mark.asyncio
    async def test_supplied_type_docs_skip_the_per_record_query(self) -> None:
        provider = _graph_provider()
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(
            return_value={"record_name": "n", "summary": "s"}
        )
        vtr = {"v1": {"id": "t1", "recordType": "TICKET", "recordName": "n"}}

        await ch.get_record(
            "v1", {}, blob_store, "org", vtr, provider, None, None,
            {"t1": {"id": "t1", "status": "open"}},
        )

        assert provider.get_document.await_count == 0

    @pytest.mark.asyncio
    async def test_missing_type_doc_falls_back_to_the_query(self) -> None:
        provider = _graph_provider()
        provider.get_document = AsyncMock(return_value={"id": "t1", "status": "open"})
        blob_store = MagicMock()
        blob_store.get_record_from_storage = AsyncMock(
            return_value={"record_name": "n", "summary": "s"}
        )
        vtr = {"v1": {"id": "t1", "recordType": "TICKET", "recordName": "n"}}

        await ch.get_record("v1", {}, blob_store, "org", vtr, provider, None, None, {})

        assert provider.get_document.await_count == 1


class TestEdgeBatching:
    """Record-relation edges were four queries per hit (2 relation types x 2
    directions). These pin the batched form to the same output."""

    @staticmethod
    def _provider_with_edges(batch_result: dict) -> MagicMock:
        provider = _graph_provider()
        provider.get_record_relations_batch = AsyncMock(return_value=batch_result)
        provider.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        provider.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])
        return provider

    @pytest.mark.asyncio
    async def test_one_query_covers_every_record_and_direction(self) -> None:
        provider = self._provider_with_edges({
            "r1": {
                "parents": [{"record_id": "p1", "relationType": "PARENT_CHILD"}],
                "children": [{"record_id": "c1", "relationType": "ATTACHMENT"}],
            },
            "r2": {"parents": [], "children": []},
        })

        out = await ch._fetch_edges_for_records(provider, ["r1", "r2"])

        assert provider.get_record_relations_batch.await_count == 1
        assert provider.get_parent_record_ids_by_relation_type.await_count == 0
        assert provider.get_child_record_ids_by_relation_type.await_count == 0
        assert out["r2"] == []
        # incoming ATTACHMENT reads as PARENT, outgoing PARENT_CHILD as CHILD
        assert sorted(out["r1"]) == [("c1", "PARENT"), ("p1", "CHILD")]

    @pytest.mark.asyncio
    async def test_direction_drives_the_label_not_the_relation_name(self) -> None:
        """PARENT_CHILD outgoing means the hit points at its child; incoming
        means the hit has a parent. Swapping these mislabels the context."""
        provider = self._provider_with_edges({
            "r1": {
                "parents": [{"record_id": "out", "relationType": "PARENT_CHILD"}],
                "children": [{"record_id": "in", "relationType": "PARENT_CHILD"}],
            },
        })

        out = dict(await ch._fetch_edges_for_records(provider, ["r1"]))["r1"]

        assert dict((rid, label) for rid, label in out) == {"out": "CHILD", "in": "PARENT"}

    @pytest.mark.asyncio
    async def test_unknown_relation_types_are_dropped(self) -> None:
        provider = self._provider_with_edges({
            "r1": {
                "parents": [{"record_id": "p1", "relationType": "SOMETHING_ELSE"}],
                "children": [],
            },
        })

        assert await ch._fetch_edges_for_records(provider, ["r1"]) == {"r1": []}

    @pytest.mark.asyncio
    async def test_no_records_issues_no_query(self) -> None:
        provider = self._provider_with_edges({})
        assert await ch._fetch_edges_for_records(provider, []) == {}
        assert provider.get_record_relations_batch.await_count == 0

    @pytest.mark.asyncio
    async def test_batch_failure_falls_back_per_record_rather_than_dropping_all(
        self,
    ) -> None:
        """A failed batch must not cost the whole turn its linked-record context.

        Returning {} here silently stripped enrichment from every hit; the
        per-record form this replaced lost only the failing pair.
        """
        provider = self._provider_with_edges({})
        provider.get_record_relations_batch = AsyncMock(side_effect=RuntimeError("boom"))
        provider.get_parent_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "p1", "recordName": "Parent One"}]
        )

        edges = await ch._fetch_edges_for_records(provider, ["r1"])

        assert provider.get_parent_record_ids_by_relation_type.await_count > 0
        assert edges.get("r1"), "per-record fallback produced no edges"

    @pytest.mark.asyncio
    async def test_batch_failure_never_raises(self) -> None:
        provider = self._provider_with_edges({})
        provider.get_record_relations_batch = AsyncMock(side_effect=RuntimeError("boom"))
        provider.get_parent_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("also down")
        )
        provider.get_child_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("also down")
        )

        assert await ch._fetch_edges_for_records(provider, ["r1"]) == {"r1": []}
