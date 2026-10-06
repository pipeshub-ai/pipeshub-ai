"""The records rebuild and the entity index, on each real vector backend.

The real registry, entity store and rebuild loop over the backend; the graph
is the unit suite's stand-in and embeddings are a stub. One journey per
backend, because each step starts from what the one before left:

- "Delete all embeddings" empties the records collection and leaves the
  entity index alone, on a deployment whose manifest lists it too.
- An entities collection left as an earlier release's cleanup left it
  (recreated through the records registry: empty, without the entity payload
  indexes, every document still marked done) is refilled, and its points can
  be filtered by entity type again, which needs those indexes on Redis.
- An entities collection dropped by hand under the running store is created
  again and refilled.
- After that nothing more is refilled.

Counts are served from the last refresh on OpenSearch, so the journey
publishes writes before each tick.

  docker run -d --name qdrant-it -p 6343:6333 qdrant/qdrant:v1.15
  cd backend/python && pytest \\
    tests/integration/vector_db/test_entity_index_outlives_records_rebuild_real_backends.py -m integration

Environment: as ``test_entity_vectorstore_real_backends``.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.modules.indexing import entity_index_rebuild
from app.modules.indexing.entity_index_rebuild import (
    REFILL_STATE_KEY,
    EntityIndexRebuilder,
    EntityIndexState,
    entity_index_marker,
)
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.collection_manifest import (
    CollectionManifestStore,
    ManagedCollection,
)
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.collections import CollectionType
from app.services.vector_db.models import CollectionConfig, VectorPoint
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.integration.vector_db.test_entity_vectorstore_real_backends import (
    DIM,
    _opensearch,
    _qdrant,
    _redis,
    _StubEmbeddings,
)
from tests.support.embedding_config import config_service as embedding_config_service
from tests.unit.modules.indexing.test_entity_index_rebuild import (
    APPS,
    ORGS,
    RECORDS,
    TOPICS,
    Clock,
    FakeGraph,
    FakeLock,
    _app,
    _org,
    _rec,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.vector_db.interface.vector_db import IVectorDBService

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

logger = logging.getLogger("entity-index-rebuild-it")
FINGERPRINT = f"stub:hash:{DIM}"


class _PrefixedStrategy(SingleCollectionStrategy):
    """The default strategy under names of this run's own."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return f"{self._prefix}_{ctx.collection_type.value}"


class _Deployment:
    def __init__(self, backend: str, service: IVectorDBService) -> None:
        self.backend, self.service = backend, service
        prefix = f"it_{uuid.uuid4().hex[:8]}"
        self.records = f"{prefix}_{CollectionType.RECORDS.value}"
        self.entities = f"{prefix}_{CollectionType.ENTITIES.value}"
        self.org, self.app = f"org-{uuid.uuid4().hex[:6]}", f"app-{uuid.uuid4().hex[:6]}"
        self.config, self.clock = embedding_config_service(), Clock()
        self.registry = CollectionRegistry(
            vector_db_service=service,
            strategy=_PrefixedStrategy(prefix),
            collection_config_factory=self._records_config,
            manifest_store=CollectionManifestStore(self.config, logger),
            logger=logger,
        )
        self.store = EntityVectorStore(
            logger=logger, config_service=self.config, vector_db_service=service,
            collection_name=self.entities, recreate_on_dimension_mismatch=True,
        )

        async def _stub_embeddings(embedding_configs: list | None = None) -> None:
            self.store._dense_embeddings = _StubEmbeddings()
            self.store._embedding_size = DIM
            self.store._model_id = "stub:hash"

        self.store._init_embeddings = _stub_embeddings  # type: ignore[method-assign]
        self.graph = FakeGraph()
        self.graph.docs[APPS][self.app] = _app(self.app)
        self.graph.docs[ORGS][self.org] = _org(self.org)
        self.graph.sources[(RECORDS, self.app)] = [_rec("r1", name="Q3 plan", org=self.org, group=None)]
        self.graph.sources[(TOPICS, self.org)] = [{"_key": "t1", "name": "Billing"}]
        self.graph.membership[("topic", "t1")] = {"connectorIds": [self.app], "recordGroupIds": []}
        self.graph.nodes[TOPICS] = {"t1": {"orgId": self.org}}

    def _records_config(self, embedding_size: int) -> CollectionConfig:
        return CollectionConfig(
            embedding_size=embedding_size,
            enable_sparse=self.service.get_capabilities().supports_sparse_vectors,
        )

    async def publish(self) -> None:
        if self.backend != "opensearch":
            return
        for name in (self.records, self.entities):
            if await self.service.collection_exists(name):
                await self.service.client.indices.refresh(index=name)  # type: ignore[attr-defined]

    async def rebuild(self) -> list[str]:
        """The rebuild loop on a fresh leader, an idle interval passing after
        each idle tick, until two idle ticks in a row."""
        rebuilder = EntityIndexRebuilder(
            logger=logger, graph_provider=self.graph, store=self.store, lock=FakeLock(),
            config_service=self.config, now_ms=self.clock,
        )
        outcomes: list[str] = []
        for _ in range(60):
            await self.publish()
            outcomes.append(await rebuilder.tick())
            if outcomes[-1] == "idle":
                if outcomes[-2:-1] == ["idle"]:
                    return outcomes
                self.clock.now += int(entity_index_rebuild.IDLE_INTERVAL_SECONDS * 1000)
        raise AssertionError(f"never settled: {outcomes}")

    async def count(self, collection: str) -> int:
        await self.publish()
        info = await self.service.get_collection_info(collection)
        return info.points_count if info.exists else -1

    async def topics(self) -> list[str]:
        await self.publish()
        refs, _ = await self.store.page_entity_points(self.org, ["topic"])
        return [ref.entity_id for ref in refs]

    async def refills(self) -> Any:  # noqa: ANN401
        return await self.config.get_config(REFILL_STATE_KEY, use_cache=False)

    def state(self, collection: str, key: str) -> object:
        return self.graph.docs[collection][key][EntityIndexState.STATE]

    async def close(self) -> None:
        for name in (self.records, self.entities):
            try:
                await self.service.delete_collection(name)
            except Exception:
                logger.warning("could not drop %s", name)
        disconnect = getattr(self.service, "disconnect", None)
        if disconnect is not None:
            await disconnect()


@pytest.fixture(params=["qdrant", "redis", "opensearch"])
async def deployment(request: pytest.FixtureRequest) -> AsyncIterator[_Deployment]:
    service = await {"qdrant": _qdrant, "redis": _redis, "opensearch": _opensearch}[request.param]()
    deployment = _Deployment(request.param, service)
    try:
        yield deployment
    finally:
        await deployment.close()


async def test_the_entity_index_outlives_the_records_rebuild_and_is_refilled_when_emptied(
    deployment: _Deployment,
) -> None:
    d = deployment
    await d.registry.ensure_collection(RecordContext(org_id=d.org), embedding_size=DIM)
    await d.service.upsert_points(d.records, [VectorPoint(
        id=str(uuid.uuid4()), dense_vector=[0.25] * DIM,
        payload={"page_content": "Q3 plan", "metadata": {"orgId": d.org, "virtualRecordId": "v1"}},
    )])
    outcomes = await d.rebuild()
    assert "refill" not in outcomes
    assert (await d.count(d.records), await d.count(d.entities)) == (1, 2)
    # As adoption left the manifest on some deployments in an earlier release.
    await d.registry.manifest_store.record(ManagedCollection(
        name=d.entities, collection_type=CollectionType.ENTITIES.value,
        embedding_dimension=DIM, strategy_name=d.registry.strategy_name,
    ))

    assert await d.registry.recreate_records_collections(DIM) == [d.records]

    assert (await d.count(d.records), await d.count(d.entities)) == (0, 2)
    assert await d.topics() == ["t1"]
    assert await d.rebuild() == ["idle", "idle"]
    assert await d.refills() is None

    # What the earlier release's cleanup did to a listed entities collection.
    await d.service.delete_collection(d.entities)
    await d.service.create_collection(collection_name=d.entities, config=d.registry.build_collection_config(DIM))
    await d.registry._ensure_payload_indexes(d.entities)
    assert await d.count(d.entities) == 0

    outcomes = await d.rebuild()

    assert outcomes[:2] == ["idle", "refill"]
    assert await d.count(d.entities) == 2
    assert await d.topics() == ["t1"]
    assert d.state(APPS, d.app) == entity_index_marker(FINGERPRINT, 1)
    assert d.state(ORGS, d.org) == entity_index_marker(FINGERPRINT, 1)
    assert await d.refills() == {"generation": 1, "emptySinceRefill": False}

    await d.service.delete_collection(d.entities)
    assert await d.count(d.entities) == -1

    outcomes = await d.rebuild()

    assert outcomes[:2] == ["idle", "refill"]
    assert await d.count(d.entities) == 2
    assert await d.topics() == ["t1"]
    assert await d.rebuild() == ["idle", "idle"]
    assert await d.refills() == {"generation": 2, "emptySinceRefill": False}


async def test_an_index_with_nothing_to_hold_is_refilled_once(deployment: _Deployment) -> None:
    """Nothing in the graph to project: the collection stays empty after the
    refill, and must not be refilled again on every later tick."""
    d = deployment
    d.graph.sources.clear()

    first = await d.rebuild()
    again = await d.rebuild()

    assert first.count("refill") == 1
    assert again == ["idle", "idle"]
    assert await d.count(d.entities) == 0
    assert await d.refills() == {"generation": 1, "emptySinceRefill": True}
