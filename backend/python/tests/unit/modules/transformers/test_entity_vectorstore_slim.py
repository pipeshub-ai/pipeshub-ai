"""Unit tests for EntityVectorStore's slim payload, deterministic point IDs,
membership merging, and org-scoped search (KG Clean Rebuild plan, Phase 8).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import ScrollResult, SearchResult, VectorPoint


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
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )
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
            "aliases": ["Law"],
            "level": None,
        }
        assert point.payload["connectorIds"] == []
        assert point.payload["recordGroupIds"] == []

    @pytest.mark.asyncio
    async def test_upsert_includes_populated_membership_arrays(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )
        store = _make_store(vector_db_service)
        entity = _entity(
            entity_id="e1",
            org_id="org-1",
            name="Legal",
            connector_ids=["c1"],
            record_group_ids=["g1"],
        )

        await store.upsert_entities_batch([entity])

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["c1"]
        assert point.payload["recordGroupIds"] == ["g1"]

    @pytest.mark.asyncio
    async def test_upsert_skips_entity_with_blank_name(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        store = _make_store(vector_db_service)
        entity = _entity(entity_id="e1", name="   ")

        await store.upsert_entities_batch([entity])

        vector_db_service.upsert_points.assert_not_called()


class TestMembershipMerge:
    """Membership arrays are unioned with whatever is already stored for that
    entity point, not replaced — see ``EntityVectorStore._merge_membership``.
    """

    @pytest.mark.asyncio
    async def test_merge_unions_with_existing_membership(self) -> None:
        """A shared department already has one group/connector on file;
        upserting it again for a different record must keep both, not
        replace the stored arrays with only the new record's own values."""
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        existing_point = VectorPoint(
            id=EntityVectorStore._point_id("org-1", "department", "eng"),
            payload={
                "metadata": {},
                "connectorIds": ["gdrive"],
                "recordGroupIds": ["group_A"],
            },
        )
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[existing_point], next_offset=None)
        )
        store = _make_store(vector_db_service)
        entity = _entity(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            connector_ids=["confluence"],
            record_group_ids=["group_B"],
        )

        await store.upsert_entities_batch([entity])

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["gdrive", "confluence"]
        assert point.payload["recordGroupIds"] == ["group_A", "group_B"]

    @pytest.mark.asyncio
    async def test_merge_dedupes_ids_already_present(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        existing_point = VectorPoint(
            id=EntityVectorStore._point_id("org-1", "department", "eng"),
            payload={
                "metadata": {},
                "connectorIds": [],
                "recordGroupIds": ["group_A"],
            },
        )
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[existing_point], next_offset=None)
        )
        store = _make_store(vector_db_service)
        entity = _entity(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            record_group_ids=["group_A"],  # same group reported again
        )

        await store.upsert_entities_batch([entity])

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["recordGroupIds"] == ["group_A"]

    @pytest.mark.asyncio
    async def test_merge_read_failure_skips_the_write(self) -> None:
        """The point ID is deterministic, so upserting against an unknown
        state would replace the stored membership with only this caller's
        ids. Skipping leaves the point intact for the next write."""
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        vector_db_service.filter_collection = AsyncMock(
            side_effect=RuntimeError("vector db down")
        )
        store = _make_store(vector_db_service)
        entity = _entity(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            record_group_ids=["group_B"],
        )

        await store.upsert_entities_batch([entity])

        vector_db_service.upsert_points.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merge_read_failure_skips_only_the_failing_entity(self) -> None:
        """One bad read must not cost the other 63 entities in the batch."""
        vector_db_service = MagicMock()
        vector_db_service.upsert_points = AsyncMock(return_value=None)
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )

        async def _filter(must):
            if must["metadata.entityId"] == "bad":
                raise RuntimeError("vector db down")
            return {"must": []}

        vector_db_service.filter_collection = AsyncMock(side_effect=_filter)
        store = _make_store(vector_db_service)
        entities = [
            _entity(entity_id="bad", entity_type=EntityType.DEPARTMENT, name="Bad"),
            _entity(entity_id="good", entity_type=EntityType.DEPARTMENT, name="Good"),
        ]

        await store.upsert_entities_batch(entities)

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["metadata"]["entityId"] == "good"


class TestConcurrentMergeIsSerialised:
    """Two writers touching the same shared entity must not each merge
    against a stale read — see ``EntityVectorStore._entity_lock``. Mirrors
    the equivalent VRID-lock coverage in ``test_vector_membership.py``.
    """

    @pytest.mark.asyncio
    async def test_same_entity_reads_and_writes_are_serialised(self) -> None:
        """Two concurrent upserts for the same department (two different
        records, two different groups) must not interleave their
        read-merge-write, and neither writer's group may be lost."""
        import asyncio

        trace: list[str] = []
        state: dict[str, list[str]] = {"recordGroupIds": []}

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})

        async def _scroll(collection_name, scroll_filter, limit, offset=None) -> ScrollResult:
            trace.append("read")
            if not state["recordGroupIds"]:
                return ScrollResult(points=[], next_offset=None)
            point = VectorPoint(
                id="p1",
                payload={
                    "metadata": {},
                    "recordGroupIds": list(state["recordGroupIds"]),
                    "connectorIds": [],
                },
            )
            return ScrollResult(points=[point], next_offset=None)

        async def _upsert_points(collection_name, points) -> None:
            await asyncio.sleep(0.01)  # yield between read and write
            trace.append("write")
            (point,) = points
            state["recordGroupIds"] = point.payload["recordGroupIds"]

        vector_db_service.scroll = AsyncMock(side_effect=_scroll)
        vector_db_service.upsert_points = AsyncMock(side_effect=_upsert_points)
        store = _make_store(vector_db_service)

        entity_a = _entity(
            entity_id="eng", entity_type=EntityType.DEPARTMENT,
            name="Engineering", record_group_ids=["group_A"],
        )
        entity_b = _entity(
            entity_id="eng", entity_type=EntityType.DEPARTMENT,
            name="Engineering", record_group_ids=["group_B"],
        )

        await asyncio.gather(
            store.upsert_entities_batch([entity_a]),
            store.upsert_entities_batch([entity_b]),
        )

        assert trace == ["read", "write", "read", "write"], (
            f"read/write interleaved across concurrent upserts: {trace}"
        )
        assert set(state["recordGroupIds"]) == {"group_A", "group_B"}, (
            "both groups must survive — neither writer may lose the other's update"
        )

    @pytest.mark.asyncio
    async def test_different_entities_are_not_serialised(self) -> None:
        """The lock is per entity; unrelated entities must still run
        concurrently rather than queuing behind each other."""
        import asyncio

        active = {"n": 0, "max": 0}

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )

        async def _upsert_points(collection_name, points) -> None:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            await asyncio.sleep(0.01)
            active["n"] -= 1

        vector_db_service.upsert_points = AsyncMock(side_effect=_upsert_points)
        store = _make_store(vector_db_service)

        entity_a = _entity(entity_id="eng", entity_type=EntityType.DEPARTMENT, name="Engineering")
        entity_b = _entity(entity_id="sales", entity_type=EntityType.DEPARTMENT, name="Sales")

        await asyncio.gather(
            store.upsert_entities_batch([entity_a]),
            store.upsert_entities_batch([entity_b]),
        )

        assert active["max"] == 2, "different entities should not block each other"


class TestSearchOrgFilter:
    @pytest.mark.asyncio
    async def test_search_scopes_filter_to_org(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(vector_db_service)

        await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
        )

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

        await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
            entity_types=["topic", "category"],
        )

        _, kwargs = vector_db_service.filter_collection.call_args
        assert kwargs["must"]["metadata.entityType"] == ["topic", "category"]

    @pytest.mark.asyncio
    async def test_should_filter_carries_record_groups_and_connectors(self) -> None:
        """The permission filter is a should-clause on both membership
        fields — no min_should_match, since at least one match is the
        cross-provider default (see plan Part 1's filter-semantics note)."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(vector_db_service)

        await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids={"rg-2", "rg-1"},
            accessible_connector_ids={"conn-1"},
        )

        _, kwargs = vector_db_service.filter_collection.call_args
        assert kwargs["should"]["recordGroupIds"] == ["rg-1", "rg-2"]
        assert kwargs["should"]["connectorIds"] == ["conn-1"]
        assert "min_should_match" not in kwargs

    @pytest.mark.asyncio
    async def test_empty_scope_returns_empty_without_search(self) -> None:
        """Both id collections empty must short-circuit — omitting the
        should-group would otherwise silently widen back to org-wide."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock()
        store = _make_store(vector_db_service)

        result = await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids=set(), accessible_connector_ids=set(),
        )

        assert result == []
        vector_db_service.filter_collection.assert_not_called()
        vector_db_service.query_nearest_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_hits_carry_membership_fields_for_stage_two(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        hit = SearchResult(
            id="p1", score=0.9,
            payload={
                "metadata": {
                    "entityId": "e1", "entityType": "category", "name": "Legal",
                },
                "connectorIds": ["conn-1"], "recordGroupIds": ["rg-1"],
            },
        )
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[hit]])
        store = _make_store(vector_db_service)

        result = await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
        )

        assert result[0]["connectorIds"] == ["conn-1"]
        assert result[0]["recordGroupIds"] == ["rg-1"]

    @pytest.mark.asyncio
    async def test_blank_query_returns_empty_without_search(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.query_nearest_points = AsyncMock()
        store = _make_store(vector_db_service)

        result = await store.search_entities(
            query="   ", org_id="org-1",
            accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
        )

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

        result = await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
            score_threshold=0.0,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_search_failure_raises(self) -> None:
        """A vector DB failure must not look like "no matching entities"."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(side_effect=RuntimeError("qdrant down"))
        store = _make_store(vector_db_service)

        with pytest.raises(RuntimeError):
            await store.search_entities(
                query="acme", org_id="org-1",
                accessible_record_group_ids={"rg-1"}, accessible_connector_ids=set(),
            )

    @pytest.mark.asyncio
    async def test_allow_org_wide_searches_without_membership_filter(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        store = _make_store(vector_db_service)

        await store.search_entities(
            query="acme", org_id="org-1",
            accessible_record_group_ids=set(), accessible_connector_ids=set(),
            allow_org_wide=True,
        )

        _, kwargs = vector_db_service.filter_collection.call_args
        assert kwargs["must"]["metadata.orgId"] == "org-1"
        assert kwargs["should"] == {}
        vector_db_service.query_nearest_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_org_returns_empty_even_when_org_wide(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.query_nearest_points = AsyncMock()
        store = _make_store(vector_db_service)

        result = await store.search_entities(
            query="acme", org_id="",
            accessible_record_group_ids=set(), accessible_connector_ids=set(),
            allow_org_wide=True,
        )

        assert result == []
        vector_db_service.query_nearest_points.assert_not_called()
