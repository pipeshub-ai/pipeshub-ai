"""A filter on a payload field that is not indexed (Qdrant) or not mapped (OpenSearch) is honoured, not widened.

Redis refuses such a filter (``test_redis_unindexed_filter.py``) because its default query dialect would drop the
clause. These tests pin that the other stores never did: a clause on a field with no index still selects exactly the
points that carry the value, and a value no point carries selects nothing. A delete built on either cannot reach
the rest of the record.

Requires: docker compose -f deployment/docker-compose/docker-compose.integration.vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_unindexed_filter_other_stores.py -m integration --timeout=120

pgvector is not covered here: it needs the migrated kernel database of ``tests/integration/db``. Its filter compiler
turns a field with no column into a payload lookup (``pt.payload #>> path``) that matches only points carrying the
value, and refuses a condition with no value (``sql_filters.py``).
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    point_id,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "5f0c1d2e3a4b5c6d7e8f9a0b"
TARGET = "vrid-target"
KEPT = "vrid-kept"


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
    _point("k1", KEPT, "b1", 3),
]


@pytest.fixture(params=["qdrant", "opensearch"])
def store(request) -> tuple:
    """The param's own store only: each backend job starts one store and selects its params with -k, so the other is never requested."""
    return request.param, request.getfixturevalue(f"{request.param}_service")


@pytest.fixture
async def seeded(store) -> AsyncIterator[tuple]:
    """A collection whose only payload indexes are the two the delete paths always have: blockId is NOT indexed or mapped."""
    kind, svc = store
    name = make_collection("unidx")
    await svc.create_collection(name, make_collection_config())
    for field in ("metadata.orgId", "metadata.virtualRecordId"):
        await svc.create_index(name, field, {"type": "keyword"})
    await svc.upsert_points(name, POINTS)
    for _ in range(100):
        if kind == "opensearch":
            await svc.client.indices.refresh(index=name)
        if (await svc.get_collection_info(name)).points_count == len(POINTS):
            break
        await asyncio.sleep(0.1)
    yield svc, name
    await svc.delete_collection(name)


async def _count(svc, name: str, **must) -> int:
    return await svc.count_points(name, await svc.filter_collection(must={"orgId": ORG, **must}))


async def _settle(svc, name: str) -> None:
    if hasattr(svc.client, "indices"):
        await svc.client.indices.refresh(index=name)


async def test_a_count_on_an_unindexed_field_matches_exactly_the_points_that_carry_the_value(seeded) -> None:
    svc, name = seeded
    assert await _count(svc, name, blockId=["b1"]) == 2
    assert await _count(svc, name, virtualRecordId=TARGET, blockId=["b1"]) == 1


async def test_a_value_no_point_carries_selects_nothing(seeded) -> None:
    svc, name = seeded
    assert await _count(svc, name, blockId=["absent"]) == 0
    assert await _count(svc, name, virtualRecordId=TARGET, blockId=["absent"]) == 0


async def test_a_field_no_point_has_selects_nothing(seeded) -> None:
    svc, name = seeded
    assert await _count(svc, name, neverWritten=["x"]) == 0


async def test_delete_by_block_ids_on_an_unindexed_field_removes_only_those_blocks(seeded) -> None:
    svc, name = seeded
    flt = await svc.filter_collection(must={"orgId": ORG, "blockId": ["b1", "b2"], "virtualRecordId": TARGET})
    await svc.delete_points(name, flt, refresh=True)
    await _settle(svc, name)
    assert await _count(svc, name, virtualRecordId=TARGET) == 1
    assert await _count(svc, name, virtualRecordId=KEPT) == 1


async def test_delete_of_an_absent_block_id_removes_nothing(seeded) -> None:
    svc, name = seeded
    flt = await svc.filter_collection(must={"orgId": ORG, "blockId": [f"{TARGET}-summary"], "virtualRecordId": TARGET})
    await svc.delete_points(name, flt, refresh=True)
    await _settle(svc, name)
    assert await _count(svc, name) == len(POINTS)


async def test_delete_on_a_field_no_point_has_removes_nothing(seeded) -> None:
    svc, name = seeded
    flt = await svc.filter_collection(must={"orgId": ORG, "neverWritten": ["x"], "virtualRecordId": TARGET})
    await svc.delete_points(name, flt, refresh=True)
    await _settle(svc, name)
    assert await _count(svc, name) == len(POINTS)
