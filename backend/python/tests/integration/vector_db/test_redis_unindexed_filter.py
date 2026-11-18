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
import os
import subprocess
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
    DIM,
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
    for _ in range(1200):  # 60 s: a scan of the 30k-document collections below takes seconds, more on a loaded host
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


BACKFILL_DIM = 256
BACKFILL_DOCS = 30000


class TestBackfill:
    """``FT.ALTER`` returns at once and indexes the stored documents in the background (minutes on a big collection)."""

    async def _big_collection(self, svc, collection: str) -> None:
        from app.services.vector_db.models import CollectionConfig, DistanceMetric

        await svc.create_collection(collection, CollectionConfig(embedding_size=BACKFILL_DIM, distance_metric=DistanceMetric.COSINE))
        for field_name, schema in PAYLOAD_KEYWORD_INDEXES:
            if field_name != "metadata.blockId":
                await svc.create_index(collection, field_name, schema)
        vec = [0.01 * (i % 7 + 1) for i in range(BACKFILL_DIM)]
        for start in range(0, BACKFILL_DOCS, 2000):
            await svc.upsert_points(collection, [
                VectorPoint(
                    id=point_id(f"bf{n}"), dense_vector=[v + n * 1e-6 for v in vec],
                    payload={"page_content": f"chunk {n}", "metadata": {
                        "orgId": ORG, "virtualRecordId": TARGET if n < 3 else f"vr-{n}", "blockId": f"blk{n}"}},
                )
                for n in range(start, start + 2000)
            ])
        await _settle(svc, collection, BACKFILL_DOCS)
        # The ALTERs above ran on an empty index, but their scans can still be reading the documents written since.
        await _alter_settled(svc, collection)

    async def test_a_delete_by_the_new_field_with_no_wait_is_refused_and_removes_nothing(self, redis_service, collection, monkeypatch) -> None:
        from app.services.vector_db.redis.errors import IndexBackfilling

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        await self._big_collection(redis_service, collection)
        await redis_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET, "blockId": [f"blk{BACKFILL_DOCS - 1}", "blk2"]})

        # Straight after FT.ALTER: the index has not reached every document, so a delete would "succeed" and leave some.
        with pytest.raises(IndexBackfilling):
            await redis_service.delete_points(collection, flt)
        assert await redis_service.count_points(collection, await redis_service.filter_collection(must={"orgId": ORG})) == BACKFILL_DOCS
        await _alter_settled(redis_service, collection)

    async def test_a_delete_by_the_new_field_waits_out_the_scan_then_removes_exactly_the_block(self, redis_service, collection, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "120")
        await self._big_collection(redis_service, collection)
        await redis_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET, "blockId": [f"blk{BACKFILL_DOCS - 1}", "blk2"]})

        await redis_service.delete_points(collection, flt)  # returns only after the scan; blk2 is the one of TARGET that exists
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 2
        assert await _count(redis_service, collection, blockId=["blk2"]) == 0
        assert await _count(redis_service, collection) == BACKFILL_DOCS - 1

    async def test_a_delete_by_fields_already_complete_runs_during_the_scan(self, redis_service, collection, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        await self._big_collection(redis_service, collection)
        await redis_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        idx = redis_service._index_name(collection)
        from app.services.vector_db.redis.redis_vector import _parse_ft_info

        assert int(_parse_ft_info(await redis_service.client.execute_command("FT.INFO", idx)).get("indexing", 0)) == 1, "the scan must still run"
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET})
        await redis_service.delete_points(collection, flt)
        await _alter_settled(redis_service, collection)
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 0
        assert await _count(redis_service, collection) == BACKFILL_DOCS - 3

    async def test_a_payload_write_by_the_new_field_with_no_wait_is_refused(self, redis_service, collection, monkeypatch) -> None:
        from app.services.vector_db.redis.errors import IndexBackfilling

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        await self._big_collection(redis_service, collection)
        await redis_service.create_index(collection, "metadata.blockId", {"type": "keyword"})
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["blk1"]})
        with pytest.raises(IndexBackfilling):
            await redis_service.set_payload(collection, {"metadata.touched": "yes"}, flt)
        await _alter_settled(redis_service, collection)


    async def test_a_scroll_past_the_search_window_refuses_an_unindexed_field(self, redis_service, collection) -> None:
        """Past MAXSEARCHRESULTS a scroll reads by FT.AGGREGATE cursor; in dialect 1 the blockId clause would be dropped (the whole org)."""
        await self._big_collection(redis_service, collection)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "blockId": ["blk1"]})
        with pytest.raises(FilterFieldNotIndexed):
            await redis_service.scroll(collection, flt, limit=5, offset="10000")

    async def test_an_index_created_over_stored_documents_holds_a_delete_by_any_field_until_it_has_read_them(self, redis_service, collection, monkeypatch) -> None:
        """FT.DROPINDEX without DD keeps the hashes; FT.CREATE then indexes them in the background, its own fields included."""
        from app.services.vector_db.models import CollectionConfig, DistanceMetric
        from app.services.vector_db.redis.errors import IndexBackfilling
        from app.services.vector_db.redis.redis_vector import _parse_ft_info

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        await self._big_collection(redis_service, collection)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET})
        await redis_service.delete_points(collection, await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": "absent"}))
        idx = redis_service._index_name(collection)
        await redis_service.client.execute_command("FT.DROPINDEX", idx)
        await redis_service.create_collection(collection, CollectionConfig(embedding_size=BACKFILL_DIM, distance_metric=DistanceMetric.COSINE))
        assert int(_parse_ft_info(await redis_service.client.execute_command("FT.INFO", idx)).get("indexing", 0)) == 1, "the scan must still run"
        await redis_service._note_complete_fields(idx)
        assert await redis_service._complete_fields(idx) == frozenset(), "neither the dropped index nor a running scan vouches for a field"

        with pytest.raises(IndexBackfilling):
            await redis_service.delete_points(collection, flt)
        await _alter_settled(redis_service, collection)
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 3

    @pytest.mark.redis_restart
    @pytest.mark.skipif(not os.environ.get("REDIS_VECTOR_RESTART_CONTAINER"), reason="needs a Redis container with appendonly yes that the test may restart")
    async def test_after_a_restart_that_rebuilds_the_index_a_delete_waits_for_the_rebuild(self, redis_service, collection, monkeypatch) -> None:
        """With AOF the restart replays FT.CREATE after the hashes, so the index reads them again in the background while the server answers.

        Run by hand (CI deselects the redis_restart marker; its Redis has no persistence):
          docker run -d --name redis-restart-it -p 127.0.0.1:6398:6379 redis:8.4.8 redis-server --appendonly yes
          REDIS_VECTOR_PORT=6398 REDIS_VECTOR_RESTART_CONTAINER=redis-restart-it \
            pytest tests/integration/vector_db/test_redis_unindexed_filter.py -m redis_restart --timeout=600
          docker rm -f -v redis-restart-it
        """
        from app.services.vector_db.models import CollectionConfig, DistanceMetric
        from app.services.vector_db.redis.errors import IndexBackfilling
        from app.services.vector_db.redis.redis_vector import _parse_ft_info

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        # The log must end with this test's FT.CREATE after its hashes. Compact it first (the earlier tests' writes would make the
        # restart's replay long enough for the scan to finish inside it), then keep a rewrite from folding the index into the
        # RDB base, which a restart loads with the index already built. The restart puts the setting back to the default.
        await redis_service.client.execute_command("CONFIG", "SET", "auto-aof-rewrite-percentage", "0")
        await redis_service.client.execute_command("BGREWRITEAOF")
        for _ in range(600):
            persistence = await redis_service.client.info("persistence")
            if not persistence.get("aof_rewrite_in_progress") and not persistence.get("aof_rewrite_scheduled"):
                break
            await asyncio.sleep(0.1)
        await self._big_collection(redis_service, collection)
        idx = redis_service._index_name(collection)
        # FT.CREATE after the hashes in the log, as when an index is built over stored documents.
        await redis_service.client.execute_command("FT.DROPINDEX", idx)
        await redis_service.create_collection(collection, CollectionConfig(embedding_size=BACKFILL_DIM, distance_metric=DistanceMetric.COSINE))
        await _alter_settled(redis_service, collection)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET})
        await redis_service.delete_points(collection, await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": "absent"}))

        subprocess.run(["docker", "restart", os.environ["REDIS_VECTOR_RESTART_CONTAINER"]], check=True, capture_output=True)
        for _ in range(600):
            try:
                info = _parse_ft_info(await redis_service.client.execute_command("FT.INFO", idx))
                break
            except Exception:  # connection refused, then LOADING
                await asyncio.sleep(0.1)
        assert int(info.get("indexing", 0)) == 1, "the restart must still be rebuilding the index"

        with pytest.raises(IndexBackfilling):
            await redis_service.delete_points(collection, flt)
        await _alter_settled(redis_service, collection)
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 3

    async def test_another_process_adding_a_field_the_first_counts_as_original_is_waited_for(self, redis_service, second_service, collection, monkeypatch) -> None:
        """An index from before virtualRecordId was part of the schema: process B alters it in, process A deletes by it mid-scan."""
        from app.services.vector_db.redis.errors import IndexBackfilling
        from app.services.vector_db.redis.redis_vector import _parse_ft_info

        idx = redis_service._index_name(collection)
        await redis_service.client.execute_command(
            "FT.CREATE", idx, "ON", "HASH", "PREFIX", "1", f"{collection}:", "SCHEMA",
            "page_content", "TEXT", "dense_embedding", "VECTOR", "HNSW", "6", "TYPE", redis_service._dense_dtype, "DIM", str(BACKFILL_DIM), "DISTANCE_METRIC", "COSINE",
            "metadata_orgId", "TAG",
        )
        vec = [0.01 * (i % 7 + 1) for i in range(BACKFILL_DIM)]
        for start in range(0, BACKFILL_DOCS, 2000):
            await redis_service.upsert_points(collection, [
                VectorPoint(id=point_id(f"om{n}"), dense_vector=[v + n * 1e-6 for v in vec],
                            payload={"page_content": f"chunk {n}", "metadata": {"orgId": ORG, "virtualRecordId": TARGET if n < 3 else f"vr-{n}"}})
                for n in range(start, start + 2000)
            ])
        await second_service.create_index(collection, "metadata.virtualRecordId", {"type": "keyword"})
        await redis_service.create_index(collection, "metadata.virtualRecordId", {"type": "keyword"})  # "duplicate" while that scan runs
        assert int(_parse_ft_info(await redis_service.client.execute_command("FT.INFO", idx)).get("indexing", 0)) == 1, "the scan must still run"
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": TARGET})

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0")
        with pytest.raises(IndexBackfilling):
            await redis_service.delete_points(collection, flt)
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "120")
        await redis_service.delete_points(collection, flt)
        assert await _count(redis_service, collection, virtualRecordId=TARGET) == 0
        assert await _count(redis_service, collection) == BACKFILL_DOCS - 3
        assert "metadata_virtualRecordId" in await redis_service._complete_fields(idx), "recorded once the scan was seen to finish"


class TestOlderIndex:
    """An index made by an earlier release holds fewer fields; the query service ensures them when it starts."""

    async def _old_index(self, svc, collection: str) -> None:
        idx = svc._index_name(collection)
        await svc.client.execute_command(
            "FT.CREATE", idx, "ON", "HASH", "PREFIX", "1", f"{collection}:", "SCHEMA",
            "page_content", "TEXT", "dense_embedding", "VECTOR", "HNSW", "6", "TYPE", "FLOAT16", "DIM", "8", "DISTANCE_METRIC", "COSINE",
            "metadata_orgId", "TAG", "metadata_virtualRecordId", "TAG",
        )
        await svc.upsert_points(collection, [
            VectorPoint(id=point_id("o1"), dense_vector=make_dense([1.0]), payload={
                "page_content": "alpha", "metadata": {"orgId": ORG, "virtualRecordId": "a"}, "connectorIds": ["c1"]}),
            VectorPoint(id=point_id("o2"), dense_vector=make_dense([0.0, 1.0]), payload={
                "page_content": "alpha", "metadata": {"orgId": ORG, "virtualRecordId": "b"}, "connectorIds": ["c2"]}),
        ])
        await _settle(svc, collection, 2)

    async def test_text_search_by_connector_works_after_the_startup_reconcile(self, redis_service, collection) -> None:
        from unittest.mock import MagicMock

        from app.services.vector_db.collection_registry import CollectionRegistry
        from app.services.vector_db.models import CollectionConfig
        from app.services.vector_db.strategies.single import SingleCollectionStrategy
        from tests.support.vector_db import make_manifest_store

        await self._old_index(redis_service, collection)
        flt = await redis_service.filter_collection(must={"orgId": ORG}, should={"connectorIds": ["c1"]})
        req = HybridSearchRequest(dense_query=None, text_query="alpha", filter=flt, limit=5)
        with pytest.raises(FilterFieldNotIndexed):  # refused, not answered with both points
            await redis_service.query_nearest_points(collection, [req])

        registry = CollectionRegistry(
            vector_db_service=redis_service, strategy=SingleCollectionStrategy(),
            collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
            manifest_store=make_manifest_store([collection], dimension=DIM), logger=MagicMock(),
        )
        assert await registry.reconcile_payload_indexes() == [collection]
        await _alter_settled(redis_service, collection)

        hits = (await redis_service.query_nearest_points(collection, [req]))[0]
        assert [h.id for h in hits] == [point_id("o1")]


class TestServerRefusalHealsTheCache:
    async def test_an_index_rebuilt_without_the_field_gives_the_typed_error_and_a_fresh_schema(self, redis_service, second_service, collection) -> None:
        await _seed(redis_service, collection, product_indexes=True)
        assert await _count(redis_service, collection, blockId=["b1"]) == 2  # cached: the field is there

        # Another process (an older release, or a model-change rebuild) drops the index and recreates it without the field.
        await second_service.delete_collection(collection)
        await second_service.create_collection(collection, make_collection_config())
        await second_service.upsert_points(collection, POINTS)
        await _settle(second_service, collection, len(POINTS))

        with pytest.raises(FilterFieldNotIndexed) as raised:
            await _count(redis_service, collection, blockId=["b1"])
        assert raised.value.fields == {"metadata_blockId"}
        assert "metadata_blockId" not in redis_service._schema_fields[redis_service._index_name(collection)]
