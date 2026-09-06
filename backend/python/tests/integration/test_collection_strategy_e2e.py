"""End-to-end tests for the flexible VectorDB collection strategy.

Exercises CollectionRegistry, IndexingPipeline.purge_connector, and
RetrievalService's multi-collection search fan-out against an in-memory
fake IVectorDBService — the same "seeded in-memory fixture" style as
test_graph_navigation_e2e.py, so these run in CI without a real Qdrant/
OpenSearch/Redis instance while still exercising the real production
classes end-to-end rather than mocking their internals.

Scenarios (see the flexible-collection-strategy plan's E2E test list):
1. Two connectors indexed side by side; deleting one preserves the other's data.
2. A strategy that resolves multiple collections fans a search out and merges
   results correctly.

Run with:
    pytest tests/integration/test_collection_strategy_e2e.py -v
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_manifest import CollectionManifestStore
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.filters import build_filter_expression, canonical_filter_key
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
    HybridSearchRequest,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.strategy import (
    CollectionStrategy,
    DeleteContext,
    RecordContext,
)

# ---------------------------------------------------------------------------
# In-memory fake IVectorDBService
# ---------------------------------------------------------------------------


def _get_path(payload: dict, dotted_key: str) -> Any:
    node: Any = payload
    for part in dotted_key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _condition_matches(payload: dict, condition: FieldCondition) -> bool:
    actual = _get_path(payload, condition.key)
    if condition.values is not None:
        if isinstance(actual, list):
            return any(v in actual for v in condition.values)
        return actual in condition.values
    if isinstance(actual, list):
        return condition.value in actual
    return actual == condition.value


def _filter_matches(payload: dict, flt: FilterExpression | None) -> bool:
    if flt is None or flt.is_empty():
        return True
    if flt.must and not all(_condition_matches(payload, c) for c in flt.must):
        return False
    if flt.must_not and any(_condition_matches(payload, c) for c in flt.must_not):
        return False
    if flt.should and not any(_condition_matches(payload, c) for c in flt.should):
        return False
    return True


def _dot(a: List[float] | None, b: List[float] | None) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class FakeVectorDBService(IVectorDBService):
    """Minimal in-memory IVectorDBService: enough to exercise
    CollectionRegistry, IndexingPipeline, and RetrievalService without a
    real vector DB. Not a general-purpose test double for provider-specific
    behavior (quantization, sparse fusion, etc.) — see the real per-provider
    integration tests under tests/integration/vector_db/ for that.
    """

    def __init__(self, embedding_size: int = 4) -> None:
        self._embedding_size = embedding_size
        self.collections: Dict[str, List[VectorPoint]] = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def get_service_name(self) -> str:
        return "fake"

    def get_service(self) -> "IVectorDBService":
        return self

    def get_service_client(self) -> object:
        return self

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities()

    async def health_check(self) -> VectorDBHealth:
        return VectorDBHealth()

    async def create_collection(
        self, collection_name: str = "records", config: CollectionConfig | None = None
    ) -> None:
        self.collections.setdefault(collection_name, [])

    async def get_collections(self) -> object:
        return list(self.collections.keys())

    async def get_collection(self, collection_name: str) -> object:
        return self.collections.get(collection_name)

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        if collection_name not in self.collections:
            return VectorCollectionInfo(name=collection_name, exists=False)
        return VectorCollectionInfo(
            name=collection_name,
            exists=True,
            dense_dimension=self._embedding_size,
            points_count=len(self.collections[collection_name]),
        )

    async def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    async def delete_collection(self, collection_name: str) -> None:
        if collection_name not in self.collections:
            raise ValueError(f"Collection {collection_name} not found")
        del self.collections[collection_name]

    async def create_index(
        self, collection_name: str, field_name: str, field_schema: dict
    ) -> None:
        pass

    async def filter_collection(
        self,
        filter_mode: str | FilterMode = FilterMode.MUST,
        must: Dict[str, FilterValue] | None = None,
        should: Dict[str, FilterValue] | None = None,
        must_not: Dict[str, FilterValue] | None = None,
        min_should_match: int | None = None,
        **filters: FilterValue,
    ) -> FilterExpression:
        def _build_conditions(d: Dict[str, FilterValue]) -> List[FieldCondition]:
            conditions = []
            for key, value in d.items():
                if value is None:
                    continue
                field_key = canonical_filter_key(key)
                if isinstance(value, (list, tuple)):
                    if value:
                        conditions.append(FieldCondition(key=field_key, values=list(value)))
                else:
                    conditions.append(FieldCondition(key=field_key, value=value))
            return conditions

        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=min_should_match,
            extra_kwargs=filters or None,
            build_conditions=_build_conditions,
        )

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: str | None = None,
    ) -> ScrollResult:
        points = self.collections.get(collection_name, [])
        matched = [p for p in points if _filter_matches(p.payload, scroll_filter)]
        return ScrollResult(points=matched[:limit], next_offset=None)

    async def query_nearest_points(
        self, collection_name: str, requests: List[HybridSearchRequest]
    ) -> List[List[SearchResult]]:
        points = self.collections.get(collection_name, [])
        batches: List[List[SearchResult]] = []
        for req in requests:
            candidates = [p for p in points if _filter_matches(p.payload, req.filter)]
            scored = sorted(
                (
                    SearchResult(id=p.id, score=_dot(req.dense_query, p.dense_vector), payload=p.payload)
                    for p in candidates
                ),
                key=lambda r: r.score,
                reverse=True,
            )
            batches.append(scored[: req.limit])
        return batches

    async def upsert_points(self, collection_name: str, points: List[VectorPoint]) -> None:
        existing = self.collections.setdefault(collection_name, [])
        by_id = {p.id: p for p in existing}
        for point in points:
            by_id[point.id] = point
        self.collections[collection_name] = list(by_id.values())

    async def delete_points(self, collection_name: str, filter: FilterExpression) -> None:
        points = self.collections.get(collection_name, [])
        self.collections[collection_name] = [
            p for p in points if not _filter_matches(p.payload, filter)
        ]

    async def overwrite_payload(
        self, collection_name: str, payload: dict, points: FilterExpression
    ) -> None:
        for p in self.collections.get(collection_name, []):
            if _filter_matches(p.payload, points):
                p.payload = dict(payload)

    async def set_payload(self, collection_name: str, payload: dict, filter: FilterExpression) -> None:
        for p in self.collections.get(collection_name, []):
            if _filter_matches(p.payload, filter):
                p.payload.update(payload)


def _make_config_service():
    store: dict = {}

    async def get_config(key, default=None):
        return store.get(key, default)

    async def set_config(key, value):
        store[key] = value

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock(side_effect=set_config)
    return svc


def _make_registry(vector_db_service, strategy) -> CollectionRegistry:
    return CollectionRegistry(
        vector_db_service=vector_db_service,
        strategy=strategy,
        collection_config_factory=lambda size, sparse_idf=False: CollectionConfig(
            embedding_size=size
        ),
        manifest_store=CollectionManifestStore(_make_config_service(), MagicMock()),
        logger=MagicMock(),
    )


def _point(point_id: str, org_id: str, vrid: str, connector_id: str, vector: List[float]) -> VectorPoint:
    return VectorPoint(
        id=point_id,
        dense_vector=vector,
        payload={
            "page_content": f"content for {point_id}",
            "metadata": {"orgId": org_id, "virtualRecordId": vrid},
            "connectorIds": [connector_id],
        },
    )


# ---------------------------------------------------------------------------
# Scenario 1: two connectors share a collection; deleting one preserves the
# other's data (the correctness property purge_connector must not violate).
# ---------------------------------------------------------------------------


class TestConnectorDeletionPreservesOtherConnectors:
    @pytest.mark.asyncio
    async def test_delete_one_connector_leaves_other_connector_intact(self):
        from app.modules.indexing.run import IndexingPipeline
        from app.services.vector_db.strategies.single import SingleCollectionStrategy

        vdb = FakeVectorDBService()
        registry = _make_registry(vdb, SingleCollectionStrategy())

        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=4)
        await vdb.upsert_points(
            name,
            [
                _point("p-drive-1", "org-1", "vr-drive-1", "conn-drive", [1.0, 0.0, 0.0, 0.0]),
                _point("p-slack-1", "org-1", "vr-slack-1", "conn-slack", [0.0, 1.0, 0.0, 0.0]),
            ],
        )

        graph_provider = AsyncMock()
        # After connector-drive's records are deleted from the graph, no
        # remaining record references vr-drive-1 — safe to hard-delete.
        graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=_make_config_service(),
            graph_provider=graph_provider,
            collection_registry=registry,
            vector_db_service=vdb,
        )

        ctx = DeleteContext(org_id="org-1", connector_id="conn-drive", connector_name="GOOGLE_DRIVE")
        import app.modules.indexing.run as run_mod

        original_delay = run_mod.EMPTY_CONFIRM_DELAY_SECONDS
        run_mod.EMPTY_CONFIRM_DELAY_SECONDS = 0
        try:
            result = await pipeline.purge_connector(ctx, ["vr-drive-1"])
        finally:
            run_mod.EMPTY_CONFIRM_DELAY_SECONDS = original_delay

        assert result["action"] == "filtered_delete"
        remaining_ids = {p.id for p in vdb.collections[name]}
        assert remaining_ids == {"p-slack-1"}

    @pytest.mark.asyncio
    async def test_shared_vrid_across_connectors_is_rewritten_not_deleted(self):
        """A VRID deduplicated across two connectors must survive deletion of
        one connector — this is exactly the correctness gap a raw
        connectorIds filter-delete would introduce (see purge_connector's
        docstring)."""
        from app.modules.indexing.run import IndexingPipeline
        from app.services.vector_db.strategies.single import SingleCollectionStrategy

        vdb = FakeVectorDBService()
        registry = _make_registry(vdb, SingleCollectionStrategy())
        name = await registry.ensure_collection(RecordContext(org_id="org-1"), embedding_size=4)

        shared_point = VectorPoint(
            id="p-shared",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            payload={
                "page_content": "shared content",
                "metadata": {"orgId": "org-1", "virtualRecordId": "vr-shared"},
                "connectorIds": ["conn-drive", "conn-slack"],
            },
        )
        await vdb.upsert_points(name, [shared_point])

        graph_provider = AsyncMock()
        # A record under conn-slack still references this VRID even after
        # conn-drive's own record was deleted from the graph.
        graph_provider.get_records_by_virtual_record_id = AsyncMock(
            return_value=[{"_key": "rec-slack", "isDeleted": False}]
        )

        pipeline = IndexingPipeline(
            logger=MagicMock(),
            config_service=_make_config_service(),
            graph_provider=graph_provider,
            collection_registry=registry,
            vector_db_service=vdb,
        )

        ctx = DeleteContext(org_id="org-1", connector_id="conn-drive")
        result = await pipeline.purge_connector(ctx, ["vr-shared"])

        assert result["action"] == "filtered_delete"
        assert result["virtual_record_ids_deleted"] == 0
        assert result["virtual_record_ids_rewritten"] == 1
        remaining_ids = {p.id for p in vdb.collections[name]}
        assert remaining_ids == {"p-shared"}, "shared point must survive one connector's deletion"


# ---------------------------------------------------------------------------
# Scenario 2: a strategy resolving multiple collections fans a search out
# and merges results.
# ---------------------------------------------------------------------------


class _PerConnectorNameStrategy(CollectionStrategy):
    """Test-only strategy: one collection per connectorName, proving the
    interface supports fan-out without any OSS code depending on it."""

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return f"{(ctx.connector_name or 'unknown').lower()}_records"

    def resolve_read_collections(self, ctx, managed) -> list:
        names = ctx.accessible_connector_names or []
        if names:
            return [f"{n.lower()}_records" for n in names]
        # No connector hint: the strategy cannot narrow, so it hands back
        # every collection it manages of this type rather than guessing.
        return [
            entry.name
            for entry in managed
            if entry.collection_type == ctx.collection_type.value
        ]

    def resolve_delete_scope(self, ctx: DeleteContext):
        raise NotImplementedError

    def strategy_name(self) -> str:
        return "per_connector_name_test_only"


class TestMultiCollectionSearchFanOut:
    @pytest.mark.asyncio
    async def test_search_merges_results_from_two_collections(self):
        from app.modules.retrieval.retrieval_service import RetrievalService

        vdb = FakeVectorDBService()
        strategy = _PerConnectorNameStrategy()
        registry = _make_registry(vdb, strategy)

        drive_name = await registry.ensure_collection(
            RecordContext(org_id="org-1", connector_name="GOOGLE_DRIVE"), embedding_size=4
        )
        slack_name = await registry.ensure_collection(
            RecordContext(org_id="org-1", connector_name="SLACK"), embedding_size=4
        )
        await vdb.upsert_points(
            drive_name,
            [_point("p-drive-1", "org-1", "vr-drive-1", "conn-drive", [1.0, 0.0, 0.0, 0.0])],
        )
        await vdb.upsert_points(
            slack_name,
            [_point("p-slack-1", "org-1", "vr-slack-1", "conn-slack", [0.0, 1.0, 0.0, 0.0])],
        )

        registry.resolve_for_query = AsyncMock(return_value=[drive_name, slack_name])

        svc = RetrievalService.__new__(RetrievalService)
        svc.logger = MagicMock()
        svc.config_service = MagicMock()
        svc.collection_registry = registry
        svc.vector_db_service = vdb
        svc.graph_provider = AsyncMock()
        svc._capabilities = vdb.get_capabilities()
        svc.get_embedding_model_instance = AsyncMock(
            return_value=AsyncMock(aembed_query=AsyncMock(return_value=[0.5, 0.5, 0.0, 0.0]))
        )
        svc._ensure_sparse_embedder = AsyncMock(return_value=None)

        results = await svc._execute_parallel_searches(["query"], None, 10, "org-1")

        contents = {r["content"] for r in results}
        assert contents == {"content for p-drive-1", "content for p-slack-1"}

    @pytest.mark.asyncio
    async def test_resolve_for_query_skips_never_created_collection(self):
        """A strategy naming a collection nothing has been indexed into yet
        must not error — it's silently absent from the fan-out."""
        vdb = FakeVectorDBService()
        strategy = _PerConnectorNameStrategy()
        registry = _make_registry(vdb, strategy)
        await registry.ensure_collection(
            RecordContext(org_id="org-1", connector_name="GOOGLE_DRIVE"), embedding_size=4
        )

        from app.services.vector_db.strategy import QueryContext

        resolved = await registry.resolve_for_query(
            QueryContext(org_id="org-1", accessible_connector_names=["GOOGLE_DRIVE", "SLACK"])
        )

        assert resolved == ["google_drive_records"]
