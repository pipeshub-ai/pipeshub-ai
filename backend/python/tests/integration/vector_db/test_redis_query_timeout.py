"""Redis filter operations under a query timeout.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_query_timeout.py -m integration --timeout=600

RediSearch's default ON_TIMEOUT is RETURN: a query that runs past its timeout
answers with the rows it had, and a RESP2 reply carries no flag saying so. The
module timeout is dropped to its 1 ms minimum here, so any sizeable scan runs out
of time. A delete, payload write or scroll that trusted that reply would quietly
leave points behind.
"""

import asyncio
import fcntl
import os
import tempfile
from collections.abc import AsyncIterator

import pytest

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    VectorPoint,
)
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    wait_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

POINTS = 60000
# A wide union makes each matching document expensive to reach, so a 1 ms budget
# cannot cover even one delete page.
VRIDS = 2000


async def _count(svc, collection: str, query: str = "*") -> int:
    raw = await svc.client.execute_command(
        "FT.SEARCH", svc._index_name(collection), query,
        "NOCONTENT", "LIMIT", "0", "0", "TIMEOUT", "0",
    )
    return int(raw[0])


@pytest.fixture
async def seeded(redis_service) -> AsyncIterator[tuple[str, FilterExpression]]:
    col = make_collection("redis_timeout")
    await redis_service.create_collection(col, make_collection_config())
    for start in range(0, POINTS, 2000):
        await redis_service.upsert_points(col, [
            VectorPoint(
                id=f"pt-{i}",
                dense_vector=make_dense([1.0]),
                payload={
                    "page_content": f"chunk {i}",
                    "metadata": {"orgId": "org1", "virtualRecordId": f"vr-{i % VRIDS}"},
                },
            )
            for i in range(start, min(start + 2000, POINTS))
        ])
    for _ in range(120):
        if await _count(redis_service, col) == POINTS:
            break
        await asyncio.sleep(1)
    filt = FilterExpression(must=[
        FieldCondition(key="metadata.orgId", value="org1"),
        FieldCondition(key="metadata.virtualRecordId", values=[f"vr-{i}" for i in range(VRIDS)]),
    ])
    # search-timeout belongs to the server, not to this test: under xdist two workers that each read the
    # "original" while the other has it at 1 ms would restore 1 ms for good. The lock makes the tests of
    # this module take turns, so the original is only ever read while nobody has changed it.
    with open(os.path.join(tempfile.gettempdir(), "pipeshub-redis-search-timeout.lock"), "w") as lock:
        await asyncio.to_thread(fcntl.flock, lock, fcntl.LOCK_EX)
        original = (await redis_service.client.execute_command(
            "CONFIG", "GET", "search-timeout"))[1]
        if str(original) in ("1", "b'1'"):  # left behind by a run that died before restoring it
            original = "500"
        await redis_service.client.execute_command("CONFIG", "SET", "search-timeout", "1")
        try:
            yield col, filt
        finally:
            await redis_service.client.execute_command(
                "CONFIG", "SET", "search-timeout", original)
            await redis_service.delete_collection(col)
            fcntl.flock(lock, fcntl.LOCK_UN)


class TestRedisFilterOpsUnderTimeout:
    async def test_delete_by_filter_leaves_nothing_behind(self, redis_service, seeded) -> None:
        col, filt = seeded
        await redis_service.delete_points(col, filt)
        assert await _count(redis_service, col) == 0

    async def test_set_payload_reaches_every_point(self, redis_service, seeded) -> None:
        col, filt = seeded
        await redis_service.set_payload(col, {"recordGroupIds": ["g1"]}, filt)
        assert await _count(redis_service, col, "@recordGroupIds:{g1}") == POINTS

    async def test_key_scan_sees_every_point_exactly_once(self, redis_service, seeded) -> None:
        """An unsorted offset page repeats some keys and misses others while documents are still settling
        in the index, so a payload write skipped points."""
        col, filt = seeded
        keys = await redis_service._keys_matching_filter(col, filt)
        assert len(keys) == POINTS == len(set(keys))

    async def test_scroll_returns_the_requested_page(self, redis_service, seeded) -> None:
        col, filt = seeded
        page = await redis_service.scroll(col, filt, limit=5000)
        assert len(page.points) == 5000


class TestRedisKeyScanIsHeldToACount:
    async def test_a_truncated_cursor_raises_instead_of_answering_with_a_part(self, redis_service) -> None:
        """The scan is held to a separate count of the matches: a cursor that ends early (here, cut after its
        first batch) must not be taken for the whole set."""
        n = 3000
        col = make_collection("redis_key_scan_cut")
        await redis_service.create_collection(col, make_collection_config())
        try:
            await redis_service.upsert_points(col, [
                VectorPoint(
                    id=f"pt-{i}", dense_vector=make_dense([1.0]),
                    payload={"page_content": f"c{i}", "metadata": {"orgId": "org1", "virtualRecordId": f"vr-{i % 50}"}},
                )
                for i in range(n)
            ])

            async def indexed() -> bool:
                return await _count(redis_service, col) >= n

            await wait_for(indexed, timeout=120, poll=0.2)
            real = redis_service.client.execute_command

            async def cut(*args, **kwargs) -> list:
                if args[:2] == ("FT.CURSOR", "READ"):
                    return [[0], 0]  # the cursor "ends" after the first batch
                return await real(*args, **kwargs)

            redis_service.client.execute_command = cut
            try:
                with pytest.raises(RuntimeError, match=r"of 3000 counted"):
                    await redis_service._keys_matching_filter(
                        col, FilterExpression(must=[FieldCondition(key="metadata.orgId", value="org1")]),
                    )
            finally:
                redis_service.client.execute_command = real
        finally:
            await redis_service.delete_collection(col)
