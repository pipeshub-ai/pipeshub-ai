"""The gate on "Enterprise can add multi-tenancy by shipping one class".

Every other test in this area exercises the default `single` strategy, where a
bug that assumes one collection is invisible — the wrong answer and the right
answer are the same string. This module drives the *real* CollectionRegistry,
VirtualRecordCollectionLocator, IndexingPipeline, membership functions, and
RetrievalService under a strategy that names a collection per org, against an
in-memory vector DB, and asserts each operation touched the collections it
should have.

`PerOrgStrategy` lives in the test tree, not in `app/`: nothing in OSS depends
on it. That is the point — if this passes, an EE package can register the same
shape through `CollectionStrategyFactory` and every call site here works
untouched.

Before this work, steps 3 through 6 all resolved `RecordContext(org_id="")` to
get "the" collection, which under a per-org strategy names one that does not
exist. Each was a silent no-op: the write landed nowhere, the delete removed
nothing, and the vectors stayed searchable.
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
from app.services.vector_db.models import (
    CollectionConfig,
    VectorCollectionInfo,
)
from app.services.vector_db.strategy import (
    DeleteContext,
    IncompleteCollectionContext,
    QueryContext,
    RecordContext,
)
from tests.support.vector_db import make_config_service
from tests.unit.services.vector_db.test_strategy_contract import PerOrgStrategy

pytestmark = pytest.mark.asyncio

ORG_A = "acme"
ORG_B = "globex"
COLL_A = "org_acme_records"
COLL_B = "org_globex_records"


class FakeVectorDB:
    """In-memory vector DB that records which collection each call targeted."""

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

    async def get_collection_info(self, name) -> VectorCollectionInfo:
        present = name in self.live
        return VectorCollectionInfo(
            name=name,
            exists=present,
            dense_dimension=1024 if present else None,
            points_count=0,
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
        from app.services.vector_db.models import ScrollResult

        return ScrollResult(points=[], next_offset=None)


def _record(key: str, org: str, connector: str = "conn-1") -> dict:
    return {
        "_key": key,
        "orgId": org,
        "connectorId": connector,
        "connectorName": "KB",
        "recordGroupId": f"rg-{connector}",
    }


def _graph(records_by_vrid: dict) -> AsyncMock:
    all_records = {r["_key"]: r for rs in records_by_vrid.values() for r in rs}
    gp = AsyncMock()
    gp.get_records_by_virtual_record_id = AsyncMock(
        side_effect=lambda virtual_record_id=None, **kw: [
            r["_key"] for r in records_by_vrid.get(virtual_record_id, [])
        ]
    )
    gp.get_document = AsyncMock(side_effect=lambda key, _c: all_records.get(key))
    gp.get_edges_from_node = AsyncMock(return_value=[])
    gp.delete_nodes = AsyncMock()
    return gp


def _registry(vdb: FakeVectorDB) -> CollectionRegistry:
    return CollectionRegistry(
        vector_db_service=vdb,
        strategy=PerOrgStrategy(),
        collection_config_factory=lambda size, sparse=False: CollectionConfig(
            embedding_size=size
        ),
        manifest_store=CollectionManifestStore(make_config_service(), MagicMock()),
        logger=MagicMock(),
    )


def _locator(registry: CollectionRegistry) -> VirtualRecordCollectionLocator:
    return VirtualRecordCollectionLocator(
        strategy=registry.strategy,
        manifest_store=registry.manifest_store,
        logger=MagicMock(),
    )


async def _two_org_registry(vdb: FakeVectorDB) -> CollectionRegistry:
    registry = _registry(vdb)
    await registry.ensure_collection(RecordContext(org_id=ORG_A), 1024)
    await registry.ensure_collection(RecordContext(org_id=ORG_B), 1024)
    return registry


def _pipeline(vdb, registry, graph):
    from app.modules.indexing.run import IndexingPipeline

    return IndexingPipeline(
        logger=MagicMock(),
        config_service=MagicMock(),
        graph_provider=graph,
        collection_registry=registry,
        vector_db_service=vdb,
    )


class TestWritePath:
    async def test_each_org_gets_its_own_collection(self):
        vdb = FakeVectorDB()
        await _two_org_registry(vdb)
        assert vdb.created == [COLL_A, COLL_B]

    async def test_second_record_for_an_org_reuses_its_collection(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        name = await registry.ensure_collection(RecordContext(org_id=ORG_A), 1024)
        assert name == COLL_A
        assert vdb.created == [COLL_A, COLL_B]

    async def test_an_org_less_context_is_refused_not_resolved(self):
        """The failure mode this whole design exists to prevent: a name that
        looks plausible and that every read and delete then misses."""
        registry = _registry(FakeVectorDB())
        with pytest.raises(IncompleteCollectionContext):
            registry.resolve_write_collection(RecordContext(org_id=""))


class TestMembershipIsOrgScoped:
    async def test_membership_rewrite_targets_only_the_records_own_org(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        graph = _graph({"vr-a": [_record("rec-a", ORG_A)]})

        await sync_vector_membership(
            vdb, _locator(registry), graph, "vr-a", MagicMock()
        )

        assert vdb.payload_writes == [COLL_A]

    async def test_a_vrid_shared_by_two_orgs_is_rewritten_in_both(self):
        """Legacy cross-org duplicates exist on upgraded deployments, from when
        dedup was global. Both copies must stay consistent with the graph."""
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        graph = _graph(
            {"vr-shared": [_record("rec-a", ORG_A), _record("rec-b", ORG_B)]}
        )

        await sync_vector_membership(
            vdb, _locator(registry), graph, "vr-shared", MagicMock()
        )

        assert set(vdb.payload_writes) == {COLL_A, COLL_B}


class TestDeleteIsOrgScoped:
    async def test_re_embed_delete_leaves_the_other_org_untouched(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        pipeline = _pipeline(vdb, registry, _graph({}))

        await pipeline.delete_points_for_virtual_record(
            "vr-a", RecordContext(org_id=ORG_A, connector_id="conn-1")
        )

        assert vdb.point_deletes == [COLL_A]

    async def test_a_vrid_alive_in_one_org_is_rewritten_there_and_purged_elsewhere(self):
        """Org B still has a record for this content; org A does not any more.

        B's collection keeps the points and gets fresh membership. A's is
        purged — under a per-org strategy that is also the isolation property:
        a tenant's collection must not keep vectors for content that tenant has
        no record of.
        """
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        graph = _graph({"vr-1": [_record("rec-b", ORG_B)]})

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(registry), graph, "vr-1", MagicMock()
        )

        assert outcome == "rewritten"
        assert vdb.payload_writes == [COLL_B]
        assert vdb.point_deletes == [COLL_A]

    async def test_globally_orphaned_vrid_is_removed_from_every_org(self):
        """Safe precisely because nothing references it: a VRID still shared
        with a live record takes the rewrite branch above."""
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        graph = _graph({})

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(registry), graph, "vr-gone", MagicMock()
        )

        assert outcome == "deleted"
        assert set(vdb.point_deletes) == {COLL_A, COLL_B}

    async def test_connector_purge_routes_through_the_membership_aware_path(self):
        """A raw connectorIds delete would take points this connector shares
        with a still-live one through dedup."""
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        graph = _graph({"vr-a": [_record("rec-a", ORG_A)]})
        pipeline = _pipeline(vdb, registry, graph)

        result = await pipeline.purge_connector(
            DeleteContext(org_id=ORG_A, connector_id="conn-1"), ["vr-a"]
        )

        # The VRID still has a graph record, so its points are rewritten in
        # that record's own org — and that org's collection is never purged.
        # The other org's is swept, since the VRID does not belong there.
        assert result["action"] == "filtered_delete"
        assert vdb.payload_writes == [COLL_A]
        assert COLL_A not in vdb.point_deletes


class TestReadPathIsOrgScoped:
    async def test_search_resolves_only_the_searching_orgs_collection(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)

        assert await registry.resolve_for_query(QueryContext(org_id=ORG_B)) == [COLL_B]

    async def test_an_org_with_nothing_indexed_resolves_nothing(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)

        assert await registry.resolve_for_query(QueryContext(org_id="newcomer")) == []

    async def test_search_fans_out_only_to_the_resolved_collection(self):
        from app.modules.retrieval.retrieval_service import RetrievalService

        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)

        svc = RetrievalService.__new__(RetrievalService)
        svc.logger = MagicMock()
        svc.collection_registry = registry
        svc.vector_db_service = vdb
        svc._capabilities = MagicMock(
            supports_sparse_vectors=False, supports_server_side_text_search=False
        )
        svc.get_embedding_model_instance = AsyncMock(
            return_value=AsyncMock(aembed_query=AsyncMock(return_value=[0.1]))
        )
        svc._ensure_sparse_embedder = AsyncMock(return_value=None)

        await svc._execute_parallel_searches(["q"], None, 10, ORG_A)

        assert vdb.searched == [COLL_A]


class TestManifestTracksEveryOrg:
    async def test_all_org_collections_are_managed(self):
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)

        names = {e.name for e in await registry.list_managed_collections(fresh=True)}

        assert names == {COLL_A, COLL_B}

    async def test_rebuild_recreates_every_org_collection(self):
        """The model-change rebuild must not stop at one tenant's data."""
        vdb = FakeVectorDB()
        registry = await _two_org_registry(vdb)
        vdb.created.clear()

        recreated = await registry.recreate_all_collections(2048)

        assert set(recreated) == {COLL_A, COLL_B}
        assert set(vdb.created) == {COLL_A, COLL_B}

    async def test_adoption_is_skipped_for_a_context_dependent_strategy(self):
        """There is no single name to probe, and inventing one would adopt a
        collection that does not exist."""
        registry = _registry(FakeVectorDB())

        assert await registry.list_managed_collections() == []
