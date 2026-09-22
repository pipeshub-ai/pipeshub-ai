"""Unit tests for EntityVectorStore's deletion paths: the entityType filter
fix on ``delete_entity``, and the shrink-vs-delete membership reconciliation
in ``delete_entities_by_connector``/``_shrink_connector_membership``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


def _point(entity_id: str, entity_type: str, connector_ids: list[str], record_group_ids: list[str], **extra) -> VectorPoint:
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


class TestDeleteEntityFilter:
    @pytest.mark.asyncio
    async def test_filter_scopes_by_type_org_and_id(self) -> None:
        """entity_type must be part of the delete filter, not just entity_id
        + org_id — otherwise a different-typed entity that happened to reuse
        the same graph-node key would also match."""
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


class TestDeleteEntitiesByConnectorSharedMembership:
    """A taxonomy/record-group entity referenced by more than one connector
    must survive connector disconnect with only that connector's id removed
    — not be deleted outright, which would silently drop the other
    connector's membership too.
    """

    @pytest.mark.asyncio
    async def test_shared_entity_survives_with_other_connector_membership_intact(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("eng", "department", ["conn-a", "conn-b"], [])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        vector_db_service.delete_points.assert_not_called()
        vector_db_service.upsert_points.assert_awaited_once()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == ["conn-b"]

    @pytest.mark.asyncio
    async def test_entity_with_no_membership_left_is_deleted_outright(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("r1", "record", ["conn-a"], [])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        vector_db_service.upsert_points.assert_not_called()
        vector_db_service.delete_points.assert_awaited_once()
        # delete_entity's filter must carry the entity's own type, not a hardcoded one.
        assert vector_db_service.filter_collection.call_args.kwargs["must"] == {
            "metadata.entityId": "r1",
            "metadata.entityType": "record",
            "metadata.orgId": "org-1",
        }

    @pytest.mark.asyncio
    async def test_entity_with_remaining_record_group_membership_survives(self) -> None:
        """Removing the connector can leave connectorIds empty while
        recordGroupIds is still populated — that entity is still reachable
        and must be shrunk, not deleted."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("rg-1", "record_group", ["conn-a"], ["rg-1"])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        vector_db_service.delete_points.assert_not_called()
        (point,) = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert point.payload["connectorIds"] == []
        assert point.payload["recordGroupIds"] == ["rg-1"]

    @pytest.mark.asyncio
    async def test_shrink_write_does_not_re_merge_removed_connector(self) -> None:
        """The re-upsert for a shrunk entity must go through with
        merge_membership=False — otherwise upsert_entities_batch's normal
        union-merge would read the still-present old point and immediately
        re-add the connector this call is removing."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(
                points=[_point("eng", "department", ["conn-a", "conn-b"], [])],
                next_offset=None,
            )
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        # merge_membership=False means no membership-merge read (scroll) is
        # issued from within upsert_entities_batch itself — only the one
        # scroll call this method made to enumerate connector-scoped points.
        assert vector_db_service.scroll.await_count == 1

    @pytest.mark.asyncio
    async def test_unrelated_entity_is_not_touched(self) -> None:
        """The scroll filter itself is scoped to this connector — an entity
        with no membership for it is out of scope entirely."""
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )
        vector_db_service.upsert_points = AsyncMock()
        vector_db_service.delete_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        vector_db_service.filter_collection.assert_any_call(
            must={"metadata.orgId": "org-1", "connectorIds": "conn-a"}
        )
        vector_db_service.upsert_points.assert_not_called()
        vector_db_service.delete_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_scroll_pagination_is_followed_to_completion(self) -> None:
        page_one = ScrollResult(
            points=[_point("eng", "department", ["conn-a"], ["rg-1"])],
            next_offset="cursor-2",
        )
        page_two = ScrollResult(
            points=[_point("sales", "department", ["conn-a"], ["rg-2"])],
            next_offset=None,
        )
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(side_effect=[page_one, page_two])
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        assert vector_db_service.scroll.await_count == 2
        points = vector_db_service.upsert_points.call_args.kwargs["points"]
        # Both pages' entities were re-upserted in one batch call.
        assert vector_db_service.upsert_points.await_count == 1
        assert len(points) == 2

    @pytest.mark.asyncio
    async def test_malformed_point_is_skipped_not_fatal(self) -> None:
        """A point with an unparseable typeCategory must not abort cleanup
        for the rest of the connector's entities."""
        bad_point = _point("eng", "department", ["conn-a"], [], typeCategory="not-a-real-category")
        good_point = _point("sales", "department", ["conn-a"], ["rg-1"])
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(return_value={"must": []})
        vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[bad_point, good_point], next_offset=None)
        )
        vector_db_service.upsert_points = AsyncMock()
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")

        points = vector_db_service.upsert_points.call_args.kwargs["points"]
        assert [p.payload["metadata"]["entityId"] for p in points] == ["sales"]

    @pytest.mark.asyncio
    async def test_reconcile_failure_is_logged_not_raised(self) -> None:
        vector_db_service = MagicMock()
        vector_db_service.filter_collection = AsyncMock(side_effect=RuntimeError("db down"))
        store = _make_store(vector_db_service)

        await store.delete_entities_by_connector(org_id="org-1", connector_id="conn-a")  # must not raise


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
