"""EntityVectorStore: replace-mode writes skip unchanged points, and
connector cleanup's final sweep leaves shared taxonomy points alone.

Uses a small stateful vector DB fake so a write is visible to the next read,
as it is on every real backend after ``upsert_points``.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import ScrollResult, VectorPoint
from tests.support.embedding_config import config_service as embedding_config_service
from tests.support.embedding_config import skip_bootstrap

ORG = "org-1"


class _StatefulVectorDB:
    """Just enough of IVectorDBService for the upsert path."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.upserts: list[list[VectorPoint]] = []
        self.payload_updates: list[tuple[list[str], dict[str, Any]]] = []
        self.fail_reads = False

    def get_capabilities(self) -> MagicMock:
        return MagicMock(supports_sparse_vectors=False)

    async def retrieve_points(self, collection: str, ids: list[str]) -> list[VectorPoint]:
        if self.fail_reads:
            raise RuntimeError("vector db down")
        return [VectorPoint(id=i, payload=dict(self.points[i])) for i in ids if i in self.points]

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        self.upserts.append(list(points))
        for point in points:
            self.points[point.id] = dict(point.payload)

    async def update_payload_by_ids(self, collection: str, ids: list[str], payload: dict) -> None:
        self.payload_updates.append((list(ids), dict(payload)))
        for point_id in ids:
            if point_id in self.points:
                self.points[point_id].update(payload)


def _store(db: _StatefulVectorDB) -> tuple[EntityVectorStore, MagicMock]:
    store = EntityVectorStore(
        logger=logging.getLogger("entity-store-test"),
        config_service=embedding_config_service(),
        vector_db_service=db,
    )
    skip_bootstrap(store)
    embed = MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts])
    store._dense_embeddings = MagicMock(embed_documents=embed)
    store._sparse_embedder = None
    return store, embed


def _record(name: str = "Q3 plan", group: str = "rg-1", connector: str = "c-1") -> EntityRecord:
    return EntityRecord(
        entity_id="rec-1",
        entity_type=EntityType.RECORD,
        name=name,
        org_id=ORG,
        connector_ids=[connector],
        record_group_ids=[group],
        type_category=EntityTypeCategory.PREDEFINED,
    )


def _group(group: str = "rg-1", name: str = "Roadmaps") -> EntityRecord:
    return EntityRecord(
        entity_id=group,
        entity_type=EntityType.RECORD_GROUP,
        name=name,
        org_id=ORG,
        connector_ids=["c-1"],
        record_group_ids=[group],
        type_category=EntityTypeCategory.PREDEFINED,
    )


def _stored(db: _StatefulVectorDB, entity: EntityRecord) -> dict[str, Any]:
    return db.points[EntityVectorStore._point_id(ORG, entity.entity_type.value, entity.entity_id)]


class TestReplaceModeSkipsUnchangedPoints:
    async def test_first_write_embeds_and_upserts(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_record()], merge_membership=False)
        assert embed.call_count == 1
        assert len(db.upserts) == 1

    async def test_same_record_again_makes_no_embedding_and_no_write(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_record()], merge_membership=False)
        await store.upsert_entities_batch([_record()], merge_membership=False)
        assert embed.call_count == 1
        assert len(db.upserts) == 1
        assert db.payload_updates == []

    async def test_record_group_shared_by_many_records_is_embedded_once(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        for _ in range(3):
            await store.upsert_entities_batch([_group()], merge_membership=False)
        assert embed.call_count == 1
        assert len(db.upserts) == 1

    async def test_moved_record_replaces_membership_without_embedding(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_record(group="rg-old")], merge_membership=False)
        await store.upsert_entities_batch([_record(group="rg-new")], merge_membership=False)
        assert embed.call_count == 1
        assert len(db.payload_updates) == 1
        assert _stored(db, _record())["recordGroupIds"] == ["rg-new"]

    async def test_renamed_record_is_reembedded(self) -> None:
        db = _StatefulVectorDB()
        store, embed = _store(db)
        await store.upsert_entities_batch([_record(name="Q3 plan")], merge_membership=False)
        await store.upsert_entities_batch([_record(name="Q3 plan (final)")], merge_membership=False)
        assert embed.call_count == 2
        assert _stored(db, _record())["page_content"] == "Q3 plan (final)"

    async def test_failed_read_still_writes_in_replace_mode(self, caplog) -> None:
        """Replace mode never merges, so the read is only an optimisation."""
        db = _StatefulVectorDB()
        store, embed = _store(db)
        db.fail_reads = True
        with caplog.at_level(logging.WARNING, logger="entity-store-test"):
            await store.upsert_entities_batch([_record()], merge_membership=False)
        assert embed.call_count == 1
        assert len(db.upserts) == 1

    async def test_failed_read_still_skips_the_batch_in_merge_mode(self) -> None:
        """Merging against an unknown state would drop stored membership."""
        db = _StatefulVectorDB()
        store, embed = _store(db)
        db.fail_reads = True
        await store.upsert_entities_batch([_group()])
        assert embed.call_count == 0
        assert db.upserts == []


# ---------------------------------------------------------------------------
# The final sweep must not delete shared taxonomy points
# ---------------------------------------------------------------------------


def _cleanup_store(scroll_pages: list[list[VectorPoint]]) -> tuple[EntityVectorStore, MagicMock]:
    db = MagicMock()
    db.get_capabilities.return_value = MagicMock(supports_sparse_vectors=False)
    db.collection_exists = AsyncMock(return_value=True)
    db.filter_collection = AsyncMock(side_effect=lambda **kw: kw)
    pages = list(scroll_pages)

    async def _scroll(**_kwargs: object) -> ScrollResult:
        return ScrollResult(points=pages.pop(0) if pages else [], next_offset=None)

    db.scroll = AsyncMock(side_effect=_scroll)
    db.delete_points = AsyncMock()
    db.set_payload = AsyncMock()
    store = EntityVectorStore(
        logger=logging.getLogger("entity-store-test"),
        config_service=embedding_config_service(),
        vector_db_service=db,
    )
    skip_bootstrap(store)
    return store, db


def _taxonomy_point(entity_id: str) -> VectorPoint:
    return VectorPoint(
        id=f"p-{entity_id}",
        payload={
            "metadata": {"entityId": entity_id, "entityType": "topic", "orgId": ORG},
            "connectorIds": ["c-gone", "c-other"],
            "recordGroupIds": [],
        },
    )


class TestFinalSweepSparesSharedTaxonomy:
    async def test_sweep_excludes_every_taxonomy_type(self) -> None:
        store, db = _cleanup_store(scroll_pages=[])
        await store.delete_entities_by_connector(ORG, "c-gone", record_group_ids=[])
        sweep = db.delete_points.await_args_list[-1].args[1]
        assert sweep["must"] == {"metadata.orgId": ORG, "connectorIds": "c-gone"}
        excluded = set(sweep["must_not"]["metadata.entityType"])
        assert {"category", "subcategory", "topic", "department", "language"} <= excluded
        assert not {"record", "record_group"} & excluded

    async def test_shared_point_retagged_during_cleanup_is_reported_not_deleted(
        self, caplog,
    ) -> None:
        """An in-flight record re-tags a shared topic with the deleted
        connector after the page loop drained; the sweep must leave it."""
        # Scrolls: record-group id scan, the page loop, then the post-sweep check.
        store, db = _cleanup_store(scroll_pages=[[], [], [_taxonomy_point("t-shared")]])
        with caplog.at_level(logging.WARNING, logger="entity-store-test"):
            await store.delete_entities_by_connector(ORG, "c-gone", record_group_ids=[])
        swept = db.delete_points.await_args_list[-1].args[1]
        assert "topic" in swept["must_not"]["metadata.entityType"]
        messages = [r.getMessage() for r in caplog.records]
        assert any("c-gone" in m and ORG in m and "still name" in m for m in messages)
