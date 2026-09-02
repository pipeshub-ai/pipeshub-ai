"""Tests for VRID-level connectorIds / recordGroupIds on vector points."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.filters import canonical_filter_key
from app.services.vector_db.membership import (
    remaining_record_keys,
    reset_membership_context,
    resolve_vector_membership,
    rewrite_or_delete_virtual_record,
    set_membership_context,
    sync_vector_membership,
    vector_point_payload,
)
from tests.support.vector_db import (
    make_collection_registry as _make_collection_registry,
)


def _loc(names=("records",)) -> StaticCollectionLocator:
    """The single-collection locator these tests previously expressed as a
    bare collection name."""
    return StaticCollectionLocator(list(names))


from app.services.vector_db.models import VectorPoint
from app.services.vector_db.redis.utils import (
    hash_doc_to_payload,
    join_tag_values,
    split_tag_values,
    vector_point_to_hash_fields,
)


def _graph(
    *,
    keys=None,
    records=None,
    edges=None,
):
    gp = AsyncMock()
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=keys or [])
    recs = records or {}

    async def _get_document(key, collection):
        return recs.get(key)

    gp.get_document = AsyncMock(side_effect=_get_document)
    gp.get_edges_from_node = AsyncMock(return_value=edges if edges is not None else [])
    return gp


class TestCanonicalFilterKey:
    def test_metadata_fields_prefixed(self):
        assert canonical_filter_key("orgId") == "metadata.orgId"
        assert canonical_filter_key("virtualRecordId") == "metadata.virtualRecordId"

    def test_already_prefixed_unchanged(self):
        assert canonical_filter_key("metadata.blockId") == "metadata.blockId"

    def test_membership_fields_top_level(self):
        assert canonical_filter_key(CONNECTOR_IDS_FIELD) == CONNECTOR_IDS_FIELD
        assert canonical_filter_key(RECORD_GROUP_IDS_FIELD) == RECORD_GROUP_IDS_FIELD


class TestRemainingRecordKeys:
    def test_string_keys(self):
        assert remaining_record_keys(["a", "b", "a"]) == ["a", "b"]

    def test_dict_keys(self):
        assert remaining_record_keys([{"_key": "a"}, {"id": "b"}]) == ["a", "b"]

    def test_non_list(self):
        assert remaining_record_keys(AsyncMock()) == []

    def test_skips_empty_and_keyless_dicts(self):
        assert remaining_record_keys(["", None, {}, {"name": "x"}, "ok"]) == ["ok"]


class TestResolveVectorMembership:
    @pytest.mark.asyncio
    async def test_unique_record_connector_and_belongs_to(self):
        gp = _graph(
            keys=["rec-1"],
            records={
                "rec-1": {
                    "connectorId": "conn-1",
                    "recordGroupId": "rg-primary",
                }
            },
            edges=[
                {"_to": f"{CollectionNames.RECORD_GROUPS.value}/rg-primary"},
                {"_to": f"{CollectionNames.RECORD_GROUPS.value}/rg-shared"},
                {"_to": f"{CollectionNames.APPS.value}/conn-1"},
            ],
        )
        connectors, groups = await resolve_vector_membership(gp, "vr-1")
        assert connectors == ["conn-1"]
        assert groups == ["rg-primary", "rg-shared"]

    @pytest.mark.asyncio
    async def test_kb_record_empty_groups(self):
        """A Collection contributes no record group, by decision.

        Its records have no recordGroupId and their belongsTo edge targets
        apps/<kbId>, because the Collection *is* the container and its id is
        already in connectorIds. Empty groups here is the intended state, so
        this asserts the contract rather than tolerating a gap: anything
        filtering by container reads connectorIds for Collections.
        """
        gp = _graph(
            keys=["kb-rec"],
            records={"kb-rec": {"connectorId": "kb-1"}},
            edges=[{"_to": f"{CollectionNames.APPS.value}/kb-1"}],
        )
        connectors, groups = await resolve_vector_membership(gp, "vr-kb")
        assert connectors == ["kb-1"]
        assert groups == []

    @pytest.mark.asyncio
    async def test_union_across_duplicate_instances(self):
        gp = _graph(
            keys=["r1", "r2"],
            records={
                "r1": {"connectorId": "drive-1", "recordGroupId": "space-a"},
                "r2": {"connectorId": "drive-2", "recordGroupId": "space-b"},
            },
            edges=[],
        )

        async def _edges(node_id, collection):
            if node_id.endswith("/r1"):
                return [{"_to": f"{CollectionNames.RECORD_GROUPS.value}/space-a"}]
            return [{"_to": f"{CollectionNames.RECORD_GROUPS.value}/space-b"}]

        gp.get_edges_from_node = AsyncMock(side_effect=_edges)
        connectors, groups = await resolve_vector_membership(gp, "vr-dup")
        assert connectors == ["drive-1", "drive-2"]
        assert groups == ["space-a", "space-b"]

    @pytest.mark.asyncio
    async def test_current_record_fallback_when_graph_empty(self):
        gp = _graph(keys=[])
        connectors, groups = await resolve_vector_membership(
            gp,
            "vr-new",
            current_record={"connectorId": "conn-new", "recordGroupId": "rg-new"},
        )
        assert connectors == ["conn-new"]
        assert groups == ["rg-new"]

    @pytest.mark.asyncio
    async def test_current_record_ignored_when_graph_has_connectors(self):
        gp = _graph(
            keys=["rec-1"],
            records={"rec-1": {"connectorId": "c1"}},
            edges=[],
        )
        connectors, groups = await resolve_vector_membership(
            gp,
            "vr-1",
            current_record={"connectorId": "other", "recordGroupId": "rg-x"},
        )
        assert connectors == ["c1"]
        assert groups == []

    @pytest.mark.asyncio
    async def test_empty_vrid_skips_graph_lookup(self):
        gp = _graph(keys=["should-not-load"])
        connectors, groups = await resolve_vector_membership(
            gp, "", current_record={"connector_id": "c1", "record_group_id": "g1"}
        )
        gp.get_records_by_virtual_record_id.assert_not_awaited()
        assert connectors == ["c1"]
        assert groups == ["g1"]

    @pytest.mark.asyncio
    async def test_snake_case_object_record(self):
        class Rec:
            connector_id = "conn-obj"
            connectorId = None
            record_group_id = "rg-obj"
            recordGroupId = None

        gp = _graph(keys=["rec-1"])
        gp.get_document = AsyncMock(return_value=Rec())
        connectors, groups = await resolve_vector_membership(gp, "vr-1")
        assert connectors == ["conn-obj"]
        assert groups == ["rg-obj"]

    @pytest.mark.asyncio
    async def test_non_dict_edges_ignored(self):
        gp = _graph(
            keys=["rec-1"],
            records={"rec-1": {"connectorId": "c1"}},
            edges=["not-an-edge", None, {"_to": "apps/c1"}],
        )
        connectors, groups = await resolve_vector_membership(gp, "vr-1")
        assert connectors == ["c1"]
        assert groups == []


class TestRedisTagMembership:
    def test_join_never_json_dumps(self):
        assert join_tag_values(["a", "b"]) == "a,b"
        assert "[" not in join_tag_values(["a"])

    def test_round_trip(self):
        assert split_tag_values(join_tag_values(["id-1", "id-2"])) == ["id-1", "id-2"]

    def test_hash_fields_store_comma_joined(self):
        point = VectorPoint(
            id="p1",
            dense_vector=[0.0, 0.0],
            payload={
                "page_content": "hi",
                "metadata": {"orgId": "o1", "virtualRecordId": "vr-1"},
                CONNECTOR_IDS_FIELD: ["c1", "c2"],
                RECORD_GROUP_IDS_FIELD: ["g1"],
            },
        )
        fields = vector_point_to_hash_fields(point, dtype="FLOAT16")
        assert fields[CONNECTOR_IDS_FIELD] == "c1,c2"
        assert fields[RECORD_GROUP_IDS_FIELD] == "g1"
        restored = hash_doc_to_payload(fields)
        assert restored[CONNECTOR_IDS_FIELD] == ["c1", "c2"]
        assert restored[RECORD_GROUP_IDS_FIELD] == ["g1"]


class TestVectorPointPayloadContext:
    def test_defaults_empty(self):
        payload = vector_point_payload({"orgId": "o"}, "text")
        assert payload[CONNECTOR_IDS_FIELD] == []
        assert payload[RECORD_GROUP_IDS_FIELD] == []
        assert payload["page_content"] == "text"

    def test_reads_bound_context(self):
        tokens = set_membership_context(["c1"], ["g1", "g2"])
        try:
            payload = vector_point_payload({"orgId": "o"}, "text")
            assert payload[CONNECTOR_IDS_FIELD] == ["c1"]
            assert payload[RECORD_GROUP_IDS_FIELD] == ["g1", "g2"]
        finally:
            reset_membership_context(tokens)
        cleared = vector_point_payload({"orgId": "o"}, "text")
        assert cleared[CONNECTOR_IDS_FIELD] == []
        assert cleared[RECORD_GROUP_IDS_FIELD] == []


class TestVectorStoreWritesMembership:
    @pytest.mark.asyncio
    async def test_unique_index_writes_both_arrays(self):
        from langchain_core.documents import Document

        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.membership import set_membership_context
        from app.services.vector_db.models import VectorDBCapabilities

        mock_vdb = AsyncMock()
        mock_vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        mock_vdb.get_service_name = MagicMock(return_value="mock")
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=mock_vdb,
        )
        vs.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
        vs.dense_embeddings = MagicMock()
        vs.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
        vs._compute_sparse_embeddings = AsyncMock(return_value=[None])

        tokens = set_membership_context(["conn-1"], ["rg-1"])
        try:
            await vs._embed_and_upsert_documents(
                [Document(page_content="hello", metadata={"virtualRecordId": "vr-1"})],
                "rec-1",
                "records",
            )
        finally:
            from app.services.vector_db.membership import reset_membership_context

            reset_membership_context(tokens)

        points = mock_vdb.upsert_points.await_args.kwargs["points"]
        payload = points[0].payload
        assert payload[CONNECTOR_IDS_FIELD] == ["conn-1"]
        assert payload[RECORD_GROUP_IDS_FIELD] == ["rg-1"]
        assert payload["metadata"]["virtualRecordId"] == "vr-1"


class TestBulkDeleteMembership:
    @pytest.mark.asyncio
    async def test_last_record_deletes_points(self):
        from app.modules.indexing.run import IndexingPipeline

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=AsyncMock(),
        )
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=[]
        )
        pipeline.graph_provider.delete_nodes = AsyncMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(
            return_value=MagicMock()
        )
        pipeline.vector_db_service.delete_points = AsyncMock()
        pipeline.vector_db_service.set_payload = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_awaited_once()
        pipeline.vector_db_service.set_payload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shared_instance_rewrites_arrays(self):
        from app.modules.indexing.run import IndexingPipeline

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=AsyncMock(),
        )
        pipeline.graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=["rec-2"]
        )
        pipeline.graph_provider.get_document = AsyncMock(
            return_value={"connectorId": "conn-2", "recordGroupId": "rg-2"}
        )
        pipeline.graph_provider.get_edges_from_node = AsyncMock(
            return_value=[{"_to": f"{CollectionNames.RECORD_GROUPS.value}/rg-2"}]
        )
        filt = MagicMock()
        pipeline.vector_db_service.filter_collection = AsyncMock(return_value=filt)
        pipeline.vector_db_service.set_payload = AsyncMock()
        pipeline.vector_db_service.delete_points = AsyncMock()

        result = await pipeline.bulk_delete_embeddings(["vr-1"])

        assert result["virtual_record_ids_processed"] == 1
        pipeline.vector_db_service.delete_points.assert_not_awaited()
        payload = pipeline.vector_db_service.set_payload.await_args.args[1]
        assert payload[CONNECTOR_IDS_FIELD] == ["conn-2"]
        assert payload[RECORD_GROUP_IDS_FIELD] == ["rg-2"]


class TestDeletedRecordPayloadIncludesConnectorId:
    @pytest.mark.asyncio
    async def test_arango_payload_has_connector_id(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
        provider.logger = MagicMock()
        result = await provider._create_deleted_record_event_payload(
            {
                "orgId": "o1",
                "_key": "r1",
                "version": 2,
                "virtualRecordId": "v1",
                "connectorId": "conn-9",
            },
            None,
        )
        assert result["connectorId"] == "conn-9"
        assert result["virtualRecordId"] == "v1"

    @pytest.mark.asyncio
    async def test_neo4j_payload_has_connector_id(self):
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        provider = Neo4jProvider.__new__(Neo4jProvider)
        provider.logger = MagicMock()
        result = await provider._create_deleted_record_event_payload(
            {
                "orgId": "o1",
                "id": "r1",
                "version": 2,
                "virtualRecordId": "v1",
                "connectorId": "conn-9",
            },
            None,
        )
        assert result["connectorId"] == "conn-9"
        assert result["virtualRecordId"] == "v1"


class TestProviderMembershipFilters:
    def test_qdrant_match_any_on_top_level_arrays(self):
        from qdrant_client.http.models import MatchAny

        from app.services.vector_db.qdrant.utils import QdrantUtils

        conditions = QdrantUtils.build_conditions(
            {"connectorIds": ["c1", "c2"], "recordGroupIds": ["g1"]}
        )
        by_key = {c.key: c for c in conditions}
        assert isinstance(by_key["connectorIds"].match, MatchAny)
        assert by_key["connectorIds"].match.any == ["c1", "c2"]
        assert isinstance(by_key["recordGroupIds"].match, MatchAny)
        assert by_key["recordGroupIds"].match.any == ["g1"]

    def test_opensearch_terms_on_top_level_arrays(self):
        from app.services.vector_db.models import FieldCondition, FilterExpression
        from app.services.vector_db.opensearch.utils import OpenSearchUtils

        query = OpenSearchUtils.filter_expression_to_bool_query(
            FilterExpression(
                must=[
                    FieldCondition(key="connectorIds", values=["c1", "c2"]),
                    FieldCondition(key="recordGroupIds", values=["g1"]),
                ]
            )
        )
        must = query["bool"]["must"]
        assert {"terms": {"connectorIds": ["c1", "c2"]}} in must
        assert {"terms": {"recordGroupIds": ["g1"]}} in must

    def test_redis_tag_query_on_top_level_arrays(self):
        from app.services.vector_db.models import FieldCondition, FilterExpression
        from app.services.vector_db.redis.utils import filter_expression_to_redis_query

        query = filter_expression_to_redis_query(
            FilterExpression(
                must=[
                    FieldCondition(key="connectorIds", values=["c1", "c2"]),
                    FieldCondition(key="recordGroupIds", values=["g1"]),
                ]
            )
        )
        assert "@connectorIds:{c1|c2}" in query
        assert "@recordGroupIds:{g1}" in query
        assert "metadata_connectorIds" not in query
        assert "metadata_recordGroupIds" not in query


class TestSyncAndRewriteMembership:
    @pytest.mark.asyncio
    async def test_sync_rewrites_both_arrays(self):
        gp = _graph(
            keys=["rec-1"],
            records={"rec-1": {"connectorId": "c1", "recordGroupId": "g1"}},
            edges=[{"_to": f"{CollectionNames.RECORD_GROUPS.value}/g1"}],
        )
        vdb = AsyncMock()
        filt = object()
        vdb.filter_collection = AsyncMock(return_value=filt)

        await sync_vector_membership(vdb, _loc(), gp, "vr-1", MagicMock())

        vdb.set_payload.assert_awaited_once_with(
            "records",
            {CONNECTOR_IDS_FIELD: ["c1"], RECORD_GROUP_IDS_FIELD: ["g1"]},
            filt,
        )

    @pytest.mark.asyncio
    async def test_sync_skips_empty_vrid(self):
        vdb = AsyncMock()
        await sync_vector_membership(vdb, _loc(), _graph(), "", MagicMock())
        vdb.set_payload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rewrite_when_records_remain(self):
        gp = _graph(
            keys=["rec-1"],
            records={"rec-1": {"connectorId": "c1"}},
            edges=[],
        )
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        result = await rewrite_or_delete_virtual_record(
            vdb, _loc(), gp, "vr-1", MagicMock()
        )

        assert result == "rewritten"
        vdb.set_payload.assert_awaited_once()
        vdb.delete_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_when_none_remain(self):
        gp = _graph(keys=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        result = await rewrite_or_delete_virtual_record(
            vdb, _loc(), gp, "vr-1", MagicMock()
        )

        assert result == "deleted"
        gp.delete_nodes.assert_awaited_once()
        vdb.delete_points.assert_awaited_once()
        vdb.set_payload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mapping_delete_failure_still_deletes_points(self):
        gp = _graph(keys=[])
        gp.delete_nodes = AsyncMock(side_effect=RuntimeError("arango down"))
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        result = await rewrite_or_delete_virtual_record(
            vdb, _loc(), gp, "vr-1", MagicMock()
        )

        assert result == "deleted"
        vdb.delete_points.assert_awaited_once()


class TestVectorStoreBindMembership:
    @pytest.mark.asyncio
    async def test_bind_resolves_from_graph(self):
        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        mock_vdb = AsyncMock()
        mock_vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        mock_vdb.get_service_name = MagicMock(return_value="mock")
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=_graph(
                keys=["rec-1"],
                records={"rec-1": {"connectorId": "conn-1", "recordGroupId": "rg-1"}},
                edges=[{"_to": f"{CollectionNames.RECORD_GROUPS.value}/rg-1"}],
            ),
            collection_registry=_make_collection_registry(),
            vector_db_service=mock_vdb,
        )

        tokens = await vs._bind_membership("vr-1")
        try:
            payload = vector_point_payload({"orgId": "o"}, "chunk")
        finally:
            reset_membership_context(tokens)

        assert payload[CONNECTOR_IDS_FIELD] == ["conn-1"]
        assert payload[RECORD_GROUP_IDS_FIELD] == ["rg-1"]

    @pytest.mark.asyncio
    async def test_bind_failure_raises_instead_of_writing_empty(self):
        """A resolve failure must not look like legitimately-empty membership.

        Points written with empty arrays are indistinguishable from a KB record
        that genuinely has no groups, so only a backfill could repair them.
        """
        from app.exceptions.indexing_exceptions import VectorStoreError
        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        mock_vdb = AsyncMock()
        mock_vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        mock_vdb.get_service_name = MagicMock(return_value="mock")
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(side_effect=RuntimeError("graph down"))
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=gp,
            collection_registry=_make_collection_registry(),
            vector_db_service=mock_vdb,
        )

        with pytest.raises(VectorStoreError):
            await vs._bind_membership("vr-1")


class TestPayloadKeywordIndexes:
    def test_indexes_include_membership_fields(self):
        from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES

        fields = [name for name, _ in PAYLOAD_KEYWORD_INDEXES]
        assert CONNECTOR_IDS_FIELD in fields
        assert RECORD_GROUP_IDS_FIELD in fields
        assert "metadata.virtualRecordId" in fields
        assert "metadata.orgId" in fields



class TestMembershipConcurrency:
    """Concurrent writers for one VRID must not let the staler read win."""

    @pytest.mark.asyncio
    async def test_syncs_for_one_vrid_are_serialised(self):
        """read→write must be atomic per VRID.

        Unserialised, both callers read before either writes, so the staler read
        overwrites the fresher one and an instance is dropped for good.
        """
        import asyncio

        from app.services.vector_db.membership import sync_vector_membership

        trace = []
        recs = {"r1": {"connectorId": "c1"}, "r2": {"connectorId": "c2"}}
        state = {"keys": ["r1"]}

        gp = AsyncMock()

        async def _keys(vrid, *a, **k):
            trace.append("read")
            return list(state["keys"])

        async def _doc(key, collection):
            await asyncio.sleep(0.01)  # yield between read and write
            return recs.get(key)

        gp.get_records_by_virtual_record_id = AsyncMock(side_effect=_keys)
        gp.get_document = AsyncMock(side_effect=_doc)
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        async def _set_payload(collection, payload, filt):
            trace.append("write")
            # a second instance appears once the first write has happened
            state["keys"] = ["r1", "r2"]

        vdb.set_payload = AsyncMock(side_effect=_set_payload)

        await asyncio.gather(
            sync_vector_membership(vdb, _loc(), gp, "vr-1", None),
            sync_vector_membership(vdb, _loc(), gp, "vr-1", None),
        )

        assert trace == ["read", "write", "read", "write"], (
            f"read/write interleaved across callers: {trace}"
        )

    @pytest.mark.asyncio
    async def test_different_vrids_are_not_serialised(self):
        """The lock is per VRID; unrelated records must still run concurrently."""
        import asyncio

        from app.services.vector_db.membership import sync_vector_membership

        active = {"n": 0, "max": 0}
        gp = _graph(keys=["r1"], records={"r1": {"connectorId": "c1"}}, edges=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        async def _set_payload(collection, payload, filt):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.01)
            active["n"] -= 1

        vdb.set_payload = AsyncMock(side_effect=_set_payload)

        await asyncio.gather(
            sync_vector_membership(vdb, _loc(), gp, "vr-a", None),
            sync_vector_membership(vdb, _loc(), gp, "vr-b", None),
        )
        assert active["max"] == 2, "different VRIDs should not block each other"

    @pytest.mark.asyncio
    async def test_rewrite_or_delete_does_not_deadlock(self):
        """rewrite path calls the sync body; a reentrant acquire would hang."""
        import asyncio

        gp = _graph(keys=["r1"], records={"r1": {"connectorId": "c1"}}, edges=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        result = await asyncio.wait_for(
            rewrite_or_delete_virtual_record(vdb, _loc(), gp, "vr-1", None),
            timeout=5,
        )
        assert result == "rewritten"


class TestMembershipLockRelease:
    """The lock must never survive the operation that took it."""

    @pytest.mark.asyncio
    async def test_released_when_vector_db_raises(self):
        """A disconnect mid-write must not strand the lock."""
        import asyncio

        from app.services.vector_db.membership import (
            _vrid_lock,
            sync_vector_membership,
        )

        gp = _graph(keys=["r1"], records={"r1": {"connectorId": "c1"}}, edges=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())
        vdb.set_payload = AsyncMock(side_effect=ConnectionError("vector db gone"))

        with pytest.raises(ConnectionError):
            await sync_vector_membership(vdb, _loc(), gp, "vr-err", None)

        assert not _vrid_lock("vr-err").locked()
        # and the VRID is still usable afterwards
        vdb.set_payload = AsyncMock()
        await asyncio.wait_for(
            sync_vector_membership(vdb, _loc(), gp, "vr-err", None), timeout=5
        )

    @pytest.mark.asyncio
    async def test_released_on_cancellation(self):
        """Consumer shutdown cancels in-flight tasks; the lock must come back."""
        import asyncio

        from app.services.vector_db.membership import (
            _vrid_lock,
            sync_vector_membership,
        )

        started = asyncio.Event()
        gp = _graph(keys=["r1"], records={"r1": {"connectorId": "c1"}}, edges=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        async def _hang(*a, **k):
            started.set()
            await asyncio.sleep(3600)

        vdb.set_payload = AsyncMock(side_effect=_hang)

        task = asyncio.create_task(
            sync_vector_membership(vdb, _loc(), gp, "vr-cancel", None)
        )
        await started.wait()
        assert _vrid_lock("vr-cancel").locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not _vrid_lock("vr-cancel").locked()

    @pytest.mark.asyncio
    async def test_hung_write_does_not_block_the_vrid_forever(self):
        """A stuck call expires instead of wedging every later write."""
        import asyncio

        from app.services.vector_db import membership as m

        gp = _graph(keys=["r1"], records={"r1": {"connectorId": "c1"}}, edges=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        async def _hang(*a, **k):
            await asyncio.sleep(3600)

        vdb.set_payload = AsyncMock(side_effect=_hang)

        original = m.MEMBERSHIP_LOCK_TIMEOUT_SECONDS
        m.MEMBERSHIP_LOCK_TIMEOUT_SECONDS = 0.05
        try:
            with pytest.raises(asyncio.TimeoutError):
                await m.sync_vector_membership(vdb, _loc(), gp, "vr-hang", None)
            assert not m._vrid_lock("vr-hang").locked()
        finally:
            m.MEMBERSHIP_LOCK_TIMEOUT_SECONDS = original


class TestDeleteConfirmation:
    """A stale 'no records remain' read must not destroy embeddings."""

    @pytest.mark.asyncio
    async def test_lagging_read_does_not_delete(self):
        """First read is empty (follower lag), second sees the record."""
        from app.services.vector_db import membership as m

        reads = [[], ["r1"]]
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(
            side_effect=lambda *a, **k: reads.pop(0) if reads else ["r1"]
        )
        gp.get_document = AsyncMock(return_value={"connectorId": "c1"})
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        original = m.EMPTY_CONFIRM_DELAY_SECONDS
        m.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            result = await m.rewrite_or_delete_virtual_record(
                vdb, _loc(), gp, "vr-lag", MagicMock()
            )
        finally:
            m.EMPTY_CONFIRM_DELAY_SECONDS = original

        assert result == "rewritten"
        vdb.delete_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_genuinely_empty_still_deletes(self):
        from app.services.vector_db import membership as m

        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())

        original = m.EMPTY_CONFIRM_DELAY_SECONDS
        m.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            result = await m.rewrite_or_delete_virtual_record(
                vdb, _loc(), gp, "vr-gone", None
            )
        finally:
            m.EMPTY_CONFIRM_DELAY_SECONDS = original

        assert result == "deleted"
        vdb.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_points_deleted_before_mapping(self):
        """The mapping is how orphaned points are found; it must outlive them."""
        from app.services.vector_db import membership as m

        order = []
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        gp.delete_nodes = AsyncMock(side_effect=lambda **k: order.append("mapping"))
        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())
        vdb.delete_points = AsyncMock(side_effect=lambda **k: order.append("points"))

        original = m.EMPTY_CONFIRM_DELAY_SECONDS
        m.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            await m.rewrite_or_delete_virtual_record(vdb, _loc(), gp, "vr-x", None)
        finally:
            m.EMPTY_CONFIRM_DELAY_SECONDS = original

        assert order == ["points", "mapping"], order


class TestQuantizationDefaultsAcrossProviders:
    """INT8 by default, never pinned to RAM — and NONE must actually mean none."""

    def test_collection_config_defaults(self):
        from app.services.vector_db.models import CollectionConfig, QuantizationType

        cfg = CollectionConfig()
        assert cfg.quantization == QuantizationType.SCALAR  # INT8
        assert cfg.quantization_always_ram is False
        assert cfg.on_disk_vectors is True
        assert cfg.on_disk_sparse is True
        assert cfg.on_disk_hnsw is True

    def test_qdrant_builds_int8_not_pinned(self):
        from app.services.vector_db.models import CollectionConfig
        from app.services.vector_db.qdrant.qdrant import _build_quantization_config

        q = _build_quantization_config(CollectionConfig())
        assert q.scalar.type.value == "int8"
        assert q.scalar.always_ram is False

    def test_qdrant_none_means_no_quantization(self):
        from app.services.vector_db.models import CollectionConfig, QuantizationType
        from app.services.vector_db.qdrant.qdrant import _build_quantization_config

        assert (
            _build_quantization_config(
                CollectionConfig(quantization=QuantizationType.NONE)
            )
            is None
        )

    def test_redis_only_supports_float_dtypes(self):
        """Redis cannot express INT8 or an off-RAM copy; FLOAT16 is its lever."""
        from app.services.vector_db.redis.config import (
            _DEFAULT_DENSE_DTYPE,
            _VALID_DENSE_DTYPES,
        )

        assert "INT8" not in _VALID_DENSE_DTYPES
        assert _DEFAULT_DENSE_DTYPE == "FLOAT16"

    def test_opensearch_quantizes_by_default(self):
        from app.services.vector_db.opensearch.config import OpenSearchConfig

        # 7-bit Lucene SQ ≈ Qdrant INT8; Lucene mmaps it, so nothing is pinned.
        assert OpenSearchConfig().quantization_bits == 7


class TestLockIsEventLoopSafe:
    """Indexing runs work on both the main loop and the consumer's worker loop."""

    def test_same_vrid_reused_across_loops_does_not_raise(self):
        """A lock shared across loops raises RuntimeError on the second one.

        The strong reference matters: without it the WeakValueDictionary drops
        the lock when the first loop closes and a fresh one is handed out,
        hiding the collision.
        """
        import asyncio

        from app.services.vector_db.membership import _vrid_lock

        held = []

        async def _use():
            lock = _vrid_lock("vr-cross-loop")
            held.append(lock)  # keep the map entry alive across loops
            async with lock:
                pass

        for _ in range(2):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_use())
            finally:
                loop.close()

        # Two loops, two distinct locks — never the same object reused.
        assert held[0] is not held[1]

    def test_lock_still_serialises_within_one_loop(self):
        import asyncio

        from app.services.vector_db.membership import _vrid_lock

        async def _check():
            assert _vrid_lock("vr-same") is _vrid_lock("vr-same")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_check())
        finally:
            loop.close()


class TestResolveFansOut:
    """A deduped VRID must not cost 1 + 2N serial round trips."""

    @pytest.mark.asyncio
    async def test_record_fetches_are_concurrent(self):
        import asyncio

        active = {"n": 0, "max": 0}

        async def _slow(*a, **k):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.01)
            active["n"] -= 1
            return {"connectorId": "c1"}

        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(
            return_value=["r1", "r2", "r3", "r4"]
        )
        gp.get_document = AsyncMock(side_effect=_slow)
        gp.get_edges_from_node = AsyncMock(return_value=[])

        await resolve_vector_membership(gp, "vr-dup")
        assert active["max"] > 1, "record fetches ran serially"

    @pytest.mark.asyncio
    async def test_results_stay_in_key_order(self):
        recs = {
            "r1": {"connectorId": "c1"},
            "r2": {"connectorId": "c2"},
            "r3": {"connectorId": "c3"},
        }

        async def _doc(key, collection):
            return recs[key]

        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["r1", "r2", "r3"])
        gp.get_document = AsyncMock(side_effect=_doc)
        gp.get_edges_from_node = AsyncMock(return_value=[])

        connectors, _ = await resolve_vector_membership(gp, "vr-order")
        assert connectors == ["c1", "c2", "c3"]


class TestStorageReconcileIsOptIn:
    """The rewrite is expensive and unattended-unsafe; it must not run by default."""

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        import os
        from unittest.mock import patch as _patch

        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        vdb = AsyncMock()
        vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        vdb.get_service_name = MagicMock(return_value="mock")
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=vdb,
        )

        with _patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VECTOR_STORAGE_RECONCILE_ENABLED", None)
            await vs._reconcile_storage_layout("records", 1024, False)

        vdb.reconcile_storage_layout.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runs_when_operator_opts_in(self):
        import os
        from unittest.mock import patch as _patch

        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        vdb = AsyncMock()
        vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        vdb.get_service_name = MagicMock(return_value="mock")
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_registry=_make_collection_registry(),
            vector_db_service=vdb,
        )

        with _patch.dict(os.environ, {"VECTOR_STORAGE_RECONCILE_ENABLED": "true"}):
            await vs._reconcile_storage_layout("records", 1024, False)

        vdb.reconcile_storage_layout.assert_awaited_once()

    def test_only_green_is_treated_as_settled(self):
        """grey means optimizations are pending; stacking onto it is the bug."""
        from app.services.vector_db.qdrant.qdrant import _optimizers_idle

        def _info(status):
            return MagicMock(status=status, optimizer_status="ok")

        assert _optimizers_idle(_info("green")) is True
        assert _optimizers_idle(_info("grey")) is False
        assert _optimizers_idle(_info("yellow")) is False
        assert _optimizers_idle(_info("red")) is False


class TestDeleteDuringIndexLeavesNoOrphans:
    """The per-VRID lock does not span embed→upsert, and cannot.

    Embedding takes minutes; holding the lock across it would block every delete
    for that whole time. So the window is closed after the fact instead: the
    post-write reconcile decides from the graph, and deletes points whose record
    disappeared while they were being embedded.
    """

    def _vectorstore(self, graph, vdb):
        from app.modules.transformers.vectorstore import VectorStore
        from app.services.vector_db.models import VectorDBCapabilities

        vdb.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
        vdb.get_service_name = MagicMock(return_value="mock")
        return VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=graph,
            collection_registry=_make_collection_registry(),
            vector_db_service=vdb,
        )

    @pytest.mark.asyncio
    async def test_points_are_removed_when_the_record_vanished_mid_embed(self):
        from app.services.vector_db import membership as m

        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        gp.get_document = AsyncMock(return_value=None)
        gp.get_edges_from_node = AsyncMock(return_value=[])
        gp.delete_nodes = AsyncMock()

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())
        vs = self._vectorstore(gp, vdb)

        original = m.EMPTY_CONFIRM_DELAY_SECONDS
        m.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            # record_id is required: the delete branch is entered only after
            # positively confirming *that* record is gone, never inferred from
            # an empty VRID lookup.
            await vs._resync_membership_after_write("vr-gone", "rec-gone")
        finally:
            m.EMPTY_CONFIRM_DELAY_SECONDS = original

        # Without this the points would survive with no VRID→doc mapping to find
        # them, so only a full collection cleanup could ever remove them.
        vdb.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_live_record_keeps_its_points(self):
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
        gp.get_document = AsyncMock(return_value={"connectorId": "c1"})
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())
        vs = self._vectorstore(gp, vdb)

        await vs._resync_membership_after_write("vr-live", "rec-1")

        vdb.delete_points.assert_not_awaited()
        vdb.set_payload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_delete_without_a_confirmed_missing_record(self):
        """Not knowing is not the same as knowing it is gone.

        Without this, an unrelated empty VRID lookup would let an ordinary index
        delete the points it had just written.
        """
        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        gp.get_document = AsyncMock(side_effect=ConnectionError("graph flaky"))
        gp.get_edges_from_node = AsyncMock(return_value=[])

        vdb = AsyncMock()
        vdb.filter_collection = AsyncMock(return_value=MagicMock())
        vs = self._vectorstore(gp, vdb)

        await vs._resync_membership_after_write("vr-x", "rec-1")

        vdb.delete_points.assert_not_awaited()
