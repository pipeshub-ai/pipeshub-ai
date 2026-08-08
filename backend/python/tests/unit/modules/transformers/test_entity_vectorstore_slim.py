"""Unit tests for EntityVectorStore's slim payload, deterministic point IDs,
and org-scoped search (KG Clean Rebuild plan, Phase 8 — complements the
dedup-cache coverage in ``test_entity_vectorstore_dedup.py``).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import SearchResult


def _entity(
    entity_id: str = "e1",
    org_id: str = "org-1",
    entity_type: EntityType = EntityType.CATEGORY,
    name: str = "Legal",
    **kwargs,
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=entity_type,
        name=name,
        org_id=org_id,
        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
        **kwargs,
    )


def _make_store(vector_db_service: MagicMock | None = None) -> EntityVectorStore:
    vector_db_service = vector_db_service or MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    store = EntityVectorStore(
        logger=MagicMock(),
        config_service=MagicMock(),
        vector_db_service=vector_db_service,
    )
    store._initialized = True  # skip embedding-model/collection bootstrap
    store._dense_embeddings = MagicMock(embed_documents=MagicMock(return_value=[[0.1, 0.2]]))
    store._dense_embeddings.embed_query = MagicMock(return_value=[0.1, 0.2])
    store._sparse_embedder = None
    return store


class TestPointIdDeterminism:
    def test_same_triple_yields_same_id(self) -> None:
        id_1 = EntityVectorStore._point_id("org-1", "category", "e1")
        id_2 = EntityVectorStore._point_id("org-1", "category", "e1")

        assert id_1 == id_2
        uuid.UUID(id_1)  # must be a valid UUID string

    def test_different_org_yields_different_id(self) -> None:
        id_org1 = EntityVectorStore._point_id("org-1", "category", "e1")
        id_org2 = EntityVectorStore._point_id("org-2", "category", "e1")

        assert id_org1 != id_org2

    def test_different_type_yields_different_id(self) -> None:
        id_category = EntityVectorStore._point_id("org-1", "category", "e1")
        id_topic = EntityVectorStore._point_id("org-1", "topic", "e1")

        assert id_category != id_topic


class TestUpsertPayloadShape:
    @pytest.mark.asyncio
    async def test_upsert_uses_deterministic_id_and_slim_payload(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        store = _make_store(vector_db_service)
        entity = _entity(entity_id="e1", org_id="org-1", name="Legal", aliases=["Law"])

        await store.upsert_entities_batch([entity])

        vector_db_service.upsert_points.assert_awaited_once()
        _, kwargs = vector_db_service.upsert_points.call_args
        (point,) = kwargs["points"]
        assert point.id == EntityVectorStore._point_id("org-1", "category", "e1")
        metadata = point.payload["metadata"]
        assert metadata == {
            "entityId": "e1",
            "entityType": "category",
            "orgId": "org-1",
            "name": "Legal",
            "canonicalName": "Legal",
            "domain": metadata["domain"],
            "typeCategory": "generic_schema_free",
            "parentEntityId": None,
            "parentEntityType": None,
            "aliases": ["Law"],
            "connectorId": metadata["connectorId"],
        }

    @pytest.mark.asyncio
    async def test_upsert_skips_entity_with_blank_name(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        store = _make_store(vector_db_service)
        entity = _entity(entity_id="e1", name="   ")

        await store.upsert_entities_batch([entity])

        vector_db_service.upsert_points.assert_not_called()


class TestSearchOrgFilter:
    @pytest.mark.asyncio
    async def test_search_scopes_filter_to_org(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(vector_db_service)

        await store.search_entities(query="acme", org_id="org-1")

        vector_db_service.filter_collection.assert_awaited_once()
        _, kwargs = vector_db_service.filter_collection.call_args
        assert kwargs["must"]["metadata.orgId"] == "org-1"
        assert "metadata.entityType" not in kwargs["must"]

    @pytest.mark.asyncio
    async def test_search_adds_entity_type_filter_when_provided(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(vector_db_service)

        await store.search_entities(query="acme", org_id="org-1", entity_types=["person", "category"])

        _, kwargs = vector_db_service.filter_collection.call_args
        assert kwargs["must"]["metadata.entityType"] == ["person", "category"]

    @pytest.mark.asyncio
    async def test_blank_query_returns_empty_without_search(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.query_nearest_points = AsyncMock()
        store = _make_store(vector_db_service)

        result = await store.search_entities(query="   ", org_id="org-1")

        assert result == []
        vector_db_service.query_nearest_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_results_below_score_threshold_are_dropped(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        low_score_hit = SearchResult(
            id="p1", score=-0.5, payload={"metadata": {"entityId": "e1", "entityType": "category", "name": "Legal"}},
        )
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[low_score_hit]])
        store = _make_store(vector_db_service)

        result = await store.search_entities(query="acme", org_id="org-1", score_threshold=0.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(side_effect=RuntimeError("qdrant down"))
        store = _make_store(vector_db_service)

        result = await store.search_entities(query="acme", org_id="org-1")

        assert result == []
