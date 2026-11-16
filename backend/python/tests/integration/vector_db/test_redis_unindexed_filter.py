"""A Redis filter on a field the index lacks must be refused, never widened.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_unindexed_filter.py -m integration --timeout=120

RediSearch's default query dialect (1) drops a clause on a field that is not in the
schema, so ``@metadata_orgId:{o} @metadata_blockId:{b}`` matched every point of the
org, and a delete by block id removed the whole record. These run against a real
Redis Stack.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES
from app.services.vector_db.models import HybridSearchRequest, VectorPoint
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.errors import FilterFieldNotIndexed
from app.services.vector_db.redis.redis_vector import RedisVectorService
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    point_id,
)
from tests.support.vector_db import make_collection_registry

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "5f0c1d2e3a4b5c6d7e8f9a0b"
KEPT = "vrid-kept"
TARGET = "vrid-target"


def _point(name: str, vrid: str, block_id: str, axis: int) -> VectorPoint:
    vec = [0.0] * 8
    vec[axis] = 1.0
    return VectorPoint(
        id=point_id(name),
        dense_vector=make_dense(vec),
        payload={
            "page_content": f"content of {name}",
            "metadata": {"orgId": ORG, "virtualRecordId": vrid, "blockId": block_id},
        },
    )


POINTS = [
    _point("t1", TARGET, "b1", 0),
    _point("t2", TARGET, "b2", 1),
    _point("t3", TARGET, "b3", 2),
    # Same block id on another record: a delete scoped to TARGET must not reach it.
    _point("k1", KEPT, "b1", 3),
]


async def _settle(svc, collection: str, expected: int) -> None:
    for _ in range(100):
        info = await svc.get_collection_info(collection)
        if (info.points_count or 0) >= expected:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"index of {collection} never reached {expected} documents")


async def _alter_settled(svc, collection: str) -> None:
    """FT.ALTER indexes the documents that already exist in the background; a count before it ends sees only some of them."""
    from app.services.vector_db.redis.redis_vector import _parse_ft_info

    idx = svc._index_name(collection)
    for _ in range(200):
        info = _parse_ft_info(await svc.client.execute_command("FT.INFO", idx))
        if int(info.get("indexing", 0)) == 0 and float(info.get("percent_indexed", 1)) == 1.0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{idx} kept indexing after FT.ALTER")


async def _seed(svc, collection: str, *, product_indexes: bool) -> None:
    """A collection as the product makes it: ``product_indexes`` runs what ``CollectionRegistry._ensure_payload_indexes`` runs."""
    await svc.create_collection(collection, make_collection_config())
    if product_indexes:
        for field_name, schema in PAYLOAD_KEYWORD_INDEXES:
            await svc.create_index(collection, field_name, schema)
    await svc.upsert_points(collection, POINTS)
    await _settle(svc, collection, len(POINTS))


async def _count(svc, collection: str, **must) -> int:
    flt = await svc.filter_collection(must={"orgId": ORG, **must})
    return await svc.count_points(collection, flt)


@pytest.fixture
async def collection(redis_service) -> AsyncIterator[str]:
    name = make_collection("unidx")
    yield name
    await redis_service.delete_collection(name)


@pytest.fixture
async def second_service(redis_service) -> AsyncIterator[RedisVectorService]:
    """Another process on the same Redis: it alters the index behind the first one's back."""
    cfg = redis_service.config_service
    assert isinstance(cfg, RedisVectorConfig)
    svc = RedisVectorService(cfg)
    await svc.connect()
    yield svc
    await svc.disconnect()


def _store(svc, collection: str) -> VectorStore:
    return VectorStore(
        logger=logging.getLogger("redis-unindexed-filter-it"),
        config_service=AsyncMock(),
        graph_provider=AsyncMock(),
        collection_registry=make_collection_registry(collection),
        vector_db_service=svc,
    )


class TestDeleteByBlockIds:
    async def test_removes_only_those_blocks_of_the_record(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=True)

        await _store(redis_service, collection).delete_blocks_by_ids({"b1", "b2"}, TARGET, collection, org_id=ORG)

        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 1
        assert await _count(redis_service, collection, virtualRecordId=TARGET, blockId=["b3"]) == 1
        # The same block id on another record is untouched.
        assert await _count(redis_service, collection, virtualRecordId=KEPT) == 1

    async def test_an_absent_block_id_deletes_nothing(self, redis_service, collection) -> None:
        """The summary refresh deletes the summary block, which a record does not have until it is summarised."""
        await _seed(redis_service, collection, product_indexes=True)

        await _store(redis_service, collection).delete_blocks_by_ids({f"{TARGET}-summary"}, TARGET, collection, org_id=ORG)

        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 3
        assert await _count(redis_service, collection, virtualRecordId=KEPT) == 1

    async def test_without_the_block_id_index_it_raises_and_deletes_nothing(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        await redis_service.create_index(collection, "metadata.virtualRecordId", {"type": "keyword"})
        await redis_service.create_index(collection, "metadata.orgId", {"type": "keyword"})

        with pytest.raises(Exception) as raised:  # VectorStore wraps the provider's error
            await _store(redis_service, collection).delete_blocks_by_ids({"b1"}, TARGET, collection, org_id=ORG)

        assert isinstance(raised.value.__cause__, FilterFieldNotIndexed)
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 3

    async def test_provider_delete_on_an_unindexed_field_raises_and_deletes_nothing(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["b1"], "virtualRecordId": TARGET})

        with pytest.raises(FilterFieldNotIndexed) as raised:
            await redis_service.delete_points(collection, flt)

        assert raised.value.fields == {"metadata_blockId"}
        assert await _count(redis_service, collection) == len(POINTS)


class TestEveryQueryRefusesAnUnindexedField:
    """count, scroll, search and a payload write answer a filter on an unindexed field with the typed error, not with every point."""

    async def test_count(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        with pytest.raises(FilterFieldNotIndexed):
            await _count(redis_service, collection, blockId=["nope"])

    async def test_scroll(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["nope"]})
        with pytest.raises(FilterFieldNotIndexed):
            await redis_service.scroll(collection, flt, limit=10)

    async def test_hybrid_search(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["nope"]})
        req = HybridSearchRequest(dense_query=make_dense([1.0]), text_query="content", filter=flt, limit=5)
        with pytest.raises(FilterFieldNotIndexed):
            await redis_service.query_nearest_points(collection, [req])

    async def test_text_only_search(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["nope"]})
        req = HybridSearchRequest(dense_query=None, text_query="content", filter=flt, limit=5)
        with pytest.raises(FilterFieldNotIndexed):
            await redis_service.query_nearest_points(collection, [req])

    async def test_payload_write_by_filter_changes_nothing(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["nope"]})
        with pytest.raises(FilterFieldNotIndexed):
            await redis_service.set_payload(collection, {"metadata.touched": "yes"}, flt)
        pts = await redis_service.scroll(
            collection, await redis_service.filter_collection(must={"orgId": ORG}), limit=10, with_payload=["metadata.touched"]
        )
        assert all("touched" not in (p.payload.get("metadata") or {}) for p in pts.points)

    async def test_should_and_must_not_clauses_are_checked_too(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        for kwargs in ({"should": {"blockId": ["b1"]}}, {"must": {"orgId": ORG}, "must_not": {"blockId": ["b1"]}}):
            flt = await redis_service.filter_collection(**kwargs)
            with pytest.raises(FilterFieldNotIndexed):
                await redis_service.count_points(collection, flt)


class TestSchemaCache:
    async def test_create_index_makes_the_field_usable_at_once(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        with pytest.raises(FilterFieldNotIndexed):
            await _count(redis_service, collection, blockId=["b1"])
        idx = redis_service._index_name(collection)
        assert idx in redis_service._schema_fields  # the refusal read the schema

        await redis_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        await _alter_settled(redis_service, collection)

        assert idx not in redis_service._schema_fields  # FT.ALTER dropped the kept copy
        assert await _count(redis_service, collection, blockId=["b1"]) == 2

    async def test_an_alter_by_another_process_is_seen_without_a_restart(self, redis_service, second_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        with pytest.raises(FilterFieldNotIndexed):
            await _count(redis_service, collection, blockId=["b1"])

        await second_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        await _alter_settled(second_service, collection)

        # The first process still holds the old schema; a miss reads it again before refusing.
        assert await _count(redis_service, collection, blockId=["b1"]) == 2

    async def test_a_field_added_by_another_process_while_cached_is_still_refused_when_absent(self, redis_service, second_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=False)
        await second_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        await _alter_settled(second_service, collection)
        assert await _count(redis_service, collection, blockId=["b1"]) == 2
        with pytest.raises(FilterFieldNotIndexed) as raised:
            await _count(redis_service, collection, chapter=["x"])
        assert raised.value.fields == {"metadata_chapter"}

    async def test_dropping_and_recreating_the_collection_resets_the_schema(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=True)
        assert await _count(redis_service, collection, blockId=["b1"]) == 2
        await redis_service.delete_collection(collection)
        await _seed(redis_service, collection, product_indexes=False)
        with pytest.raises(FilterFieldNotIndexed):
            await _count(redis_service, collection, blockId=["b1"])


class TestDialect:
    async def test_every_query_pins_dialect_2(self, redis_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=True)
        seen: list[tuple] = []
        real = redis_service.client.execute_command

        async def spy(*args: object, **kwargs: object) -> object:
            seen.append(args)
            return await real(*args, **kwargs)

        redis_service.client.execute_command = spy
        try:
            store = _store(redis_service, collection)
            flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET})
            await redis_service.count_points(collection, flt)
            await redis_service.scroll(collection, flt, limit=2)
            await redis_service.set_payload(collection, {"metadata.x": "1"}, flt)
            await redis_service.query_nearest_points(
                collection, [HybridSearchRequest(dense_query=None, text_query="content", filter=flt, limit=5)]
            )
            await store.delete_blocks_by_ids({"b1"}, TARGET, collection, org_id=ORG)
        finally:
            redis_service.client.execute_command = real
        queries = [a for a in seen if a[0] in ("FT.SEARCH", "FT.AGGREGATE")]
        assert len(queries) >= 5
        for args in queries:
            assert args[args.index("DIALECT") + 1] == "2", args

    async def test_the_server_widens_in_dialect_1_and_refuses_in_dialect_2(self, redis_service, collection) -> None:
        """The premise of the fix, pinned against the server in use: if a Redis release changes it, this says so."""
        from redis.exceptions import ResponseError

        await _seed(redis_service, collection, product_indexes=False)
        idx = redis_service._index_name(collection)
        query = f"@metadata_orgId:{{{ORG}}} @metadata_blockId:{{nope}}"
        run = redis_service.client.execute_command

        dialect_1 = await run("FT.SEARCH", idx, query, "NOCONTENT", "LIMIT", "0", "0", "DIALECT", "1")
        assert dialect_1[0] == len(POINTS)  # the clause on the unknown field was dropped
        with pytest.raises(ResponseError, match="Unknown field"):
            await run("FT.SEARCH", idx, query, "NOCONTENT", "LIMIT", "0", "0", "DIALECT", "2")
