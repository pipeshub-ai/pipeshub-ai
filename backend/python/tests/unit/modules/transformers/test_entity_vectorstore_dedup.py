"""Unit tests for EntityVectorStore's opportunistic-sync dedup cache
(KG Clean Rebuild plan, Phase 2 — avoids re-embedding a shared taxonomy /
record-group entity for every record that references it).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import EntityVectorStore


def _make_store() -> EntityVectorStore:
    vector_db_service = MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(
        supports_sparse_vectors=False
    )
    store = EntityVectorStore(
        logger=MagicMock(),
        config_service=MagicMock(),
        vector_db_service=vector_db_service,
    )
    store.upsert_entities_batch = AsyncMock()
    return store


def _entity(entity_id="e1", org_id="org-1", entity_type=EntityType.CATEGORY, name="Legal") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        org_id=org_id,
        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
    )


class TestSyncEntitiesFromMetadataDedup:
    @pytest.mark.asyncio
    async def test_first_sync_upserts(self):
        store = _make_store()
        entity = _entity()

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[entity])

        store.upsert_entities_batch.assert_awaited_once()
        (batch,) = store.upsert_entities_batch.call_args[0]
        assert batch == [entity]

    @pytest.mark.asyncio
    async def test_repeat_sync_within_ttl_is_skipped(self):
        store = _make_store()
        entity = _entity()

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[entity])
        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[entity])

        # Second call finds nothing fresh to upsert.
        assert store.upsert_entities_batch.await_count == 1

    @pytest.mark.asyncio
    async def test_distinct_entities_both_sync(self):
        store = _make_store()
        e1 = _entity(entity_id="e1", name="Legal")
        e2 = _entity(entity_id="e2", name="Finance")

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[e1, e2])

        (batch,) = store.upsert_entities_batch.call_args[0]
        assert {e.entity_id for e in batch} == {"e1", "e2"}

    @pytest.mark.asyncio
    async def test_same_entity_id_different_org_both_sync(self):
        """Dedup key must include org_id — no cross-tenant cache bleed."""
        store = _make_store()
        e_org1 = _entity(entity_id="e1", org_id="org-1")
        e_org2 = _entity(entity_id="e1", org_id="org-2")

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[e_org1])
        await store.sync_entities_from_metadata(org_id="org-2", new_entities=[e_org2])

        assert store.upsert_entities_batch.await_count == 2

    @pytest.mark.asyncio
    async def test_same_entity_id_different_type_both_sync(self):
        """Dedup key must include entity_type — category vs subcategory with
        the same graph key must not collide."""
        store = _make_store()
        e_cat = _entity(entity_id="shared-id", entity_type=EntityType.CATEGORY)
        e_sub = _entity(entity_id="shared-id", entity_type=EntityType.SUBCATEGORY)

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[e_cat, e_sub])

        (batch,) = store.upsert_entities_batch.call_args[0]
        assert len(batch) == 2

    @pytest.mark.asyncio
    async def test_empty_list_is_noop(self):
        store = _make_store()

        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[])

        store.upsert_entities_batch.assert_not_awaited()


class TestSyncEntityIfStale:
    @pytest.mark.asyncio
    async def test_syncs_new_entity(self):
        store = _make_store()
        entity = _entity(entity_type=EntityType.RECORD_GROUP)

        await store.sync_entity_if_stale(entity)

        store.upsert_entities_batch.assert_awaited_once_with([entity])

    @pytest.mark.asyncio
    async def test_skips_recently_synced_entity(self):
        store = _make_store()
        entity = _entity(entity_type=EntityType.RECORD_GROUP)

        await store.sync_entity_if_stale(entity)
        await store.sync_entity_if_stale(entity)

        assert store.upsert_entities_batch.await_count == 1

    @pytest.mark.asyncio
    async def test_shares_cache_with_batch_sync(self):
        """sync_entity_if_stale and sync_entities_from_metadata must dedup
        against the same cache — a record-group entity synced via one path
        should not immediately re-sync via the other."""
        store = _make_store()
        entity = _entity(entity_type=EntityType.RECORD_GROUP)

        await store.sync_entity_if_stale(entity)
        await store.sync_entities_from_metadata(org_id="org-1", new_entities=[entity])

        assert store.upsert_entities_batch.await_count == 1


class TestExplicitRepairBypassesCache:
    @pytest.mark.asyncio
    async def test_upsert_entities_batch_is_not_deduped(self):
        """The admin entity-sync/trigger repair path calls upsert_entities_batch
        directly and must always force a refresh, regardless of the
        opportunistic-sync cache state."""
        vector_db_service = MagicMock()
        vector_db_service.get_capabilities.return_value = MagicMock(
            supports_sparse_vectors=False
        )
        store = EntityVectorStore(
            logger=MagicMock(),
            config_service=MagicMock(),
            vector_db_service=vector_db_service,
        )
        store._ensure_initialized = AsyncMock()
        store._embed = AsyncMock(return_value=[[0.1, 0.2]])
        store._embed_sparse = AsyncMock(return_value=[None])
        store.vector_db_service.upsert_points = AsyncMock()
        entity = _entity()

        # Simulate the opportunistic path having just synced this entity.
        await store._filter_freshly_synced([entity])

        # Repair path bypasses the cache entirely.
        await store.upsert_entities_batch([entity])
        await store.upsert_entities_batch([entity])

        assert store.vector_db_service.upsert_points.await_count == 2
