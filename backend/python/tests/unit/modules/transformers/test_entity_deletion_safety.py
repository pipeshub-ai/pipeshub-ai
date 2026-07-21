"""
test_entity_deletion_safety.py — Tests ensuring shared entities survive partial connector disconnects.

Matches plan section: "6. Cross-Connector Deletion Safety (test_entity_deletion_safety.py)"
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evs(*, supports_sparse: bool = False):
    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    caps = MagicMock(spec=VectorDBCapabilities)
    caps.supports_sparse_vectors = supports_sparse

    evs = EntityVectorStore.__new__(EntityVectorStore)
    evs.logger = MagicMock()
    evs.collection_name = "entities"
    evs.vector_db_service = MagicMock()
    evs.vector_db_service.capabilities = caps
    evs._dense_embeddings = MagicMock()
    evs._sparse_embeddings = None
    evs._embed = AsyncMock()
    evs._embed_sparse = AsyncMock(return_value=[None])
    evs._initialized = True  # skip lazy init
    return evs


def _scroll_result(points=None, next_offset=None):
    from app.services.vector_db.models import ScrollResult, VectorPoint

    vpoints = []
    for p in (points or []):
        vp = VectorPoint(
            id=p["id"],
            dense_vector=[0.1] * 384,
            payload={"metadata": p["metadata"]},
        )
        vpoints.append(vp)
    return ScrollResult(points=vpoints, next_offset=next_offset)


# ===========================================================================
# TestCrossConnectorEntityDeletion
# ===========================================================================


class TestCrossConnectorEntityDeletion:

    @pytest.mark.asyncio
    async def test_shared_person_survives_single_connector_disconnect(self):
        """Person referenced by Drive + Jira: disconnect Jira -> entity persists via overwrite_payload."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        meta = {
            "entityId": "person_bob",
            "entityType": "person",
            "sourceConnectors": ["drive_1", "jira_2"],
            "entityCount": 5,
        }
        evs.vector_db_service.scroll = AsyncMock(
            return_value=_scroll_result(
                points=[{"id": "pt_bob", "metadata": meta}],
                next_offset=None,
            )
        )
        evs.vector_db_service.overwrite_payload = AsyncMock()
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.remove_all_connector_references(org_id="org1", connector_id="jira_2")

        # Entity should be updated (overwrite_payload), not deleted
        evs.vector_db_service.overwrite_payload.assert_awaited()
        # delete_points called by delete_entity internally only if deleting — here should NOT be called
        # Note: filter_collection IS called for overwrite_payload, but not delete_points directly
        call = evs.vector_db_service.overwrite_payload.call_args
        updated_metadata = call.kwargs.get("payload", {}).get("metadata", {})
        assert "jira_2" not in updated_metadata.get("sourceConnectors", [])
        assert "drive_1" in updated_metadata.get("sourceConnectors", [])

    @pytest.mark.asyncio
    async def test_orphaned_entity_removed_after_all_references_gone(self):
        """Last connector AND entityCount=0 -> delete_entity is called."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        meta = {
            "entityId": "person_orphan",
            "entityType": "person",
            "sourceConnectors": ["jira_only"],
            "entityCount": 0,
        }
        evs.vector_db_service.scroll = AsyncMock(
            return_value=_scroll_result(
                points=[{"id": "pt_orphan", "metadata": meta}],
                next_offset=None,
            )
        )
        evs.vector_db_service.overwrite_payload = AsyncMock()
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.remove_all_connector_references(org_id="org1", connector_id="jira_only")

        # delete_entity delegates to delete_points
        evs.vector_db_service.delete_points.assert_awaited()
        evs.vector_db_service.overwrite_payload.assert_not_called()

    @pytest.mark.asyncio
    async def test_entity_with_records_not_deleted_even_if_last_connector(self):
        """entityCount > 0 -> entity kept even if last connector disconnects."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        meta = {
            "entityId": "topic_security",
            "entityType": "topic",
            "sourceConnectors": ["conn_only"],
            "entityCount": 12,  # still has records
        }
        evs.vector_db_service.scroll = AsyncMock(
            return_value=_scroll_result(
                points=[{"id": "pt_sec", "metadata": meta}],
                next_offset=None,
            )
        )
        evs.vector_db_service.overwrite_payload = AsyncMock()
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.remove_all_connector_references(org_id="org1", connector_id="conn_only")

        # Must NOT delete because entityCount > 0
        evs.vector_db_service.delete_points.assert_not_called()
        evs.vector_db_service.overwrite_payload.assert_awaited()

    @pytest.mark.asyncio
    async def test_entity_not_referencing_connector_is_left_unchanged(self):
        """Entity that never referenced the disconnected connector is skipped."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        meta = {
            "entityId": "cat_finance",
            "entityType": "category",
            "sourceConnectors": ["drive_1"],  # no reference to "jira_2"
            "entityCount": 3,
        }
        evs.vector_db_service.scroll = AsyncMock(
            return_value=_scroll_result(
                points=[{"id": "pt_fin", "metadata": meta}],
                next_offset=None,
            )
        )
        evs.vector_db_service.overwrite_payload = AsyncMock()
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.remove_all_connector_references(org_id="org1", connector_id="jira_2")

        evs.vector_db_service.overwrite_payload.assert_not_called()
        evs.vector_db_service.delete_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_all_iterates_paginated_results(self):
        """Pagination: both scroll pages are processed."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())

        meta_page1 = {"entityId": "e1", "entityType": "person", "sourceConnectors": ["conn_x"], "entityCount": 0}
        meta_page2 = {"entityId": "e2", "entityType": "person", "sourceConnectors": ["conn_x"], "entityCount": 0}

        scroll_calls = [
            _scroll_result(
                points=[{"id": "pt1", "metadata": meta_page1}],
                next_offset="cursor_1",
            ),
            _scroll_result(
                points=[{"id": "pt2", "metadata": meta_page2}],
                next_offset=None,
            ),
        ]
        evs.vector_db_service.scroll = AsyncMock(side_effect=scroll_calls)
        evs.vector_db_service.delete_points = AsyncMock()
        evs.vector_db_service.overwrite_payload = AsyncMock()

        await evs.remove_all_connector_references(org_id="org1", connector_id="conn_x")

        # Both entities are orphaned (entityCount=0) so delete_entity is called twice
        assert evs.vector_db_service.delete_points.await_count == 2

    @pytest.mark.asyncio
    async def test_record_reindex_does_not_create_duplicate_connector_refs(self):
        """Upserting the same entity twice with the same connector should not duplicate sourceConnectors."""
        from app.models.entities import EntityRecord, EntityType

        evs = _make_evs()
        evs.vector_db_service.upsert_points = AsyncMock()

        entity = EntityRecord(
            entity_id="cat_eng",
            entity_type=EntityType.CATEGORY,
            name="Engineering",
            org_id="org1",
            source_connectors=["drive_1"],  # connector referenced once
        )

        evs._embed = AsyncMock(return_value=[[0.1] * 384])
        evs._embed_sparse = AsyncMock(return_value=[None])

        await evs.upsert_entities_batch([entity])
        await evs.upsert_entities_batch([entity])

        # Both calls go through; the UPSERT semantics ensure idempotency at the vector DB level
        assert evs.vector_db_service.upsert_points.await_count == 2

        # The payload in each call should have sourceConnectors without duplicates
        for call_obj in evs.vector_db_service.upsert_points.call_args_list:
            points = call_obj.kwargs.get("points") or call_obj.args[1]
            connectors = points[0].payload["metadata"]["sourceConnectors"]
            # Should be exactly one entry for drive_1
            assert connectors.count("drive_1") == 1


# ===========================================================================
# Concurrent disconnect safety
# ===========================================================================


class TestConcurrentDisconnectSafety:

    @pytest.mark.asyncio
    async def test_concurrent_connector_disconnects_do_not_raise(self):
        """Two connectors disconnect simultaneously -> no unhandled exceptions."""
        evs = _make_evs()

        meta = {
            "entityId": "person_shared",
            "entityType": "person",
            "sourceConnectors": ["conn_A", "conn_B"],
            "entityCount": 0,
        }

        call_count = 0

        async def paginated_scroll(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _scroll_result(
                points=[{"id": f"pt_{call_count}", "metadata": meta}],
                next_offset=None,
            )

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(side_effect=paginated_scroll)
        evs.vector_db_service.overwrite_payload = AsyncMock()
        evs.vector_db_service.delete_points = AsyncMock()

        # Simulate concurrent disconnects
        await asyncio.gather(
            evs.remove_all_connector_references("org1", "conn_A"),
            evs.remove_all_connector_references("org1", "conn_B"),
        )

        # No exception should be raised; assertions on final state depend on execution order
        assert evs.vector_db_service.filter_collection.await_count >= 2
