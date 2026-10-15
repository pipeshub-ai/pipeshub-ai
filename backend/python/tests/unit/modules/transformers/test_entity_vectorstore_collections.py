"""Every EntityVectorStore operation resolves its collection from the org it
acts on (``_collection_name_for``). Unoverridden that is the one shared
collection; a subclass that gives each org its own keeps every org's entities,
and every model-change recreate, inside that org's collection.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.exceptions.indexing_exceptions import VectorStoreError
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers import entity_vectorstore as module
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from tests.support.embedding_config import (
    another_process,
    config_service,
    embedding_config,
    switch_embedding_model,
)
from tests.support.entity_collections_db import FakeEntityCollections
from tests.support.entity_vector_db import FakeEmbeddingModel, embedding_models

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.config.configuration_service import ConfigurationService

BGE = FakeEmbeddingModel(1.0, 4)
SMALL = FakeEmbeddingModel(2.0, 6)
ADA = FakeEmbeddingModel(3.0, 6)
SMALL_CONFIG = embedding_config("openAI", "text-embedding-3-small")
ADA_CONFIG = embedding_config("openAI", "text-embedding-ada-002")
SHARED = "entities"
ORG_A, ORG_B = "org_a_entities", "org_b_entities"


@pytest.fixture(autouse=True)
def _models() -> Iterator[None]:
    models = {"text-embedding-3-small": SMALL, "text-embedding-ada-002": ADA}
    with embedding_models(models), patch.object(module, "get_default_embedding_model", return_value=BGE):
        yield


class _PerOrgStore(EntityVectorStore):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._shared_collection = None

    def _collection_name_for(self, org_id: str) -> str:
        if not org_id:
            raise ValueError("an org is needed to name its collection")
        return f"org_{org_id}_entities"


def _store(
    db: FakeEntityCollections, config: ConfigurationService, *, own_collection: bool = True, owner: bool = True,
) -> EntityVectorStore:
    return (_PerOrgStore if own_collection else EntityVectorStore)(
        logger=logging.getLogger("entity-collections-test"), config_service=config,
        vector_db_service=db, recreate_on_dimension_mismatch=owner,
    )


def _topic(entity_id: str, name: str, org_id: str) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id, entity_type=EntityType.TOPIC, name=name, org_id=org_id,
        connector_ids=["c1"], type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
    )


async def _two_orgs(store: EntityVectorStore) -> None:
    outcome = await store.upsert_entities_batch([_topic("t1", "Payroll", "a"), _topic("t2", "Pricing", "b")])
    assert (outcome.written, outcome.failed) == (2, 0)


class TestTheDefaultIsOneSharedCollection:
    async def test_every_orgs_entities_are_written_to_it(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service(), own_collection=False)

        await _two_orgs(store)

        assert db.created == [SHARED]
        assert db.names(SHARED) == {"Payroll", "Pricing"}

    async def test_reads_and_deletes_stay_in_it_filtered_by_org(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service(), own_collection=False)
        await _two_orgs(store)

        hits = await store.search_entities("pay", "b", set(), {"c1"})
        await store.delete_entities_for_org("a")

        assert [h["name"] for h in hits] == ["Pricing"]
        assert db.searched == [SHARED]
        assert db.names(SHARED) == {"Pricing"}
        assert await store.collection_exists("a") is True

    async def test_the_per_org_check_never_drops_the_shared_collection(self) -> None:
        db, config = FakeEntityCollections(), config_service()
        store = _store(db, config, own_collection=False)
        await _two_orgs(store)
        await switch_embedding_model(config, SMALL_CONFIG)

        await store.recreate_stale_collection("a")

        assert db.dropped == []

    async def test_the_leaders_model_switch_recreates_it_and_the_per_org_check_adds_nothing(self) -> None:
        db, config = FakeEntityCollections(), config_service()
        store = _store(db, config, own_collection=False)
        await _two_orgs(store)
        await switch_embedding_model(config, SMALL_CONFIG)

        await store.embedding_fingerprint(recreate=True)
        await store.recreate_stale_collection("a")

        assert db.dropped == [SHARED]
        assert db.created == [SHARED, SHARED]


class TestEachOrgItsOwnCollection:
    async def test_a_batch_spanning_orgs_is_split_by_collection(self) -> None:
        db = FakeEntityCollections()

        await _two_orgs(_store(db, config_service()))

        assert set(db.collections) == {ORG_A, ORG_B}
        assert db.names(ORG_A) == {"Payroll"}
        assert db.names(ORG_B) == {"Pricing"}

    async def test_ensuring_the_shared_collection_creates_none(self) -> None:
        db = FakeEntityCollections()

        await _store(db, config_service()).ensure_collection()

        assert db.created == []

    async def test_an_entity_whose_collection_cannot_be_named_fails_alone(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service())

        outcome = await store.upsert_entities_batch([_topic("t1", "Payroll", ""), _topic("t2", "Pricing", "b")])

        assert (outcome.written, outcome.failed) == (1, 1)
        assert set(db.collections) == {ORG_B}

    async def test_reads_use_only_the_callers_collection(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service())
        await _two_orgs(store)

        hits = await store.search_entities("pay", "b", set(), {"c1"})
        best = await store.find_candidates(["Payroll"], "b", "topic")
        points, _ = await store.page_entity_points("a", ["topic"])

        assert [h["name"] for h in hits] == ["Pricing"]
        assert [c["name"] for c in best[0]] == ["Pricing"]
        assert db.searched == [ORG_B, ORG_B]
        assert [p.entity_id for p in points] == ["t1"]

    async def test_an_org_with_no_collection_reads_as_empty_and_none_is_created(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service())
        await _two_orgs(store)
        created = list(db.created)

        with patch.object(store, "_embed", side_effect=AssertionError("nothing to embed")), \
                patch.object(store, "_query_vectors", side_effect=AssertionError("nothing to embed")):
            assert await store.search_entities("pay", "c", set(), {"c1"}) == []
            assert await store.find_candidates(["Payroll"], "c", "topic") == [[]]
        assert await store.page_entity_points("c", ["topic"]) == ([], None)
        await store.delete_entity("c", "topic", "t1")
        await store.delete_entities("c", "topic", ["t1"])
        await store.delete_entities_by_connector("c", "c1")
        await store.delete_entities_for_org("c")
        await store.recreate_stale_collection("c")

        assert await store.collection_exists("c") is False
        assert db.created == created and db.searched == [] and db.dropped == []

    async def test_deleting_an_orgs_entities_leaves_the_other_orgs(self) -> None:
        db = FakeEntityCollections()
        store = _store(db, config_service())
        await _two_orgs(store)

        await store.delete_entities("a", "topic", ["t1", "t2"])
        await store.delete_entities_for_org("a")

        assert db.names(ORG_A) == set()
        assert db.names(ORG_B) == {"Pricing"}


class TestAModelSwitchIsHandledPerCollection:
    async def _switched(self, *, owner: bool = True) -> tuple[FakeEntityCollections, EntityVectorStore]:
        db, config = FakeEntityCollections(), config_service()
        await _two_orgs(_store(db, config))
        await switch_embedding_model(config, SMALL_CONFIG)
        return db, _store(db, config, owner=owner)

    async def test_an_orgs_write_does_not_drop_its_own_or_any_collection(self) -> None:
        db, store = await self._switched()

        outcome = await store.upsert_entities_batch([_topic("t3", "Leave", "a")])

        assert (outcome.written, outcome.failed) == (0, 1)
        assert db.dropped == []
        assert db.vectors(ORG_A) == {BGE.value} and db.vectors(ORG_B) == {BGE.value}

    async def test_the_leaders_fingerprint_drops_nothing(self) -> None:
        db, store = await self._switched()

        assert await store.embedding_fingerprint(recreate=True) == "openAI:text-embedding-3-small:6"

        assert db.dropped == []

    async def test_the_leader_recreates_only_the_org_it_is_rebuilding(self) -> None:
        db, store = await self._switched()

        await store.recreate_stale_collection("a")
        outcome = await store.upsert_entities_batch([_topic("t1", "Payroll", "a"), _topic("t2", "Pricing", "b")])

        assert db.dropped == [ORG_A]
        assert (outcome.written, outcome.failed) == (1, 1)
        assert db.vectors(ORG_A) == {SMALL.value}
        assert db.names(ORG_B) == {"Pricing"} and db.vectors(ORG_B) == {BGE.value}

    async def test_a_collection_another_model_wrote_is_not_searched(self) -> None:
        db, store = await self._switched()
        await store.recreate_stale_collection("a")
        await store.upsert_entities_batch([_topic("t1", "Payroll", "a")])

        assert [h["name"] for h in await store.search_entities("pay", "a", set(), {"c1"})] == ["Payroll"]
        with pytest.raises(VectorStoreError, match="does not match the embedding model"):
            await store.search_entities("pay", "b", set(), {"c1"})
        assert db.searched == [ORG_A]

    async def test_a_store_that_does_not_own_the_rebuild_never_drops(self) -> None:
        db, store = await self._switched(owner=False)

        with pytest.raises(VectorStoreError, match="does not match the embedding model"):
            await store.recreate_stale_collection("a")

        assert db.dropped == []

    async def test_a_same_dimension_switch_is_seen_from_the_orgs_own_points(self) -> None:
        db, config = FakeEntityCollections(), config_service(SMALL_CONFIG)
        await _two_orgs(_store(db, config))
        await switch_embedding_model(config, ADA_CONFIG)
        store = _store(db, config)

        await store.recreate_stale_collection("b")
        outcome = await store.upsert_entities_batch([_topic("t1", "Payroll", "a"), _topic("t2", "Pricing", "b")])

        assert db.dropped == [ORG_B]
        assert (outcome.written, outcome.failed) == (1, 1)
        assert db.vectors(ORG_A) == {SMALL.value} and db.vectors(ORG_B) == {ADA.value}

    async def test_a_running_store_checks_each_collection_again_after_the_switch(self) -> None:
        db, config = FakeEntityCollections(), config_service()
        store = _store(db, config)
        await _two_orgs(store)
        await switch_embedding_model(config, SMALL_CONFIG)

        outcome = await store.upsert_entities_batch([_topic("t3", "Leave", "a")])
        await store.recreate_stale_collection("a")
        retried = await store.upsert_entities_batch([_topic("t3", "Leave", "a")])

        assert outcome.failed == 1 and retried.written == 1
        assert db.dropped == [ORG_A]

    async def test_a_replica_on_the_replaced_model_creates_no_collection(self) -> None:
        """At another dimension, the collection it created would refuse every
        later write with the new model."""
        db, config = FakeEntityCollections(), config_service(SMALL_CONFIG)
        stale = _store(db, another_process(config))
        await stale.embedding_fingerprint()
        # The stale replica misses the notification.
        await switch_embedding_model(config, ADA_CONFIG)

        outcome = await stale.upsert_entities_batch([_topic("t1", "Payroll", "a")])

        assert outcome.failed == 1
        assert db.created == []

    async def test_a_dropped_collection_is_created_again_on_the_next_write(self) -> None:
        """Another process deleted the org's entities; this one still held the
        collection as ready."""
        db = FakeEntityCollections()
        store = _store(db, config_service())
        await _two_orgs(store)
        await db.delete_collection(ORG_A)

        first = await store.upsert_entities_batch([_topic("t1", "Payroll", "a")])
        second = await store.upsert_entities_batch([_topic("t1", "Payroll", "a")])

        assert (first.failed, second.written) == (1, 1)
        assert db.names(ORG_A) == {"Payroll"}
