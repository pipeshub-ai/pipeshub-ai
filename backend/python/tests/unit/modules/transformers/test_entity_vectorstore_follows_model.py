"""A running EntityVectorStore follows an embedding model switch.

The store used to read the model config once per process, so after an admin
switched model the indexing service kept writing entity points with the old
one until it restarted, and the rebuild marker kept the old fingerprint
(nightly 37229085638 on ArangoDB: ``default:BAAI/bge-large-en-v1.5:1024``
after the switch to text-embedding-3-small). The query service likewise kept
embedding queries with the model it started with.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.config.constants.ai_models import DEFAULT_EMBEDDING_MODEL
from app.exceptions.indexing_exceptions import VectorStoreError
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers import entity_vectorstore as module
from app.modules.transformers.entity_vectorstore import (
    EMBEDDING_MODEL_FIELD,
    EntityVectorStore,
)
from tests.support.embedding_config import (
    another_process,
    config_service,
    embedding_config,
    switch_embedding_model,
)
from tests.support.entity_vector_db import (
    FakeEmbeddingModel,
    FakeEntityVectorDB,
    embedding_models,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.config.configuration_service import ConfigurationService

ORG = "org-1"
BGE = FakeEmbeddingModel(1.0, 4)
SMALL = FakeEmbeddingModel(2.0, 6)
ADA = FakeEmbeddingModel(3.0, 6)
SMALL_CONFIG = embedding_config("openAI", "text-embedding-3-small")
ADA_CONFIG = embedding_config("openAI", "text-embedding-ada-002")
BROKEN_CONFIG = embedding_config("openAI", "broken")
BGE_FP = f"default:{DEFAULT_EMBEDDING_MODEL}:4"
SMALL_FP = "openAI:text-embedding-3-small:6"
ADA_FP = "openAI:text-embedding-ada-002:6"


class _EndpointDown(FakeEmbeddingModel):
    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")


class _Held(FakeEmbeddingModel):
    """Blocks document embedding until released, once armed."""

    def __init__(self, value: float, dimension: int) -> None:
        super().__init__(value, dimension)
        self.armed = False
        self.started, self.release = threading.Event(), threading.Event()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.armed:
            self.started.set()
            self.release.wait(5)
        return super().embed_documents(texts)


MODELS: dict[str, FakeEmbeddingModel] = {
    "text-embedding-3-small": SMALL,
    "text-embedding-ada-002": ADA,
    "broken": _EndpointDown(0.0, 6),
}


@pytest.fixture(autouse=True)
def _models() -> Iterator[None]:
    with embedding_models(MODELS), patch.object(module, "get_default_embedding_model", return_value=BGE):
        yield


def _store(db: FakeEntityVectorDB, config: ConfigurationService, *, owner: bool = True) -> EntityVectorStore:
    return EntityVectorStore(
        logger=logging.getLogger("entity-model-test"), config_service=config,
        vector_db_service=db, recreate_on_dimension_mismatch=owner,
    )


def _topic(entity_id: str = "t1") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id, entity_type=EntityType.TOPIC, name=f"Pricing {entity_id}", org_id=ORG,
        connector_ids=["c1"], type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
    )


async def _leader_tick(store: EntityVectorStore) -> str:
    """What the rebuild leader's tick does first: the one call allowed to
    drop a collection another model wrote."""
    return await store.embedding_fingerprint(recreate=True)


def _models_of(db: FakeEntityVectorDB) -> dict[str, tuple[str, float]]:
    return {
        point.payload["metadata"]["entityId"]: (point.payload["metadata"][EMBEDDING_MODEL_FIELD], point.dense_vector[0])
        for point in db.points.values()
    }


class TestWrites:
    async def test_a_switch_reaches_the_next_write(self) -> None:
        """The nightly's case: default model, then text-embedding-3-small."""
        db, config = FakeEntityVectorDB(), config_service()
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert _models_of(db) == {"t1": (BGE_FP, BGE.value)}

        await switch_embedding_model(config, SMALL_CONFIG)
        with pytest.raises(VectorStoreError, match="dimension 4"):
            await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert await _leader_tick(store) == SMALL_FP
        outcome = await store.upsert_entities_batch([_topic()], merge_membership=False)

        assert outcome.written == 1
        assert _models_of(db) == {"t1": (SMALL_FP, SMALL.value)}
        assert db.dimension == SMALL.dimension
        assert await store.embedding_fingerprint() == SMALL_FP

    async def test_a_same_dimension_switch_recreates_the_collection(self) -> None:
        """Re-embedding in place would leave the old model's points answering
        new-model queries until the rebuild reached them."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic("t1"), _topic("t2")], merge_membership=False)

        await switch_embedding_model(config, ADA_CONFIG)
        assert await _leader_tick(store) == ADA_FP
        await store.upsert_entities_batch([_topic("t1")], merge_membership=False)

        assert db.deletions == 1
        assert _models_of(db) == {"t1": (ADA_FP, ADA.value)}

    async def test_only_the_rebuild_leader_drops_the_collection(self) -> None:
        """Every indexing replica owns the collection, but one dropping it
        after another had refilled part of it would lose those points."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)

        await switch_embedding_model(config, ADA_CONFIG)
        with pytest.raises(VectorStoreError, match="embedded by openAI:text-embedding-3-small"):
            await store.embedding_fingerprint()
        assert db.deletions == 0
        assert _models_of(db) == {"t1": (SMALL_FP, SMALL.value)}

    async def test_points_from_before_the_model_was_recorded_keep_the_collection(self) -> None:
        """Their model is unknown; the rebuild re-embeds them in place."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        for point in db.points.values():
            del point.payload["metadata"][EMBEDDING_MODEL_FIELD]

        fresh = _store(db, config)
        assert await fresh.embedding_fingerprint() == SMALL_FP
        assert db.deletions == 0

    async def test_a_write_embedded_across_the_switch_is_not_stored(self) -> None:
        held = _Held(SMALL.value, SMALL.dimension)
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        with embedding_models({**MODELS, "text-embedding-3-small": held}):
            assert await store.embedding_fingerprint() == SMALL_FP
            held.armed = True
            write = asyncio.create_task(store.upsert_entities_batch([_topic()], merge_membership=False))
            assert await asyncio.to_thread(held.started.wait, 5)
            await switch_embedding_model(config, ADA_CONFIG)
            assert await store.embedding_fingerprint() == ADA_FP
            held.release.set()
            outcome = await write

        assert (outcome.written, outcome.failed) == (0, 1)
        assert db.points == {}
        await store.upsert_entities_batch([_topic()], merge_membership=False)
        assert _models_of(db) == {"t1": (ADA_FP, ADA.value)}

    async def test_a_new_model_that_cannot_start_stops_writes_until_fixed(self) -> None:
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic("t1")], merge_membership=False)

        await switch_embedding_model(config, BROKEN_CONFIG)
        with pytest.raises(RuntimeError, match="endpoint down"):
            await store.upsert_entities_batch([_topic("t2")], merge_membership=False)
        assert set(_models_of(db)) == {"t1"}

        # A corrected config is tried at once, not after the retry window.
        await switch_embedding_model(config, ADA_CONFIG)
        assert await _leader_tick(store) == ADA_FP
        await store.upsert_entities_batch([_topic("t2")], merge_membership=False)
        assert _models_of(db)["t2"] == (ADA_FP, ADA.value)


class TestQueries:
    async def test_a_query_store_embeds_queries_with_the_new_model(self) -> None:
        db, config = FakeEntityVectorDB(SMALL.dimension), config_service(SMALL_CONFIG)
        reader = _store(db, config, owner=False)
        await reader.search_entities("pricing", ORG, set(), {"c1"})

        await switch_embedding_model(config, ADA_CONFIG)
        await reader.search_entities("pricing", ORG, set(), {"c1"})

        assert [request.dense_query[0] for request in db.searches] == [SMALL.value, ADA.value]
        assert db.deletions == 0


    async def test_a_query_store_waits_while_the_collection_holds_the_old_model(self, monkeypatch) -> None:
        """Same dimension, so the old vectors would answer without an error."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        indexing = _store(db, config)
        await indexing.upsert_entities_batch([_topic()], merge_membership=False)
        reader = _store(db, config, owner=False)
        await reader.search_entities("pricing", ORG, set(), {"c1"})

        await switch_embedding_model(config, ADA_CONFIG)
        with pytest.raises(VectorStoreError, match="indexing service recreates it"):
            await reader.search_entities("pricing", ORG, set(), {"c1"})
        assert db.deletions == 0

        assert await _leader_tick(indexing) == ADA_FP
        monkeypatch.setattr(module, "_INIT_RETRY_SECONDS", 0.0)
        await reader.search_entities("pricing", ORG, set(), {"c1"})
        assert db.searches[-1].dense_query[0] == ADA.value


class TestReplicas:
    """Two indexing processes over one collection, each with its own config
    cache, as Helm's default two replicas run."""

    async def test_a_replica_with_a_stale_model_cannot_overwrite_the_new_points(self) -> None:
        db, config_a = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        config_b = another_process(config_a)
        leader, other = _store(db, config_a), _store(db, config_b)
        await leader.upsert_entities_batch([_topic()], merge_membership=False)
        await other.upsert_entities_batch([_topic("t2")], merge_membership=False)

        # B misses the notification, so its cache still names the old model.
        await switch_embedding_model(config_a, ADA_CONFIG)
        assert await _leader_tick(leader) == ADA_FP
        await leader.upsert_entities_batch([_topic()], merge_membership=False)

        outcome = await other.upsert_entities_batch([_topic()], merge_membership=False)

        assert (outcome.written, outcome.failed) == (0, 1)
        assert _models_of(db) == {"t1": (ADA_FP, ADA.value)}

    async def test_the_refused_replica_follows_the_new_model_next(self) -> None:
        db, config_a = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        config_b = another_process(config_a)
        leader, other = _store(db, config_a), _store(db, config_b)
        await other.upsert_entities_batch([_topic("t2")], merge_membership=False)
        await switch_embedding_model(config_a, ADA_CONFIG)
        assert await _leader_tick(leader) == ADA_FP

        assert (await other.upsert_entities_batch([_topic()], merge_membership=False)).failed == 1
        await other.upsert_entities_batch([_topic()], merge_membership=False)
        assert _models_of(db) == {"t1": (ADA_FP, ADA.value)}


    async def test_a_stale_replica_that_cannot_read_the_config_does_not_write(self) -> None:
        """The case the write check exists for, with the store unreachable at
        the moment of the write: the stale replica must not assume its model."""
        db, config_a = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        config_b = another_process(config_a)
        leader, other = _store(db, config_a), _store(db, config_b)
        await other.upsert_entities_batch([_topic("t2")], merge_membership=False)
        await switch_embedding_model(config_a, ADA_CONFIG)
        assert await _leader_tick(leader) == ADA_FP
        await leader.upsert_entities_batch([_topic()], merge_membership=False)

        with patch.object(config_b.store, "get_key", side_effect=RuntimeError("kv store timed out")):
            outcome = await other.upsert_entities_batch([_topic()], merge_membership=False)

        assert (outcome.written, outcome.failed) == (0, 1)
        assert _models_of(db) == {"t1": (ADA_FP, ADA.value)}


class TestRecreateOnSwitch:
    async def test_a_failed_point_read_fails_the_switch_and_is_retried(self, monkeypatch) -> None:
        """An unread collection may still hold the old model's vectors, so the
        new model is not adopted on the strength of a failed read."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)

        await switch_embedding_model(config, ADA_CONFIG)
        with patch.object(db, "scroll", side_effect=RuntimeError("scroll timed out")):
            with pytest.raises(RuntimeError, match="scroll timed out"):
                await store.embedding_fingerprint()
            assert store._initialized is False
            with pytest.raises(VectorStoreError, match="retrying later"):
                await store.embedding_fingerprint()
        assert db.deletions == 0

        monkeypatch.setattr(module, "_INIT_RETRY_SECONDS", 0.0)
        assert await _leader_tick(store) == ADA_FP
        assert (db.deletions, db.points) == (1, {})

    async def test_the_config_version_changes_without_initialising(self) -> None:
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        before = await store.embedding_config_version()
        await switch_embedding_model(config, ADA_CONFIG)
        assert await store.embedding_config_version() not in (None, before)
        assert db.dimension is None


class TestConfigReads:
    async def test_calls_read_the_cached_config_not_the_store(self) -> None:
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        reads = 0
        real_get_key = config.store.get_key

        async def _counting_get_key(key: str, **kwargs: object) -> object:
            nonlocal reads
            reads += 1
            return await real_get_key(key, **kwargs)

        with patch.object(config.store, "get_key", side_effect=_counting_get_key):
            for _ in range(3):
                await store.search_entities("pricing", ORG, set(), {"c1"})
            assert reads == 1
            # A write re-reads the store once per batch, before it upserts.
            for entity_id in ("t1", "t2", "t3"):
                await store.upsert_entities_batch([_topic(entity_id)], merge_membership=False)
            assert reads == 4

    async def test_a_missed_notification_is_caught_by_the_periodic_read(self, monkeypatch) -> None:
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)

        await switch_embedding_model(config, ADA_CONFIG, notify=False)
        assert await store.embedding_fingerprint() == SMALL_FP

        monkeypatch.setattr(module, "_CONFIG_RECHECK_SECONDS", 0.0)
        assert await _leader_tick(store) == ADA_FP

    async def test_an_unreadable_config_keeps_the_model_and_the_collection(self, monkeypatch) -> None:
        """A failed read must not look like "no model configured", which would
        switch to the default model and recreate the collection. A write is
        refused, though: it cannot confirm the collection is still its model's."""
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic("t1")], merge_membership=False)

        monkeypatch.setattr(module, "_CONFIG_RECHECK_SECONDS", 0.0)
        with patch.object(config.store, "get_key", side_effect=RuntimeError("kv store down")):
            assert await store.embedding_fingerprint() == SMALL_FP
            await store.search_entities("pricing", ORG, set(), {"c1"})
            outcome = await store.upsert_entities_batch([_topic("t2")], merge_membership=False)

        assert (outcome.written, outcome.failed) == (0, 1)
        assert db.deletions == 0
        assert _models_of(db) == {"t1": (SMALL_FP, SMALL.value)}
        await store.upsert_entities_batch([_topic("t2")], merge_membership=False)
        assert _models_of(db)["t2"] == (SMALL_FP, SMALL.value)

    async def test_an_unchanged_model_with_new_credentials_keeps_the_collection(self) -> None:
        db, config = FakeEntityVectorDB(), config_service(SMALL_CONFIG)
        store = _store(db, config)
        await store.upsert_entities_batch([_topic()], merge_membership=False)

        await switch_embedding_model(config, embedding_config("openAI", "text-embedding-3-small", apiKey="rotated"))
        outcome = await store.upsert_entities_batch([_topic()], merge_membership=False)

        assert db.deletions == 0
        assert outcome.unchanged == 1
