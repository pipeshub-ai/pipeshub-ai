"""A delete scoped to a subset of a virtual record's points never runs where the store would widen it."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from app.exceptions.indexing_exceptions import VectorStoreError
from app.modules.transformers.vectorstore import deterministic_point_id
from app.services.vector_db.membership import require_indexed_filter
from app.services.vector_db.models import FieldCondition, FilterExpression
from app.services.vector_db.redis.redis_vector import RedisVectorService
from app.services.vector_db.write_scope import (
    WholeRecordScope,
    reset_scope_resolver,
    set_scope_resolver,
)
from tests.support import scoped_points_world as world
from tests.support.in_memory_vector_db import InMemoryVectorDBService


class _BlindToTheGroup(InMemoryVectorDBService):
    """A store whose index lacks the group field and the block id."""

    async def unindexed_filter_fields(self, collection_name: str, flt: FilterExpression) -> list[str]:
        return sorted(c.key for c in (*flt.must, *flt.must_not) if c.key in {world.GROUP_PATH, "metadata.blockId"})


@pytest.fixture(autouse=True)
def _default_resolver() -> None:
    reset_scope_resolver()
    yield
    reset_scope_resolver()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["delete_embeddings", "delete_blocks_by_ids"])
async def test_a_scoped_delete_on_a_store_that_cannot_filter_by_the_scope_is_refused_and_deletes_nothing(method: str) -> None:
    service = _BlindToTheGroup()
    await world.fresh(service, "records")
    store = world.store_over(service)
    call = (
        store.delete_embeddings(world.V, "records", org_id=world.ORG, scope=world.FIRST)
        if method == "delete_embeddings"
        else store.delete_blocks_by_ids({"b0"}, world.V, "records", org_id=world.ORG, scope=world.FIRST)
    )

    with pytest.raises(VectorStoreError) as refused:
        await call

    assert world.GROUP_PATH in str(refused.value.__cause__)

    assert await world.census(service, "records") == world.FULL


@pytest.mark.asyncio
async def test_a_scoped_delete_on_a_store_that_can_filter_removes_only_its_scope() -> None:
    service = InMemoryVectorDBService()
    await world.fresh(service, "records")

    await world.store_over(service).delete_embeddings(world.V, "records", org_id=world.ORG, scope=world.FIRST)

    assert await world.census(service, "records") == {"first": 0, "second": 3, "other": 3}


@pytest.mark.asyncio
async def test_a_whole_record_delete_does_not_ask_the_store_anything() -> None:
    service = InMemoryVectorDBService()
    await world.fresh(service, "records")
    asked = AsyncMock(side_effect=AssertionError("a whole-record delete is not scoped"))
    service.unindexed_filter_fields = asked  # type: ignore[method-assign]

    await world.store_over(service).delete_embeddings(world.OTHER, "records", org_id=world.ORG, scope=WholeRecordScope.of(world.OTHER))

    asked.assert_not_awaited()
    assert (await world.census(service, "records"))["other"] == 0


@pytest.mark.asyncio
async def test_a_store_that_filters_on_any_field_is_never_refused() -> None:
    await require_indexed_filter(InMemoryVectorDBService(), "records", FilterExpression(must=[FieldCondition(key="metadata.blockId", value="b")]))


@pytest.mark.asyncio
async def test_a_write_in_a_scope_stores_salted_ids_and_the_scopes_payload() -> None:
    service = InMemoryVectorDBService()
    await service.create_collection("records", world.CollectionConfig(embedding_size=2, distance_metric=world.DistanceMetric.COSINE))
    store = world.store_over(service)
    store._embed_documents_with_retry = AsyncMock(return_value=[[0.1, 0.2]])
    store._compute_sparse_embeddings = AsyncMock(return_value=[None])
    metadata = {"virtualRecordId": world.V, "blockId": "b1", "isBlock": True, "isBlockGroup": False, "orgId": world.ORG}
    doc = Document(page_content="alpha", metadata=metadata)

    await store._embed_and_upsert_documents([doc], "r1", "records", scope=world.FIRST)
    await store._embed_and_upsert_documents([doc], "r1", "records")

    stored = service._points("records")
    salted = deterministic_point_id(metadata, "alpha", world.FIRST.point_salt)
    plain = deterministic_point_id(metadata, "alpha")
    assert set(stored) == {salted, plain} and salted != plain
    assert stored[salted].payload["metadata"][world.GROUP_FIELD] == world.FIRST.group
    assert world.GROUP_FIELD not in stored[plain].payload["metadata"]


@pytest.mark.asyncio
async def test_the_installed_resolver_decides_the_scope_and_a_whole_record_keeps_the_ids_asked_for() -> None:
    store = world.store_over(InMemoryVectorDBService())
    record = MagicMock(virtual_record_id="vr-record")

    assert await store._scope_for("vr-asked", record, write=True) == WholeRecordScope.of("vr-asked")
    assert await store._scope_for("vr-asked", None, write=True) == WholeRecordScope.of("vr-asked")

    async def resolver(_record: object, _ctx: object, _write: bool) -> world.StubScope:
        return world.SECOND

    set_scope_resolver(resolver)
    assert await store._scope_for("vr-asked", record, write=True) is world.SECOND


def _redis(attributes: list[list[str]] | Exception) -> RedisVectorService:
    service = object.__new__(RedisVectorService)
    service._connected = True
    service.client = MagicMock()
    if isinstance(attributes, Exception):
        service.client.execute_command = AsyncMock(side_effect=attributes)
    else:
        service.client.execute_command = AsyncMock(return_value=["attributes", attributes])
    service._assert_connected = lambda: None  # type: ignore[method-assign]
    return service


FILTER = FilterExpression(must=[
    FieldCondition(key="metadata.orgId", value="o"), FieldCondition(key=world.GROUP_PATH, value="s"),
    FieldCondition(key="metadata.blockId", values=["b"]),
])


@pytest.mark.asyncio
async def test_redis_names_the_fields_its_index_does_not_hold() -> None:
    service = _redis([["identifier", "metadata_orgId", "type", "TAG"], ["identifier", f"metadata_{world.GROUP_FIELD}", "type", "TAG"]])

    assert await service.unindexed_filter_fields("records", FILTER) == ["metadata.blockId"]


@pytest.mark.asyncio
async def test_redis_that_cannot_read_its_index_says_everything_is_missing() -> None:
    service = _redis(RuntimeError("no such index"))

    assert await service.unindexed_filter_fields("records", FILTER) == sorted(c.key for c in FILTER.must)


@pytest.mark.asyncio
async def test_the_default_store_answer_is_empty() -> None:
    assert await InMemoryVectorDBService().unindexed_filter_fields("records", FILTER) == []


@pytest.mark.asyncio
async def test_a_purge_asks_for_the_scope_of_a_write_so_it_removes_only_that_scope() -> None:
    """The resolver hands a scoped answer to a write and a whole-record one to a read. A purge deletes, so it is a write."""
    asked: list[bool] = []

    async def resolver(_record: object, _ctx: object, write: bool) -> object:
        asked.append(write)
        return world.FIRST if write else WholeRecordScope.of(world.V)

    set_scope_resolver(resolver)
    service = InMemoryVectorDBService()
    await world.fresh(service, "records")
    store = world.store_over(service)

    await store.purge_record_vectors(world.ORG, world.V, MagicMock(virtual_record_id=world.V, org_id=world.ORG))

    assert asked == [True]
    assert await world.census(service, "records") == {"first": 0, "second": 3, "other": 3}
