"""End-to-end for the shipped per_connector_type strategy.

The EE gate covers a strategy nobody can select; this covers one an operator
actually can. Same machinery, different grouping axis — which is the check that
the abstraction is not quietly specialised to per-org.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_locator import VirtualRecordCollectionLocator
from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record,
    sync_vector_membership,
)
from app.services.vector_db.models import CollectionConfig, ScrollResult, VectorCollectionInfo
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from app.services.vector_db.strategy import DeleteContext, QueryContext, RecordContext
from tests.support.vector_db import make_config_service

pytestmark = pytest.mark.asyncio

DRIVE = "drive_records"
SLACK = "slack_records"


class FakeVectorDB:
    def __init__(self) -> None:
        self.live: set[str] = set()
        self.created: list[str] = []
        self.payload_writes: list[str] = []
        self.point_deletes: list[str] = []
        self.searched: list[str] = []

    async def create_collection(self, collection_name, config) -> None:
        self.live.add(collection_name)
        self.created.append(collection_name)

    async def delete_collection(self, collection_name) -> None:
        self.live.discard(collection_name)

    async def get_collection_info(self, name):
        present = name in self.live
        return VectorCollectionInfo(
            name=name, exists=present,
            dense_dimension=1024 if present else None, points_count=0,
        )

    async def collection_exists(self, name) -> bool:
        return name in self.live

    async def create_index(self, collection_name, field_name, field_schema) -> None:
        return None

    async def filter_collection(self, must=None, should=None):
        return {"must": must, "should": should}

    async def set_payload(self, collection_name, payload, filter) -> None:
        self.payload_writes.append(collection_name)

    async def delete_points(self, collection_name, filter) -> None:
        self.point_deletes.append(collection_name)

    async def query_nearest_points(self, collection_name, requests):
        self.searched.append(collection_name)
        return [[] for _ in requests]

    async def scroll(self, collection_name, scroll_filter, limit, offset=None):
        return ScrollResult(points=[], next_offset=None)


def _rec(key, connector_name, connector_id="inst-1"):
    return {
        "_key": key, "orgId": "org-1",
        "connectorId": connector_id, "connectorName": connector_name,
    }


def _graph(by_vrid):
    all_recs = {r["_key"]: r for rs in by_vrid.values() for r in rs}
    gp = AsyncMock()
    gp.get_records_by_virtual_record_id = AsyncMock(
        side_effect=lambda virtual_record_id=None, **kw: [
            r["_key"] for r in by_vrid.get(virtual_record_id, [])
        ]
    )
    gp.get_document = AsyncMock(side_effect=lambda key, _c: all_recs.get(key))
    gp.get_edges_from_node = AsyncMock(return_value=[])
    gp.delete_nodes = AsyncMock()
    return gp


def _registry(vdb):
    return CollectionRegistry(
        vector_db_service=vdb,
        strategy=PerConnectorTypeStrategy(),
        collection_config_factory=lambda s, sp=False: CollectionConfig(embedding_size=s),
        manifest_store=CollectionManifestStore(make_config_service(), MagicMock()),
        logger=MagicMock(),
    )


def _locator(registry):
    return VirtualRecordCollectionLocator(
        strategy=registry.strategy,
        manifest_store=registry.manifest_store,
        logger=MagicMock(),
    )


async def _two_connectors(vdb):
    registry = _registry(vdb)
    await registry.ensure_collection(RecordContext(org_id="org-1", connector_name="DRIVE"), 1024)
    await registry.ensure_collection(RecordContext(org_id="org-1", connector_name="SLACK"), 1024)
    return registry


class TestIndexing:
    async def test_each_connector_type_gets_a_collection(self):
        vdb = FakeVectorDB()
        await _two_connectors(vdb)
        assert vdb.created == [DRIVE, SLACK]

    async def test_a_second_instance_of_a_type_reuses_its_collection(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        name = await registry.ensure_collection(
            RecordContext(org_id="org-1", connector_id="inst-2", connector_name="DRIVE"), 1024
        )
        assert name == DRIVE
        assert vdb.created == [DRIVE, SLACK]


class TestMembership:
    async def test_rewrite_targets_only_the_records_connector_type(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        graph = _graph({"vr-1": [_rec("rec-a", "DRIVE")]})

        await sync_vector_membership(vdb, _locator(registry), graph, "vr-1", MagicMock())

        assert vdb.payload_writes == [DRIVE]

    async def test_a_vrid_deduped_across_two_types_is_rewritten_in_both(self):
        """The dedup matrix indexes the same content into each type's own
        collection, so both copies must stay consistent with the graph."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        graph = _graph({"vr-dup": [_rec("rec-a", "DRIVE"), _rec("rec-b", "SLACK")]})

        await sync_vector_membership(vdb, _locator(registry), graph, "vr-dup", MagicMock())

        assert set(vdb.payload_writes) == {DRIVE, SLACK}


class TestConnectorDeletion:
    async def test_deleting_one_instance_leaves_the_other_types_data(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        graph = _graph({"vr-1": [_rec("rec-b", "SLACK")]})
        from app.modules.indexing.run import IndexingPipeline

        pipeline = IndexingPipeline(
            logger=MagicMock(), config_service=MagicMock(),
            graph_provider=graph, collection_registry=registry, vector_db_service=vdb,
        )

        await pipeline.purge_connector(
            DeleteContext(org_id="org-1", connector_id="inst-1", connector_name="DRIVE"),
            ["vr-1"],
        )

        # vr-1 still has a Slack record, so Slack's collection is rewritten and
        # never purged. Drive's is swept — the VRID no longer belongs there.
        assert vdb.payload_writes == [SLACK]
        assert SLACK not in vdb.point_deletes

    async def test_never_drops_the_shared_type_collection(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        scope = await registry.resolve_delete_scope(
            DeleteContext(
                org_id="org-1", connector_id="inst-1", connector_name="DRIVE",
                is_last_writer_to_collection=True,
            )
        )
        from app.services.vector_db.strategy import DeleteAction

        assert scope.action == DeleteAction.FILTERED_DELETE
        assert scope.collection_names == [DRIVE]

    async def test_orphaned_vrid_is_cleared_from_every_type(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        graph = _graph({})

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(registry), graph, "vr-gone", MagicMock()
        )

        assert outcome == "deleted"
        assert set(vdb.point_deletes) == {DRIVE, SLACK}


class TestSearch:
    async def test_unnarrowed_search_fans_out_to_every_type(self):
        """No connector hint is available today, so the search covers them all
        rather than guessing — correct, at the cost of round trips."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)

        assert set(await registry.resolve_for_query(QueryContext(org_id="org-1"))) == {
            DRIVE, SLACK
        }

    async def test_a_narrowed_search_hits_only_that_type(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)

        resolved = await registry.resolve_for_query(
            QueryContext(org_id="org-1", accessible_connector_names=["SLACK"])
        )

        assert resolved == [SLACK]


class TestRebuild:
    async def test_model_change_rebuilds_every_type_collection(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        vdb.created.clear()

        recreated = await registry.recreate_all_collections(2048)

        assert set(recreated) == {DRIVE, SLACK}


class TestTopKAcrossConnectors:
    """The query path end to end: narrow, fan out, fuse, truncate."""

    async def _service(self, vdb, registry, connector_types):
        from app.modules.retrieval.retrieval_service import RetrievalService
        from app.services.vector_db.models import ScoreSemantics

        svc = RetrievalService.__new__(RetrievalService)
        svc.logger = MagicMock()
        svc.collection_registry = registry
        svc.vector_db_service = vdb
        svc._capabilities = MagicMock(
            supports_sparse_vectors=False,
            supports_server_side_text_search=False,
            score_semantics=ScoreSemantics.RANK_FUSED,
        )
        svc.graph_provider = AsyncMock()
        svc.graph_provider.get_accessible_connector_types = AsyncMock(
            return_value=list(connector_types)
        )
        svc.get_embedding_model_instance = AsyncMock(
            return_value=AsyncMock(aembed_query=AsyncMock(return_value=[0.1]))
        )
        svc._ensure_sparse_embedder = AsyncMock(return_value=None)
        return svc

    @staticmethod
    def _hit(pid, score, block_id):
        p = MagicMock()
        p.id = pid
        p.score = score
        p.payload = {
            "page_content": pid,
            "metadata": {"virtualRecordId": f"vr-{block_id}", "blockId": block_id},
        }
        return p

    async def test_a_users_search_only_touches_their_connector_types(self):
        """A user with only Slack must not pay for a Drive query."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        svc = await self._service(vdb, registry, ["SLACK"])

        collections = await svc._resolve_search_collections("org-1", "user-1")

        assert collections == [SLACK]

    async def test_a_user_with_both_searches_both(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        svc = await self._service(vdb, registry, ["DRIVE", "SLACK"])

        collections = await svc._resolve_search_collections("org-1", "user-1")

        assert set(collections) == {DRIVE, SLACK}

    async def test_top_k_is_fused_by_rank_across_connectors(self):
        """Drive's collection is large and its RRF scores small; Slack's is
        tiny with larger scores. Sorting the numbers would hand the whole top
        of the list to Slack — the fusion must interleave by rank instead."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        svc = await self._service(vdb, registry, ["DRIVE", "SLACK"])

        async def _query(collection_name, requests):
            if collection_name == DRIVE:
                return [[
                    self._hit("drive-1", 0.016, "d1"),
                    self._hit("drive-2", 0.015, "d2"),
                ]]
            return [[
                self._hit("slack-1", 0.98, "s1"),
                self._hit("slack-2", 0.97, "s2"),
            ]]

        vdb.query_nearest_points = AsyncMock(side_effect=_query)

        merged = await svc._fan_out_searches([DRIVE, SLACK], [MagicMock()], limit=10)

        ids = [p.id for p in merged[0]]
        assert set(ids[:2]) == {"drive-1", "slack-1"}
        assert set(ids[2:]) == {"drive-2", "slack-2"}

    async def test_top_k_is_truncated_to_the_requested_limit(self):
        """Each collection is asked for the full limit, so the union is larger
        than the caller asked for."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        svc = await self._service(vdb, registry, ["DRIVE", "SLACK"])

        async def _query(collection_name, requests):
            prefix = "d" if collection_name == DRIVE else "s"
            return [[
                self._hit(f"{prefix}{i}", 0.5 - i / 100, f"{prefix}blk{i}")
                for i in range(5)
            ]]

        vdb.query_nearest_points = AsyncMock(side_effect=_query)

        merged = await svc._fan_out_searches([DRIVE, SLACK], [MagicMock()], limit=3)

        assert len(merged[0]) == 3

    async def test_a_block_deduped_across_connectors_appears_once(self):
        """The dedup matrix indexes shared content into both collections; the
        user must not see the same block twice."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        svc = await self._service(vdb, registry, ["DRIVE", "SLACK"])

        async def _query(collection_name, requests):
            return [[self._hit(f"{collection_name}-copy", 0.5, "shared-block")]]

        vdb.query_nearest_points = AsyncMock(side_effect=_query)

        merged = await svc._fan_out_searches([DRIVE, SLACK], [MagicMock()], limit=10)

        assert len(merged[0]) == 1


class TestConnectorDeleteEndToEnd:
    """Deleting one connector type, with a file deduplicated into another.

    The shape the existing graph-level integration script never covers: it
    gives every record a unique virtualRecordId (`connector_deletion_integration_test.py:495`),
    so the interaction between deduplication and connector deletion has gone
    untested. Everything below is the real IndexingPipeline over the real
    membership machinery.
    """

    async def _pipeline_with(self, vdb, registry, records_by_vrid):
        from app.modules.indexing.run import IndexingPipeline

        graph = _graph(records_by_vrid)
        return IndexingPipeline(
            logger=MagicMock(),
            config_service=MagicMock(),
            graph_provider=graph,
            collection_registry=registry,
            vector_db_service=vdb,
        ), graph

    async def test_a_shared_vrid_is_purged_from_the_deleted_connectors_collection(self):
        """Drive is deleted; Slack still has a record for the same content.

        The VRID survives, so membership is rewritten in Slack's collection —
        and Drive's must be purged, not left holding points for a record that
        no longer exists.
        """
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        # Post-delete graph state: only the Slack record remains.
        pipeline, _ = await self._pipeline_with(
            vdb, registry, {"vr-shared": [_rec("rec-slack", "SLACK", "inst-slack")]}
        )

        result = await pipeline.purge_connector(
            DeleteContext(
                org_id="org-1", connector_id="inst-drive", connector_name="DRIVE"
            ),
            ["vr-shared"],
        )

        assert result["virtual_record_ids_rewritten"] == 1
        assert vdb.payload_writes == [SLACK]
        assert vdb.point_deletes == [DRIVE]

    async def test_a_vrid_unique_to_the_deleted_connector_is_removed_everywhere(self):
        """Nothing references it any more, so no collection may keep it."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        pipeline, graph = await self._pipeline_with(vdb, registry, {})

        result = await pipeline.purge_connector(
            DeleteContext(
                org_id="org-1", connector_id="inst-drive", connector_name="DRIVE"
            ),
            ["vr-drive-only"],
        )

        assert result["virtual_record_ids_deleted"] == 1
        assert set(vdb.point_deletes) == {DRIVE, SLACK}
        graph.delete_nodes.assert_awaited_once()

    async def test_the_surviving_connectors_collection_is_never_purged(self):
        """Whatever else the sweep touches, the collection that still holds a
        record for the content must keep its points."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        pipeline, _ = await self._pipeline_with(
            vdb, registry, {"vr-shared": [_rec("rec-slack", "SLACK", "inst-slack")]}
        )

        await pipeline.purge_connector(
            DeleteContext(
                org_id="org-1", connector_id="inst-drive", connector_name="DRIVE"
            ),
            ["vr-shared"],
        )

        assert vdb.payload_writes == [SLACK]
        assert SLACK not in vdb.point_deletes

    async def test_the_resolved_scope_names_the_connectors_own_collection(self):
        """Reported alongside the result so the difference between "the
        connector's collection" and "where the delete actually ran" is visible
        rather than surprising."""
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        pipeline, _ = await self._pipeline_with(vdb, registry, {})

        result = await pipeline.purge_connector(
            DeleteContext(
                org_id="org-1", connector_id="inst-drive", connector_name="DRIVE"
            ),
            ["vr-drive-only"],
        )

        assert result["scope_collections"] == [DRIVE]

    async def test_deleting_a_connector_with_no_records_is_a_noop(self):
        vdb = FakeVectorDB()
        registry = await _two_connectors(vdb)
        pipeline, _ = await self._pipeline_with(vdb, registry, {})

        result = await pipeline.purge_connector(
            DeleteContext(
                org_id="org-1", connector_id="inst-drive", connector_name="DRIVE"
            ),
            [],
        )

        assert result["action"] == "noop"
        assert vdb.point_deletes == []
