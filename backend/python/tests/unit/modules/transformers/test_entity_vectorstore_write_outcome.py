"""EntityVectorStore write outcomes and locking (KG-31, KG-07).

- Every upsert reports what happened to each entity (written, membership
  only, unchanged, skipped, failed), counts it in a metric, and logs the ids
  of failures, so a vector store outage is visible instead of quietly
  missing entities.
- Embedding happens before the per-entity locks are taken, so records that
  share a popular entity no longer wait on each other's embedding call. A
  point whose stored content changed between the first read and the lock is
  still embedded correctly.
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers.entity_vectorstore import (
    EMBEDDING_MODEL_FIELD,
    EntityVectorStore,
    EntityWriteOutcome,
)
from app.services.vector_db.models import VectorPoint

ORG = "org-1"
LOGGER = "entity-outcome-test"


class _DB:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.fail_upserts = False
        self.fail_reads = False
        self.on_read: list = []
        self.upserted: list[str] = []

    def get_capabilities(self) -> MagicMock:
        return MagicMock(supports_sparse_vectors=False)

    async def retrieve_points(self, collection: str, ids: list[str]) -> list[VectorPoint]:
        if self.fail_reads:
            raise RuntimeError("vector db down")
        if self.on_read:
            self.on_read.pop(0)()
        return [VectorPoint(id=i, payload=dict(self.points[i])) for i in ids if i in self.points]

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        if self.fail_upserts:
            raise RuntimeError("write failed")
        for p in points:
            self.points[p.id] = dict(p.payload)
            self.upserted.append(p.id)

    async def update_payload_by_ids(self, collection: str, ids: list[str], payload: dict) -> None:
        for i in ids:
            self.points[i].update(payload)


def _store(db: _DB) -> EntityVectorStore:
    store = EntityVectorStore(logger=logging.getLogger(LOGGER), config_service=MagicMock(), vector_db_service=db)
    store._initialized = True
    store._model_id, store._embedding_size = "m", 2
    store._dense_embeddings = MagicMock(embed_documents=MagicMock(side_effect=lambda texts: [[0.1, 0.2] for _ in texts]))
    store._sparse_embedder = None
    return store


def _topic(entity_id: str, name: str | None = None, connectors: tuple[str, ...] = ("c1",)) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id, entity_type=EntityType.TOPIC, name=name if name is not None else f"Topic {entity_id}",
        org_id=ORG, connector_ids=list(connectors), type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
    )


def _pid(entity_id: str) -> str:
    return EntityVectorStore._point_id(ORG, "topic", entity_id)


class TestOutcome:
    async def test_each_entity_is_counted_once_by_what_happened(self) -> None:
        db = _DB()
        store = _store(db)
        await store.upsert_entities_batch([_topic("same"), _topic("moved")], merge_membership=False)
        outcome = await store.upsert_entities_batch(
            [_topic("same"), _topic("moved", connectors=("c2",)), _topic("new"), _topic("blank", name=" ")],
            merge_membership=False,
        )
        assert outcome == EntityWriteOutcome(written=1, membership_only=1, unchanged=1, skipped=1, failed=0)

    async def test_a_failed_batch_counts_its_entities_and_logs_their_ids(self, caplog) -> None:
        db = _DB()
        db.fail_upserts = True
        store = _store(db)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            outcome = await store.upsert_entities_batch([_topic("a"), _topic("b")])
        assert outcome.failed == 2 and outcome.written == 0
        assert any("topic/a" in r.getMessage() and "topic/b" in r.getMessage() for r in caplog.records)

    async def test_logged_ids_are_capped(self, caplog) -> None:
        db = _DB()
        db.fail_upserts = True
        store = _store(db)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            outcome = await store.upsert_entities_batch([_topic(f"t{i}") for i in range(50)], batch_size=100)
        assert outcome.failed == 50
        message = next(r.getMessage() for r in caplog.records if "not written" in r.getMessage())
        assert "t49" not in message and "and 30 more" in message

    async def test_unknown_membership_in_merge_mode_is_a_failure(self) -> None:
        db = _DB()
        db.fail_reads = True
        outcome = await _store(db).upsert_entities_batch([_topic("a")])
        assert outcome.failed == 1

    async def test_outcomes_are_counted_in_metrics(self) -> None:
        db = _DB()
        db.fail_upserts = True
        with patch("app.modules.transformers.entity_vectorstore.entity_index_metrics") as metrics:
            await _store(db).upsert_entities_batch([_topic("a"), _topic("b", name="")])
        calls = {(c.args[0], c.args[1]): c.args[2] for c in metrics.record_writes.call_args_list}
        assert calls[("upsert", "failed")] == 1 and calls[("upsert", "skipped")] == 1

    async def test_empty_input_is_an_empty_outcome(self) -> None:
        assert await _store(_DB()).upsert_entities_batch([]) == EntityWriteOutcome()


class TestEmbeddingOutsideTheLocks:
    async def test_no_entity_lock_is_held_while_embedding(self) -> None:
        db = _DB()
        store = _store(db)
        held: list[bool] = []

        def _embed(texts: list[str]) -> list[list[float]]:
            held.extend(store._entity_lock(store._entity_key(ORG, "topic", e)).locked() for e in ("a", "b"))
            return [[0.1, 0.2] for _ in texts]

        store._dense_embeddings = MagicMock(embed_documents=MagicMock(side_effect=_embed))
        await store.upsert_entities_batch([_topic("a"), _topic("b")])
        assert held and not any(held)

    async def test_content_changed_before_the_lock_is_embedded_under_it(self) -> None:
        """First read: unchanged, so nothing is embedded up front. Another
        writer then changes the stored text; the locked re-read sees it and
        the point is embedded and rewritten."""
        db = _DB()
        store = _store(db)
        await store.upsert_entities_batch([_topic("a")], merge_membership=False)
        embed = store._dense_embeddings.embed_documents
        embed.reset_mock()

        def _someone_renames_it() -> None:
            db.points[_pid("a")]["page_content"] = "Renamed elsewhere"

        db.on_read = [lambda: None, _someone_renames_it]  # pre-read, then locked re-read
        outcome = await store.upsert_entities_batch([_topic("a")], merge_membership=False)
        assert outcome.written == 1
        assert embed.call_count == 1
        assert db.points[_pid("a")]["page_content"] == "Topic a"

    async def test_vectors_embedded_up_front_are_used_without_a_second_call(self) -> None:
        db = _DB()
        store = _store(db)
        outcome = await store.upsert_entities_batch([_topic("a"), _topic("b")])
        assert outcome.written == 2
        assert store._dense_embeddings.embed_documents.call_count == 1
        assert db.points[_pid("a")]["metadata"][EMBEDDING_MODEL_FIELD] == "m:2"

    async def test_a_point_written_identically_meanwhile_is_not_rewritten(self) -> None:
        """New at the first read (so embedded up front), but another writer
        stored the same content before the lock: nothing is written."""
        db = _DB()
        store = _store(db)

        def _same_content_written_elsewhere() -> None:
            db.points[_pid("a")] = {
                "page_content": "Topic a",
                "metadata": {**_topic("a").to_vector_payload(), EMBEDDING_MODEL_FIELD: "m:2"},
                "connectorIds": ["c1"], "recordGroupIds": [],
            }

        db.on_read = [lambda: None, _same_content_written_elsewhere]
        outcome = await store.upsert_entities_batch([_topic("a")], merge_membership=False)
        assert outcome == EntityWriteOutcome(unchanged=1)
        assert db.upserted == []


@pytest.mark.parametrize("merge", [True, False])
async def test_membership_still_merges_against_the_locked_read(merge: bool) -> None:
    """The membership merge must use the state read under the lock, not the
    early read, or a write made between them would be dropped."""
    db = _DB()
    store = _store(db)
    await store.upsert_entities_batch([_topic("a", connectors=("c1",))])

    def _another_connector_meanwhile() -> None:
        db.points[_pid("a")]["connectorIds"] = ["c1", "c9"]

    db.on_read = [lambda: None, _another_connector_meanwhile]
    await store.upsert_entities_batch([_topic("a", connectors=("c2",))], merge_membership=merge)
    expected = ["c1", "c9", "c2"] if merge else ["c2"]
    assert db.points[_pid("a")]["connectorIds"] == expected


async def test_a_failed_write_counts_only_the_entities_it_left_unwritten(caplog) -> None:
    """The membership-only write landed before the upsert failed; that entity
    is not also counted (and logged) as failed."""
    db = _DB()
    store = _store(db)
    await store.upsert_entities_batch([_topic("same"), _topic("moved")], merge_membership=False)
    db.fail_upserts = True
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        outcome = await store.upsert_entities_batch(
            [_topic("same"), _topic("moved", connectors=("c2",)), _topic("new")], merge_membership=False,
        )
    assert outcome == EntityWriteOutcome(membership_only=1, unchanged=1, failed=1)
    message = next(r.getMessage() for r in caplog.records if "not written" in r.getMessage())
    assert "topic/new" in message and "topic/moved" not in message and "topic/same" not in message
