# ruff: noqa: ANN401
"""The conditional write of the stores: a write that carries the generation its graph was read at is refused, point by point and
atomically, where a newer one has written.

    pytest tests/integration/vector_db/test_scope_fence_stores.py -m integration -k "TestQdrant or TestOpenSearch or TestRedis"
"""
from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

import pytest

from app.services.permissions.scope_fields import SCOPE_FENCE_FIELD
from app.services.vector_db.errors import OrgRequired
from app.services.vector_db.models import FieldCondition, FilterExpression, VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import make_collection_config, make_dense

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(900)]
ORG = "abababababababababababab"
OTHER = "cdcdcdcdcdcdcdcdcdcdcdcd"

# the size of a real generation (microseconds since the epoch): the stores compare it as a number
BASE = 1_780_000_000_000_000


def F(n: int) -> int:
    return BASE + n


def _id(i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fence/{i}"))


def _points(n: int) -> list[VectorPoint]:
    return [VectorPoint(id=_id(i), dense_vector=make_dense([float(i % 5), 1.0]),
                        payload={"page_content": f"chunk {i}", "metadata": {"orgId": ORG, "virtualRecordId": f"vr-{i}"},
                                 "recordGroupIds": ["g0"]})
            for i in range(n)]


async def _seed(svc: Any, n: int) -> str:
    col = make_collection("fence")
    await svc.create_collection(col, make_collection_config())
    await svc.upsert_points(col, _points(n))
    return col


async def _held(svc: Any, col: str, n: int) -> dict[str, dict[str, Any]]:
    return {str(p.id): p.payload for p in await svc.retrieve_points(col, [_id(i) for i in range(n)])}


def _label(payload: dict[str, Any]) -> int:
    return int(payload.get(SCOPE_FENCE_FIELD) or 0)


def _groups(held: dict[str, dict[str, Any]]) -> set[tuple[str, ...]]:
    return {tuple(p["recordGroupIds"]) for p in held.values()}


class _Fenced:
    """`fence=n` writes a point only while its label is not above n."""

    @pytest.fixture
    def store(self, vector_service: Any) -> Any:
        return vector_service

    async def test_a_lower_fence_is_refused_and_an_equal_or_higher_one_is_accepted(self, store: Any) -> None:
        assert store.generation_fence
        col = await _seed(store, 6)
        try:
            ids = [_id(i) for i in range(6)]

            async def write(group: str, fence: int, who: list[str] = ids, org: str = ORG) -> None:
                await store.update_payloads_by_ids(col, {i: {"recordGroupIds": [group]} for i in who}, org_id=org, fence=fence)

            await write("g1", F(10))                                    # no label yet: the point counts as the oldest
            assert _groups(await _held(store, col, 6)) == {("g1",)}
            assert {_label(p) for p in (await _held(store, col, 6)).values()} == {F(10)}
            await write("g-old", F(9))                                  # an older reader: refused, no error, label unchanged
            assert _groups(await _held(store, col, 6)) == {("g1",)}
            assert {_label(p) for p in (await _held(store, col, 6)).values()} == {F(10)}
            await write("g2", F(10))                                    # the same graph: accepted
            assert _groups(await _held(store, col, 6)) == {("g2",)}
            await write("g3", F(11))
            assert {_label(p) for p in (await _held(store, col, 6)).values()} == {F(11)}
            # only the points that are behind are refused: one point ahead of the writer, the others not
            await write("g4", F(20), ids[:1])
            await write("g5", F(15))
            held = await _held(store, col, 6)
            assert held[ids[0]]["recordGroupIds"] == ["g4"] and _label(held[ids[0]]) == F(20)
            assert all(held[i]["recordGroupIds"] == ["g5"] and _label(held[i]) == F(15) for i in ids[1:])
        finally:
            await store.delete_collection(col)

    async def test_after_a_stale_write_is_refused_a_newer_one_still_applies(self, store: Any) -> None:
        col = await _seed(store, 3)
        try:
            ids = [_id(i) for i in range(3)]
            await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["new"]}), org_id=ORG, fence=F(20))
            await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["stale"]}), org_id=ORG, fence=F(5))
            assert _groups(await _held(store, col, 3)) == {("new",)}
            await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["newer"]}), org_id=ORG, fence=F(21))
            held = await _held(store, col, 3)
            assert _groups(held) == {("newer",)} and {_label(p) for p in held.values()} == {F(21)}
            assert all(p["page_content"].startswith("chunk") and p["metadata"]["orgId"] == ORG for p in held.values())
        finally:
            await store.delete_collection(col)

    async def test_another_orgs_point_with_the_same_id_is_untouched_and_a_blank_org_raises(self, store: Any) -> None:
        col = await _seed(store, 2)
        try:
            ids = [_id(i) for i in range(2)]
            await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["x"]}), org_id=OTHER, fence=F(30))
            await store.update_payloads_by_ids(col, {_id(99): {"recordGroupIds": ["x"]}}, org_id=ORG, fence=F(30))   # no such point
            held = await _held(store, col, 2)
            assert _groups(held) == {("g0",)} and not any(SCOPE_FENCE_FIELD in p for p in held.values())
            for blank in ("", "  ", None):
                with pytest.raises(OrgRequired):
                    await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["x"]}), org_id=blank, fence=F(30))
            assert _groups(await _held(store, col, 2)) == {("g0",)}
        finally:
            await store.delete_collection(col)

    @pytest.mark.parametrize("fence", [True, -1, 2**53, 1.5, "7"])
    async def test_a_bad_fence_is_an_error_and_never_an_unconditional_write(self, store: Any, fence: Any) -> None:
        col = await _seed(store, 2)
        try:
            with pytest.raises(ValueError):
                await store.update_payloads_by_ids(col, {_id(0): {"recordGroupIds": ["x"]}}, org_id=ORG, fence=fence)
            assert _groups(await _held(store, col, 2)) == {("g0",)}
        finally:
            await store.delete_collection(col)

    async def test_a_payload_that_names_the_fence_is_refused(self, store: Any) -> None:
        col = await _seed(store, 2)
        try:
            with pytest.raises(ValueError):
                await store.update_payloads_by_ids(col, {_id(0): {"recordGroupIds": ["x"], SCOPE_FENCE_FIELD: 0}}, org_id=ORG, fence=F(5))
            held = await _held(store, col, 2)
            assert _groups(held) == {("g0",)} and not any(SCOPE_FENCE_FIELD in p for p in held.values())
        finally:
            await store.delete_collection(col)

    async def test_without_a_fence_the_write_is_unconditional_and_leaves_the_label(self, store: Any) -> None:
        col = await _seed(store, 2)
        try:
            ids = [_id(i) for i in range(2)]
            await store.update_payloads_by_ids(col, dict.fromkeys(ids, {"recordGroupIds": ["a"]}), org_id=ORG, fence=F(15))
            await store.update_payloads_by_ids(col, {ids[0]: {"recordGroupIds": ["plain"]}, ids[1]: {"recordGroupIds": ["other"]}}, org_id=ORG)
            held = await _held(store, col, 2)
            assert (held[ids[0]]["recordGroupIds"], held[ids[1]]["recordGroupIds"]) == (["plain"], ["other"])
            assert {_label(p) for p in held.values()} == {F(15)}
        finally:
            await store.delete_collection(col)

    async def test_concurrent_writers_of_different_generations_end_with_the_newest(self, store: Any) -> None:
        """The check and the write are one step in the store: 60 interleaved writers, and every point ends with the payload of the highest
        generation that wrote it, whatever the order the requests were applied in."""
        n = 40
        col = await _seed(store, n)
        try:
            ids = [_id(i) for i in range(n)]
            rng = random.Random(7)
            plan = [(F(g), rng.sample(ids, 25)) for g in rng.sample(range(100, 400), 60)]

            async def writer(g: int, pts: list[str]) -> None:
                await asyncio.sleep(rng.random() * 0.05)
                await store.update_payloads_by_ids(col, {i: {"recordGroupIds": [f"gen-{g}"]} for i in pts}, org_id=ORG, fence=g)

            await asyncio.gather(*(writer(g, pts) for g, pts in plan))
            top: dict[str, int] = {}
            for g, pts in plan:
                for i in pts:
                    top[i] = max(top.get(i, 0), g)
            held = await _held(store, col, n)
            wrong = {i: (held[i].get("recordGroupIds"), _label(held[i]), top.get(i)) for i in ids
                     if top.get(i) and (_label(held[i]) != top[i] or held[i]["recordGroupIds"] != [f"gen-{top[i]}"])}
            assert not wrong, wrong
        finally:
            await store.delete_collection(col)


class TestQdrant(_Fenced):
    @pytest.fixture
    def vector_service(self, qdrant_service: Any) -> Any:
        return qdrant_service


class TestOpenSearch(_Fenced):
    @pytest.fixture
    def vector_service(self, opensearch_service: Any) -> Any:
        return opensearch_service

    async def test_a_point_whose_scope_fields_cannot_be_written_does_not_take_the_fence(self, vector_service: Any) -> None:
        store = vector_service
        col = await _seed(store, 2)
        try:
            ids = [_id(0), _id(1)]
            blocked = store.__dict__.setdefault("scope_blocked", set())
            blocked.add(col)                                   # the index maps a scope field as something else
            await store.update_payloads_by_ids(col, {ids[0]: {"pathIds": ["p"]}, ids[1]: {"recordGroupIds": ["plain"]}}, org_id=ORG, fence=F(10))
            blocked.discard(col)
            held = await _held(store, col, 2)
            assert SCOPE_FENCE_FIELD not in held[ids[0]] and "pathIds" not in held[ids[0]]
            assert held[ids[1]]["recordGroupIds"] == ["plain"] and _label(held[ids[1]]) == F(10)
            # a later correct write, at a lower fence than the skipped one, still lands
            await store.update_payloads_by_ids(col, {ids[0]: {"pathIds": ["p"]}}, org_id=ORG, fence=F(5))
            held = await _held(store, col, 2)
            assert held[ids[0]]["pathIds"] == ["p"] and _label(held[ids[0]]) == F(5)
        finally:
            await store.delete_collection(col)

    async def test_a_swap_is_fenced_too(self, vector_service: Any) -> None:
        store = vector_service
        col = make_collection("fenceswap")
        await store.create_collection(col, make_collection_config())
        pts = _points(4)
        for p in pts:
            p.payload["aclScope"] = ["old"]
        await store.upsert_points(col, pts)
        await store.client.indices.refresh(index=col)
        try:
            flt = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG), FieldCondition(key="aclScope", values=["old"])])
            await store.update_payloads_by_ids(col, {_id(0): {"recordGroupIds": ["x"]}}, org_id=ORG, fence=F(50))      # point 0 is already at 50
            await store.client.indices.refresh(index=col)
            assert await store.swap_array_value(col, "aclScope", "old", "new", flt, fence=F(40)) == 3
            held = await _held(store, col, 4)
            assert held[_id(0)]["aclScope"] == ["old"] and all(held[_id(i)]["aclScope"] == ["new"] and _label(held[_id(i)]) == F(40) for i in (1, 2, 3))
            # and without a fence it swaps whatever matches and leaves the labels
            await store.client.indices.refresh(index=col)
            flt2 = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG), FieldCondition(key="aclScope", values=["new"])])
            assert await store.swap_array_value(col, "aclScope", "new", "newer", flt2) == 3
            held = await _held(store, col, 4)
            assert all(held[_id(i)]["aclScope"] == ["newer"] and _label(held[_id(i)]) == F(40) for i in (1, 2, 3))
        finally:
            await store.delete_collection(col)


class TestRedis(_Fenced):
    @pytest.fixture
    def vector_service(self, redis_service: Any) -> Any:
        return redis_service

    async def test_a_stored_fence_that_is_not_a_number_is_an_error_and_the_point_is_left_alone(self, vector_service: Any) -> None:
        store = vector_service
        col = await _seed(store, 1)
        try:
            await store.client.hset(store._key(col, _id(0)), SCOPE_FENCE_FIELD, "garbage")
            with pytest.raises(Exception, match="not a number"):
                await store.update_payloads_by_ids(col, {_id(0): {"recordGroupIds": ["x"]}}, org_id=ORG, fence=F(5))
            assert _groups(await _held(store, col, 1)) == {("g0",)}
        finally:
            await store.delete_collection(col)
