"""
Provider-parametrized contract tests for CollectionRegistry.

Verifies that CollectionRegistry's lifecycle operations (create, exists,
upsert+search through the resolved name, drop, drop-again-is-noop,
create-again-is-noop) behave identically regardless of which vector DB
backs it — the whole point of going through the registry/strategy
abstraction instead of a provider-specific client.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_collection_registry_contract.py -m integration --timeout=120
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.models import CollectionConfig, HybridSearchRequest
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import RecordContext
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import DIM, make_dense, sample_points

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _make_config_service():
    """In-memory fake ConfigurationService — the manifest just needs to
    round-trip through get_config/set_config, not touch a real KV store."""
    store: dict = {}

    async def get_config(key, default=None):
        return store.get(key, default)

    async def set_config(key, value):
        store[key] = value

    svc = MagicMock()
    svc.get_config = AsyncMock(side_effect=get_config)
    svc.set_config = AsyncMock(side_effect=set_config)
    return svc


def _make_registry(vector_db_service) -> CollectionRegistry:
    return CollectionRegistry(
        vector_db_service=vector_db_service,
        strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size, sparse_idf=False: CollectionConfig(
            embedding_size=size
        ),
        config_service=_make_config_service(),
        logger=MagicMock(),
    )


class _CollectionRegistryContractTests:
    """Provider-agnostic contract. Subclasses inject `vector_service` from a
    module-scoped fixture (qdrant_service / opensearch_service / redis_service).
    """

    async def test_ensure_collection_creates_then_is_idempotent(self, vector_service):
        registry = _make_registry(vector_service)
        col = make_collection(f"{vector_service.get_service_name()}_reg")
        ctx = RecordContext(org_id="org-registry-contract")
        try:
            name = await registry.ensure_collection(ctx, embedding_size=DIM)
            assert await vector_service.collection_exists(name)

            # create-again is a no-op: same name, no error, dimension re-verified
            name_again = await registry.ensure_collection(ctx, embedding_size=DIM)
            assert name_again == name
        finally:
            await vector_service.delete_collection(name)

    async def test_upsert_and_search_through_resolved_name(self, vector_service):
        registry = _make_registry(vector_service)
        ctx = RecordContext(org_id="org-registry-search")
        name = await registry.ensure_collection(ctx, embedding_size=DIM)
        try:
            await vector_service.upsert_points(name, sample_points("org-registry-search"))
            flt = await vector_service.filter_collection(must={"orgId": "org-registry-search"})
            results = await vector_service.query_nearest_points(
                name,
                [HybridSearchRequest(dense_query=make_dense([1.0]), filter=flt, limit=3)],
            )
            assert len(results[0]) >= 1
        finally:
            await registry.delete_collection(name)

    async def test_drop_then_drop_again_is_noop(self, vector_service):
        registry = _make_registry(vector_service)
        ctx = RecordContext(org_id="org-registry-drop")
        name = await registry.ensure_collection(ctx, embedding_size=DIM)

        await registry.delete_collection(name)
        assert not await vector_service.collection_exists(name)

        # Dropping an already-missing collection must not raise.
        await registry.delete_collection(name)

    async def test_manifest_tracks_created_collection_until_dropped(self, vector_service):
        registry = _make_registry(vector_service)
        ctx = RecordContext(org_id="org-registry-manifest")
        name = await registry.ensure_collection(ctx, embedding_size=DIM)
        try:
            managed_names = [c.name for c in await registry.list_managed_collections()]
            assert name in managed_names
        finally:
            await registry.delete_collection(name)

        managed_names_after_drop = [c.name for c in await registry.list_managed_collections()]
        assert name not in managed_names_after_drop


class TestQdrantCollectionRegistryContract(_CollectionRegistryContractTests):
    @pytest.fixture
    async def vector_service(self, qdrant_service):
        return qdrant_service


class TestOpenSearchCollectionRegistryContract(_CollectionRegistryContractTests):
    @pytest.fixture
    async def vector_service(self, opensearch_service):
        return opensearch_service


class TestRedisCollectionRegistryContract(_CollectionRegistryContractTests):
    @pytest.fixture
    async def vector_service(self, redis_service):
        return redis_service
