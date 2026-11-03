"""A provider whose embedding model and dimension belong to each org's space (pgvector) cannot answer a collection-wide
question: a scroll or a read by id names an org, and the collection is never dropped for one org's model. The store then
initialises without probing a stored point, and every read by id runs for the org it is about."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.core import org_context
from app.models.entities import EntityRecord, EntityType, EntityTypeCategory
from app.modules.transformers import entity_vectorstore as module
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.errors import OrgRequired
from app.services.vector_db.models import (
    ScrollResult,
    VectorDBCapabilities,
    VectorPoint,
)
from tests.support.embedding_config import config_service
from tests.support.entity_vector_db import (
    FakeEmbeddingModel,
    FakeEntityVectorDB,
    embedding_models,
    recreate_lock_redis,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.services.vector_db.filter_expression import FilterExpression

ORG_A, ORG_B = "a" * 24, "b" * 24


@pytest.fixture(autouse=True)
def _env() -> Iterator[None]:
    with embedding_models({}), patch.object(module, "get_default_embedding_model", return_value=FakeEmbeddingModel(1.0, 4)), recreate_lock_redis():
        yield


class _OrgSpaceDB(FakeEntityVectorDB):
    """What pgvector demands: every scroll pins an org, a read by id has the request's."""

    def __init__(self, dimension: int = 4) -> None:
        super().__init__(dimension)
        self.catalog: dict[str, str] = {}
        self.scrolls = 0

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities(stamps_in_catalog=True, spaces_per_org=True)

    async def get_collection_stamp(self, name: str) -> str | None:
        return self.catalog.get(name)

    async def set_collection_stamp(self, name: str, stamp: str) -> str:
        return self.catalog.setdefault(name, stamp)

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: str | None = None,
        with_payload: list[str] | None = None,
    ) -> ScrollResult:
        self.scrolls += 1
        if not scroll_filter["must"].get("metadata.orgId"):
            raise OrgRequired("a scroll needs the organisation")
        return await super().scroll(collection_name, scroll_filter, limit, offset, with_payload)

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        org_context.current()          # MissingOrgContext when no org is bound
        return await super().retrieve_points(collection_name, ids)


def _store(db) -> EntityVectorStore:
    return EntityVectorStore(
        logger=logging.getLogger("org-space-test"), config_service=config_service(), vector_db_service=db,
        recreate_on_dimension_mismatch=True,
    )


def _entity(org: str, entity_id: str) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id, entity_type=EntityType.TOPIC, name=f"Name {entity_id}", org_id=org,
        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE, connector_ids=["c1"], record_group_ids=["g1"],
    )


async def test_initialising_reads_no_point_and_the_rebuild_leader_gets_its_fingerprint() -> None:
    db = _OrgSpaceDB()
    store = _store(db)
    assert (await store.embedding_fingerprint(recreate=True)).endswith(":4")
    assert await store.collection_stamp()
    assert db.scrolls == 0


async def test_another_dimension_in_one_orgs_space_never_drops_the_collection() -> None:
    db = _OrgSpaceDB(dimension=6)          # what one org's space reports through get_collection_info
    db.points["x"] = module.VectorPoint(id="x", payload={"metadata": {"entityId": "e", "entityType": "topic"}})
    store = _store(db)
    await store.embedding_fingerprint(recreate=True)
    assert db.deletions == 0 and "x" in db.points


async def test_a_batch_of_two_orgs_is_written_org_by_org_without_a_bound_org() -> None:
    db = _OrgSpaceDB()
    store = _store(db)
    outcome = await store.upsert_entities_batch([_entity(ORG_A, "t1"), _entity(ORG_B, "t2")])
    assert outcome.failed == 0 and len(db.entity_points) == 2
    again = await store.upsert_entities_batch([_entity(ORG_A, "t1"), _entity(ORG_B, "t2")])
    assert again.failed == 0


async def test_an_org_other_than_the_bound_one_is_refused() -> None:
    db = _OrgSpaceDB()
    store = _store(db)
    with org_context.for_org(ORG_A, job="test"):
        outcome = await store.upsert_entities_batch([_entity(ORG_B, "t2")])
    assert outcome.failed == 1 and not db.entity_points


async def test_two_orgs_sharing_a_collection_are_never_written_in_one_batch() -> None:
    """A provider with a space per org takes one org per write; grouping a batch by collection alone would mix them."""
    db = _OrgSpaceDB()
    batches: list[set[str]] = []
    real = db.upsert_points

    async def spy(collection_name: str, points: list[VectorPoint], *a: object, **k: object) -> None:
        batches.append({p.payload["metadata"]["orgId"] for p in points if "orgId" in p.payload.get("metadata", {})})
        await real(collection_name, points, *a, **k)

    db.upsert_points = spy
    store = _store(db)
    assert store._collection_name_for(ORG_A) == store._collection_name_for(ORG_B)    # one shared collection

    await store.upsert_entities_batch([_entity(ORG_A, "t1"), _entity(ORG_B, "t2"), _entity(ORG_A, "t3")])

    written = [b for b in batches if b]
    assert written and all(len(b) == 1 for b in written)
    assert set().union(*written) == {ORG_A, ORG_B}
