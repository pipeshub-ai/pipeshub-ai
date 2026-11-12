"""The fence is a store-level conditional write and nothing else: not a scope field, and no sync writer sets it."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.permissions.scope_fields import (
    SCOPE_FENCE_FIELD,
    SCOPE_KEYS,
    checked_fence,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import VectorPoint
from tests.support.in_memory_vector_db import InMemoryVectorDBService

APP = Path(__file__).resolve().parents[4] / "app"
ORG = "o" * 24


def test_the_fence_is_not_a_scope_field() -> None:
    assert SCOPE_FENCE_FIELD == "scopeFence" and SCOPE_FENCE_FIELD not in SCOPE_KEYS


def test_only_the_stores_and_the_field_list_name_the_fence() -> None:
    users = {p.relative_to(APP).as_posix() for p in APP.rglob("*.py") if "SCOPE_FENCE_FIELD" in p.read_text() or '"scopeFence"' in p.read_text()}
    assert users == {
        "services/permissions/scope_fields.py", "services/vector_db/filters.py", "services/vector_db/qdrant/qdrant.py",
        "services/vector_db/opensearch/opensearch.py", "services/vector_db/redis/redis_vector.py", "services/vector_db/redis/utils.py",
        "services/vector_db/scope_rewrite.py",      # reads a stored fence to tell a right point that still has to take its writer's fence
    }


def test_only_the_stores_with_a_conditional_write_say_so() -> None:
    from app.services.vector_db.opensearch.opensearch import OpenSearchService
    from app.services.vector_db.qdrant.qdrant import QdrantService
    from app.services.vector_db.redis.redis_vector import RedisVectorService

    assert IVectorDBService.generation_fence is False and InMemoryVectorDBService.generation_fence is False
    assert all(s.generation_fence for s in (QdrantService, OpenSearchService, RedisVectorService))
    try:
        from app.services.vector_db.pgvector.pgvector import PgVectorService
    except ImportError:
        pytest.skip("pgvector provider not importable here")
    assert PgVectorService.generation_fence is False   # it fences by computed_at, on the group row


@pytest.mark.asyncio
async def test_the_default_groups_points_that_share_a_payload_and_passes_the_extra() -> None:
    calls: list[tuple] = []

    class Spy(InMemoryVectorDBService):
        async def update_payload_by_ids(self, collection_name, point_ids, payload, *, org_id, **extra) -> None:  # type: ignore[override]
            calls.append((sorted(point_ids), payload, org_id, extra))

    await Spy().update_payloads_by_ids("c", {"a": {"k": 1}, "b": {"k": 2}, "c": {"k": 1}}, org_id=ORG, computed_at="t")
    assert sorted(calls) == [(["a", "c"], {"k": 1}, ORG, {"computed_at": "t"}), (["b"], {"k": 2}, ORG, {"computed_at": "t"})]


@pytest.mark.asyncio
async def test_the_in_memory_store_models_the_fence() -> None:
    store = InMemoryVectorDBService()
    await store.create_collection("c")
    await store.upsert_points("c", [VectorPoint(id="p", dense_vector=[1.0] * 1024, payload={"metadata": {"orgId": ORG}})])
    write = lambda value, fence: store.update_payload_by_ids("c", ["p"], {"v": value}, org_id=ORG, fence=fence)  # noqa: E731
    for value, fence, expected in (("a", 5, "a"), ("old", 4, "a"), ("same", 5, "same"), ("new", 6, "new")):
        await write(value, fence)
        assert (await store.retrieve_points("c", ["p"]))[0].payload["v"] == expected
    assert (await store.retrieve_points("c", ["p"]))[0].payload[SCOPE_FENCE_FIELD] == 6


@pytest.mark.parametrize("bad", [True, False, -1, 2**53, 1.0, 1.5, "7", None])
def test_a_fence_is_a_non_negative_int_below_2_to_the_53rd(bad: object) -> None:
    with pytest.raises(ValueError):
        checked_fence(bad)


@pytest.mark.parametrize("good", [0, 1_780_000_000_000_000, 2**53 - 1])
def test_a_good_fence_is_returned_as_it_is(good: int) -> None:
    assert checked_fence(good) == good
