"""Unit tests for EntityVectorStore's deletion paths: the entityType filter
fix on ``delete_entity``, and the five-phase connector-scoped cleanup in
``delete_entities_by_connector``/``_shrink_connector_membership``.

The five phases are:
1. Scroll all entity points matching the connector.
2. Delete RECORD entities outright (single-connector by definition).
3. Delete RECORD_GROUP entities outright and collect their recordGroupIds.
4. Delete exclusive taxonomy entities (only this connectorId).
5. Strip connectorId AND collected recordGroupIds from shared taxonomy entities.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.models.entities import EntityType
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import ScrollResult, VectorPoint


def _make_store(vector_db_service: MagicMock | None = None) -> EntityVectorStore:
    vector_db_service = vector_db_service or MagicMock()
    vector_db_service.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    store = EntityVectorStore(
        logger=MagicMock(),
        config_service=MagicMock(),
        vector_db_service=vector_db_service,
    )
    store._initialized = True  # skip embedding-model/collection bootstrap
    store._dense_embeddings = MagicMock(
        embed_documents=MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
    )
    store._sparse_embedder = None
    return store


def _point(
    entity_id: str,
    entity_type: str,
    connector_ids: list[str],
    record_group_ids: list[str],
    **extra,
) -> VectorPoint:
    metadata = {
        "entityId": entity_id,
        "entityType": entity_type,
        "name": extra.pop("name", "Engineering"),
        "canonicalName": extra.pop("canonicalName", "Engineering"),
        "typeCategory": extra.pop("typeCategory", "predefined"),
        "aliases": extra.pop("aliases", []),
    }
    metadata.update(extra)
    return VectorPoint(
        id=f"point-{entity_id}",
        payload={
            "metadata": metadata,
            "connectorIds": connector_ids,
            "recordGroupIds": record_group_ids,
        },
    )


def _deleted_entity_ids(vector_db_service: MagicMock) -> list[tuple[str, str]]:
    """Extract (entityType, entityId) pairs from all filter_collection calls
    that were followed by a delete_points call."""
    results = []
    for c in vector_db_service.filter_collection.call_args_list:
        must = c.kwargs.get("must", {})
        eid = must.get("metadata.entityId")
        etype = must.get("metadata.entityType")
        if eid and etype:
            results.append((etype, eid))
    return results


# ======================================================================
# TestDeleteEntityFilter — basic delete_entity filter coverage
# ======================================================================


class TestDeleteEntityFilter:
    @pytest.mark.asyncio
    async def test_filter_scopes_by_type_org_and_id(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entity("org-1", "record_group", "rg-1")

        vector_db_service.filter_collection.assert_awaited_once_with(
            must={
                "metadata.entityId": "rg-1",
                "metadata.entityType": "record_group",
                "metadata.orgId": "org-1",
            }
        )
        vector_db_service.delete_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_failure_is_logged_not_raised(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.delete_points = AsyncMock(side_effect=RuntimeError("db down"))
        store = _make_store(vector_db_service)

        await store.delete_entity("org-1", "record", "r1")  # must not raise


# ======================================================================
# Phase 2 — RECORD entities deleted immediately
# ======================================================================


class TestPhase2RecordEntities:
    @pytest.mark.asyncio
    async def test_record_entity_with_sole_connector_is_deleted(self) -> None:
        """A RECORD entity with only this connectorId is deleted outright."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("r1", "record", ["conn-a"], [])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record", "r1") in deleted
        vector_db_service.upsert_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_entity_deleted_even_with_record_group_ids(self) -> None:
        """A RECORD entity is deleted regardless of its recordGroupIds."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("r1", "record", ["conn-a"], ["rg-1"])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record", "r1") in deleted
        vector_db_service.upsert_points.assert_not_called()


# ======================================================================
# Phase 3 — RECORD_GROUP entities deleted, IDs collected
# ======================================================================


class TestPhase3RecordGroupEntities:
    @pytest.mark.asyncio
    async def test_record_group_entity_is_deleted(self) -> None:
        """A RECORD_GROUP entity is deleted outright."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("rg-1", "record_group", ["conn-a"], ["rg-1"])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record_group", "rg-1") in deleted
        vector_db_service.upsert_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_group_ids_collected_for_shared_entity_cleanup(self) -> None:
        """recordGroupIds from deleted RECORD_GROUP entities are stripped
        from shared taxonomy entities (phase 5 depends on phase 3 collection)."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    # Phase 3: record group entity — its rg-1 should be collected
                    _point("rg-1", "record_group", ["conn-a"], ["rg-1"]),
                    # Phase 5: shared category — has rg-1 (from conn-a) and rg-2 (from conn-b)
                    _point("cat-1", "category", ["conn-a", "conn-b"], ["rg-1", "rg-2"]),
                ],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        # record_group deleted
        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record_group", "rg-1") in deleted

        # shared category re-upserted with rg-1 stripped
        vector_db_service.upsert_points.assert_awaited_once()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]
        assert point.payload["recordGroupIds"] == ["rg-2"]


# ======================================================================
# Phase 4 — Exclusive taxonomy entities deleted
# ======================================================================


class TestPhase4ExclusiveTaxonomyEntities:
    @pytest.mark.asyncio
    async def test_exclusive_taxonomy_entity_deleted(self) -> None:
        """A CATEGORY with only this connectorId and no recordGroupIds is deleted."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("cat-1", "category", ["conn-a"], [])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("category", "cat-1") in deleted
        vector_db_service.upsert_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_exclusive_taxonomy_entity_deleted_even_with_record_group_ids(self) -> None:
        """A CATEGORY with only this connectorId but non-empty recordGroupIds
        is still deleted — those recordGroupIds belong to this connector."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("cat-1", "category", ["conn-a"], ["rg-1"])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("category", "cat-1") in deleted
        vector_db_service.upsert_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_exclusive_department_entity_deleted(self) -> None:
        """Verifies phase 4 works across entity types, not just categories."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("dept-1", "department", ["conn-a"], [])],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("department", "dept-1") in deleted
        vector_db_service.upsert_points.assert_not_called()


# ======================================================================
# Phase 5 — Shared taxonomy entities: strip connectorId + recordGroupIds
# ======================================================================


class TestPhase5SharedTaxonomyEntities:
    @pytest.mark.asyncio
    async def test_shared_taxonomy_entity_survives_with_connector_stripped(self) -> None:
        """A CATEGORY shared between two connectors survives with only the
        remaining connector's ID."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("cat-1", "category", ["conn-a", "conn-b"], [])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        # Not deleted — re-upserted with conn-a removed
        vector_db_service.upsert_points.assert_awaited_once()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]

    @pytest.mark.asyncio
    async def test_shared_entity_also_strips_deleted_connector_record_group_ids(self) -> None:
        """When a shared taxonomy entity carries recordGroupIds from the
        deleted connector, those IDs are also removed."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    _point("rg-1", "record_group", ["conn-a"], ["rg-1"]),
                    _point("cat-1", "category", ["conn-a", "conn-b"], ["rg-1", "rg-2"]),
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]
        assert point.payload["recordGroupIds"] == ["rg-2"]

    @pytest.mark.asyncio
    async def test_shared_entity_keeps_record_group_ids_from_other_connectors(self) -> None:
        """Only the deleted connector's recordGroupIds are removed; IDs
        belonging to other connectors are preserved."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    # rg-1 belongs to conn-a (collected in phase 3)
                    _point("rg-1", "record_group", ["conn-a"], ["rg-1"]),
                    # shared category has rg-1 (conn-a's) and rg-2, rg-3 (conn-b's)
                    _point(
                        "cat-1", "category",
                        ["conn-a", "conn-b"],
                        ["rg-1", "rg-2", "rg-3"],
                    ),
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["recordGroupIds"] == ["rg-2", "rg-3"]


# ======================================================================
# Mixed / integration scenarios
# ======================================================================


class TestMixedBatch:
    @pytest.mark.asyncio
    async def test_mixed_batch_records_groups_and_taxonomy(self) -> None:
        """A single scroll result containing RECORD, RECORD_GROUP, exclusive
        taxonomy, and shared taxonomy entities — all handled correctly."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    # Phase 2: record — deleted
                    _point("r1", "record", ["conn-a"], ["rg-1"]),
                    # Phase 3: record group — deleted, rg-1 collected
                    _point("rg-1", "record_group", ["conn-a"], ["rg-1"]),
                    # Phase 4: exclusive category — deleted
                    _point("cat-excl", "category", ["conn-a"], ["rg-1"]),
                    # Phase 5: shared category — stripped
                    _point("cat-shared", "category", ["conn-a", "conn-b"], ["rg-1", "rg-2"]),
                ],
                next_offset=None,
            )
        )
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record", "r1") in deleted
        assert ("record_group", "rg-1") in deleted
        assert ("category", "cat-excl") in deleted

        # Only the shared category should be re-upserted
        vector_db_service.upsert_points.assert_awaited_once()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["metadata"]["entityId"] == "cat-shared"
        assert point.payload["connectorIds"] == ["conn-b"]
        assert point.payload["recordGroupIds"] == ["rg-2"]

    @pytest.mark.asyncio
    async def test_shared_entity_with_no_connector_ids_left_but_other_record_groups_survives(
        self,
    ) -> None:
        """Edge case: after stripping connectorIds becomes empty but
        recordGroupIds from another source remains — the entity survives.

        This can happen if a taxonomy entity was referenced by records from
        two connectors but only one connector's ID was in connectorIds
        (e.g. a race during membership merge)."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    # shared category with only conn-a in connectorIds but rg-2
                    # from another connector in recordGroupIds
                    _point("cat-1", "category", ["conn-a"], ["rg-2"]),
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        # This is a taxonomy entity (not record/record_group), with only
        # conn-a → phase 4 deletes it since connectorIds is now empty.
        # rg-2 is from an unknown source but with no connectorId left,
        # the entity is unreachable.
        deleted = _deleted_entity_ids(vector_db_service)
        assert ("category", "cat-1") in deleted


# ======================================================================
# Preserved fields
# ======================================================================


class TestPreservedFields:
    @pytest.mark.asyncio
    async def test_shrink_preserves_subcategory_level(self) -> None:
        """`level` must survive the re-upsert — it is a filter key in
        search_entities and the entity resolver."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    _point(
                        "sub-1", "subcategory", ["conn-a", "conn-b"], [],
                        name="Budgets", canonicalName="budgets", level="1",
                    )
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["metadata"]["level"] == "1"
        assert point.payload["connectorIds"] == ["conn-b"]

    @pytest.mark.asyncio
    async def test_shrink_preserves_aliases(self) -> None:
        """`aliases` must survive the re-upsert."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    _point(
                        "cat-1", "category", ["conn-a", "conn-b"], [],
                        name="ML", aliases=["Machine Learning", "AI/ML"],
                    )
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["metadata"]["aliases"] == ["Machine Learning", "AI/ML"]


# ======================================================================
# Pagination
# ======================================================================


class TestPagination:
    @pytest.mark.asyncio
    async def test_scroll_pagination_processes_all_pages(self) -> None:
        """Both pages of scroll results are fully processed."""
        page_one = ScrollResult(
            points=[
                _point("r1", "record", ["conn-a"], []),
                _point("rg-1", "record_group", ["conn-a"], ["rg-1"]),
            ],
            next_offset="cursor-2",
        )
        page_two = ScrollResult(
            points=[
                _point("cat-1", "category", ["conn-a", "conn-b"], ["rg-1"]),
            ],
            next_offset=None,
        )
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(side_effect=[page_one, page_two])
        vector_db_service.delete_points = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        assert vector_db_service.scroll.await_count == 2

        # record and record_group deleted
        deleted = _deleted_entity_ids(vector_db_service)
        assert ("record", "r1") in deleted
        assert ("record_group", "rg-1") in deleted

        # shared category re-upserted with rg-1 stripped (collected from page 1)
        vector_db_service.upsert_points.assert_awaited_once()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]
        assert point.payload["recordGroupIds"] == []


# ======================================================================
# Merge safety
# ======================================================================


class TestMergeSafety:
    @pytest.mark.asyncio
    async def test_reupsert_uses_merge_membership_false(self) -> None:
        """The re-upsert for a shrunk entity must go through with
        merge_membership=False — otherwise upsert_entities_batch's normal
        union-merge would re-add the connector this call is removing."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("cat-1", "category", ["conn-a", "conn-b"], [])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        # merge_membership=False means no membership-merge scroll is issued
        # from within upsert_entities_batch — only the one scroll call from
        # _shrink_connector_membership itself.
        assert vector_db_service.scroll.await_count == 1


# ======================================================================
# Error handling
# ======================================================================


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_malformed_point_skipped_not_fatal(self) -> None:
        """A point with an unparseable typeCategory must not abort cleanup
        for the rest of the connector's entities."""
        bad_point = _point(
            "bad-1", "department", ["conn-a", "conn-b"], [],
            typeCategory="not-a-real-category",
        )
        good_point = _point("cat-1", "category", ["conn-a", "conn-b"], [])
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[bad_point, good_point], next_offset=None)
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        points = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert [p.payload["metadata"]["entityId"] for p in points] == ["cat-1"]

    @pytest.mark.asyncio
    async def test_reconcile_failure_logged_not_raised(self) -> None:
        """A db failure during the scroll must not propagate to the caller."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(side_effect=RuntimeError("db down"))
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")  # must not raise


# ======================================================================
# No-op
# ======================================================================


class TestNoop:
    @pytest.mark.asyncio
    async def test_no_entities_found_is_noop(self) -> None:
        """Empty scroll result → no deletes, no upserts."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        vector_db_service.upsert_points.assert_not_called()
        vector_db_service.delete_points.assert_not_called()


# ======================================================================
# UpsertEntitiesBatch merge_membership flag (existing coverage)
# ======================================================================


class TestUpsertEntitiesBatchMergeMembershipFlag:
    @pytest.mark.asyncio
    async def test_merge_membership_false_skips_membership_read(self) -> None:
        from app.models.entities import EntityRecord, EntityTypeCategory

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock()
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)
        entity = EntityRecord(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org-1",
            type_category=EntityTypeCategory.PREDEFINED,
            connector_ids=["conn-b"],
            record_group_ids=[],
        )

        await store.upsert_entities_batch([entity], merge_membership=False)

        vector_db_service.scroll.assert_not_called()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]

    @pytest.mark.asyncio
    async def test_merge_membership_default_true_preserves_existing_behaviour(self) -> None:
        from app.models.entities import EntityRecord, EntityTypeCategory

        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[
                    VectorPoint(
                        id="p1",
                        payload={
                            "metadata": {},
                            "connectorIds": ["conn-a"],
                            "recordGroupIds": [],
                        },
                    )
                ],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)
        entity = EntityRecord(
            entity_id="eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org-1",
            type_category=EntityTypeCategory.PREDEFINED,
            connector_ids=["conn-b"],
            record_group_ids=[],
        )

        await store.upsert_entities_batch([entity])

        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-a", "conn-b"]
