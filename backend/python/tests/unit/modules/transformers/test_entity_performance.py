"""
test_entity_performance.py — Performance and scalability tests for entity vector store.

Matches plan section: "Performance / Load Tests (TestEntityVectorStorePerformance)"

These tests use mocked backends and measure wall-clock behavior of the application
layer (batching, concurrency, iteration). They do NOT require a running vector DB.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evs(*, latency_ms: float = 0.0):
    """
    EntityVectorStore with configurable simulated I/O latency.
    latency_ms: each upsert/scroll call sleeps this many ms.
    """
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    caps = MagicMock(spec=VectorDBCapabilities)
    caps.supports_sparse_vectors = False

    evs = EntityVectorStore.__new__(EntityVectorStore)
    evs.logger = MagicMock()
    evs.collection_name = "entities"
    evs._dense_embeddings = MagicMock()
    evs._sparse_embeddings = None
    evs._embed_sparse = AsyncMock(return_value=[None])

    async def fast_embed(texts, **kwargs):
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000)
        return [[0.1] * 384] * len(texts)

    async def fast_embed_sparse(texts, **kwargs):
        return [None] * len(texts)

    async def fast_upsert(**kwargs):
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000)

    evs._embed = fast_embed
    evs._embed_sparse = fast_embed_sparse
    evs.vector_db_service = MagicMock()
    evs.vector_db_service.capabilities = caps
    evs.vector_db_service.upsert_points = AsyncMock(side_effect=fast_upsert)
    evs._initialized = True  # skip lazy init
    return evs


def _make_entities(n: int, org_id: str = "org1"):
    from app.models.entities import EntityRecord, EntityType

    types = [EntityType.CATEGORY, EntityType.TOPIC, EntityType.DEPARTMENT,
             EntityType.PERSON, EntityType.RECORD_GROUP]
    return [
        EntityRecord(
            entity_id=f"entity_{i}",
            entity_type=types[i % len(types)],
            name=f"Entity Number {i}",
            org_id=org_id,
        )
        for i in range(n)
    ]


# ===========================================================================
# TestEntityVectorStorePerformance
# ===========================================================================


class TestEntityVectorStorePerformance:

    @pytest.mark.asyncio
    async def test_bulk_sync_100_entities_completes_quickly(self):
        """100 entities upserted in batch -> completes within reasonable time."""
        evs = _make_evs(latency_ms=0)
        entities = _make_entities(100)

        start = time.monotonic()
        await evs.upsert_entities_batch(entities, batch_size=25)
        elapsed = time.monotonic() - start

        # With zero I/O latency, this should be well under 5 seconds
        assert elapsed < 5.0, f"Bulk sync of 100 entities took {elapsed:.2f}s (too slow)"

        # All 4 batches of 25 should have been sent
        assert evs.vector_db_service.upsert_points.await_count == 4

    @pytest.mark.asyncio
    async def test_bulk_sync_1000_entities_with_batching(self):
        """1000 entities should be processed in multiple batches without OOM."""
        evs = _make_evs(latency_ms=0)
        entities = _make_entities(1000)

        start = time.monotonic()
        await evs.upsert_entities_batch(entities, batch_size=50)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, f"Bulk sync of 1000 entities took {elapsed:.2f}s"
        assert evs.vector_db_service.upsert_points.await_count == 20  # 1000/50

    @pytest.mark.asyncio
    async def test_entity_resolution_calls_single_search_per_facet(self):
        """Each facet in resolve_entity_filters triggers exactly one search call."""
        from app.agents.actions.retrieval.retrieval import Retrieval

        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])

        state = {
            "org_id": "org1",
            "entity_vector_store": mock_evs,
            "logger": MagicMock(),
        }

        r = Retrieval.__new__(Retrieval)
        r.state = state
        r.writer = None

        facets = ["engineering", "OAuth", "security", "John Smith", "DevOps"]
        await r.resolve_entity_filters(query_facets=facets)

        assert mock_evs.search_entities.await_count == len(facets)

    @pytest.mark.asyncio
    async def test_concurrent_upserts_from_multiple_connectors(self):
        """5 connectors syncing entities simultaneously -> all complete without errors."""
        evs = _make_evs(latency_ms=1)  # 1 ms simulated latency

        async def connector_sync(connector_id: str):
            from app.models.entities import EntityRecord, EntityType

            entities = [
                EntityRecord(
                    entity_id=f"entity_{connector_id}_{i}",
                    entity_type=EntityType.PERSON,
                    name=f"User {i} from {connector_id}",
                    org_id="org1",
                    source_connectors=[connector_id],
                )
                for i in range(10)
            ]
            await evs.upsert_entities_batch(entities, batch_size=5)

        connectors = [f"connector_{c}" for c in ["drive", "jira", "slack", "github", "notion"]]

        start = time.monotonic()
        await asyncio.gather(*[connector_sync(c) for c in connectors])
        elapsed = time.monotonic() - start

        # 5 connectors * 2 batches each = 10 upsert calls
        assert evs.vector_db_service.upsert_points.await_count == 10
        assert elapsed < 5.0, f"Concurrent sync took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_batch_size_affects_upsert_call_count(self):
        """Smaller batch sizes produce more upsert calls; larger batch sizes produce fewer."""
        entities = _make_entities(100)

        evs_small = _make_evs()
        await evs_small.upsert_entities_batch(entities, batch_size=10)
        small_calls = evs_small.vector_db_service.upsert_points.await_count

        evs_large = _make_evs()
        await evs_large.upsert_entities_batch(entities, batch_size=50)
        large_calls = evs_large.vector_db_service.upsert_points.await_count

        assert small_calls == 10   # 100 / 10
        assert large_calls == 2    # 100 / 50
        assert small_calls > large_calls

    @pytest.mark.asyncio
    async def test_empty_name_entities_not_upserted(self):
        """Entities with blank names must not appear in the upserted VectorPoints."""
        from app.models.entities import EntityRecord, EntityType

        evs = _make_evs()

        mixed_entities = [
            EntityRecord(entity_id="e1", entity_type=EntityType.TOPIC, name="Valid Topic", org_id="org1"),
            EntityRecord(entity_id="e2", entity_type=EntityType.TOPIC, name="   ", org_id="org1"),  # blank
            EntityRecord(entity_id="e3", entity_type=EntityType.TOPIC, name="", org_id="org1"),     # empty
            EntityRecord(entity_id="e4", entity_type=EntityType.TOPIC, name="Another Valid", org_id="org1"),
        ]

        await evs.upsert_entities_batch(mixed_entities, batch_size=10)

        # upsert_points should be called with only valid-name entities
        assert evs.vector_db_service.upsert_points.called
        call = evs.vector_db_service.upsert_points.call_args
        points = call.kwargs.get("points") or call.args[1]
        upserted_ids = {p.id for p in points}

        # e2 (blank) and e3 (empty) should not be in upserted points
        valid_id_1 = evs._point_id("org1", "topic", "e1")
        valid_id_4 = evs._point_id("org1", "topic", "e4")
        blank_id_2 = evs._point_id("org1", "topic", "e2")
        empty_id_3 = evs._point_id("org1", "topic", "e3")

        assert valid_id_1 in upserted_ids
        assert valid_id_4 in upserted_ids
        assert blank_id_2 not in upserted_ids
        assert empty_id_3 not in upserted_ids
