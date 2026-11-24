"""Redis reads past RediSearch's 10,000-result window. Nothing here depends on the order of a scroll past the window (it is not
stable on a busy server) and nothing sleeps: the stray sweep is safe whatever order it reads in, and removes every stray over
repeated passes; the move reads a match set in one query or does not move it.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_scroll_window.py -m integration
"""
from __future__ import annotations

import contextlib
import random

import pytest

from app.modules.indexing.stray_sweep import sweep_stray_points
from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    point_id,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module"), pytest.mark.timeout(300)]
ORG, VRID = "org-window", "r-window"
KEPT, STRAYS = 11_000, 700          # the strays come last, past the 10,000 window


def _points() -> list[VectorPoint]:
    return [
        VectorPoint(
            id=point_id(f"window-{i}"), dense_vector=make_dense([1.0, float(i % 7)]),
            payload={"page_content": f"text {i}",
                     "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": f"keep-{i}" if i < KEPT else f"stray-{i}"}},
        )
        for i in range(KEPT + STRAYS)
    ]


async def _load(svc, col: str) -> None:
    # the window this file is about; a server that already has it (the older default) or refuses the change is left as it is
    with contextlib.suppress(Exception):
        await svc.client.execute_command("FT.CONFIG", "SET", "MAXSEARCHRESULTS", "10000")
    await svc.create_collection(col, make_collection_config())
    for field in ("metadata.blockId", "metadata.virtualRecordId"):
        await svc.create_index(col, field, {"type": "keyword"})
    points = _points()
    for i in range(0, len(points), 1000):
        await svc.upsert_points(col, points[i:i + 1000])


async def test_a_scroll_goes_past_the_window_and_returns_only_the_records_points(redis_service) -> None:
    col = make_collection("redis_window")
    try:
        await _load(redis_service, col)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        assert await redis_service.count_points(col, flt) == KEPT + STRAYS
        seen: set[str] = set()
        offset, pages = None, 0
        while True:
            page = await redis_service.scroll(col, flt, 500, offset, with_payload=["metadata.blockId"])
            seen.update(p.payload["metadata"]["blockId"] for p in page.points)
            pages += 1
            if not page.next_offset:
                break
            offset = page.next_offset
        assert pages > 20 and len(seen) > 10_500, "the scroll reached past the 10,000 window"
        assert all(b.startswith(("keep-", "stray-")) for b in seen)
        small = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID, "blockId": ["keep-1", "keep-2"]})
        whole = await redis_service.scroll_with_vectors(col, small, 5)
        assert len(whole.points) == 2 and all(p.dense_vector and p.payload["page_content"] for p in whole.points)
    finally:
        await redis_service.delete_collection(col)


async def test_the_sweep_removes_strays_past_the_window_in_bounded_passes(redis_service) -> None:
    col = make_collection("redis_sweep_window")
    bound = 4_000
    try:
        await _load(redis_service, col)
        keep = {f"keep-{i}" for i in range(KEPT)}
        passes = await _sweep_until_clean(redis_service, col, keep, bound)
        assert passes > 2
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        assert await redis_service.count_points(col, flt) == KEPT, "the strays are gone and no kept point was deleted"
    finally:
        await redis_service.delete_collection(col)


SPREAD = 21_000                  # more than two windows, strays all through it


async def _load_spread(svc, col: str, rng: random.Random) -> set[str]:
    """One record of SPREAD points, a tenth of them strays at random positions; returns the kept block ids."""
    await svc.create_collection(col, make_collection_config())
    with contextlib.suppress(Exception):
        await svc.client.execute_command("FT.CONFIG", "SET", "MAXSEARCHRESULTS", "10000")
    for field in ("metadata.blockId", "metadata.virtualRecordId"):
        await svc.create_index(col, field, {"type": "keyword"})
    keep: set[str] = set()
    points = []
    for i in range(SPREAD):
        block = f"stray-{i}" if rng.random() < 0.1 else f"keep-{i}"
        if block.startswith("keep"):
            keep.add(block)
        points.append(VectorPoint(
            id=point_id(f"spread-{i}"), dense_vector=make_dense([1.0, float(i % 5)]),
            payload={"page_content": block, "metadata": {"orgId": ORG, "virtualRecordId": VRID, "blockId": block}},
        ))
    for i in range(0, len(points), 1000):
        await svc.upsert_points(col, points[i:i + 1000])
    return keep


async def _blocks(svc, col: str) -> list[str]:
    flt = await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
    out: list[str] = []
    offset = None
    while True:
        page = await svc.scroll(col, flt, 1000, offset, with_payload=["metadata.blockId"])
        out.extend(p.payload["metadata"]["blockId"] for p in page.points)
        if not page.next_offset:
            return out
        offset = page.next_offset


async def _sweep_until_clean(svc, col: str, keep: set[str], bound: int, *, cycles: int = 12) -> int:
    """Bounded passes, resuming, in whole cycles (a cycle ends when a pass has nothing left to resume), until a whole cycle removes
    nothing. A read past the window can skip a point, so one cycle may miss a stray; the next finds it. Returns the passes made."""
    passes = 0
    for _ in range(cycles):
        removed, resume = 0, None
        while True:
            passes += 1
            result = await sweep_stray_points(svc, col, org_id=ORG, virtual_record_id=VRID, keep_block_ids=keep, max_points=bound, resume=resume)
            assert result.scanned <= bound
            removed += result.stray_blocks
            resume = result.next_offset
            if resume is None:
                break
        if removed == 0:
            return passes
    pytest.fail(f"strays were still being found after {cycles} cycles")


async def test_strays_spread_through_a_large_record_are_all_removed_by_bounded_passes(redis_service) -> None:
    """Deleting strays shifts the positions after them (the resume offset moves back by what was deleted), and whatever order a read
    past the window gives, repeated passes remove every stray and never a kept point."""
    col = make_collection("redis_spread")
    try:
        keep = await _load_spread(redis_service, col, random.Random(20261116))
        assert await _sweep_until_clean(redis_service, col, keep, 3_000) > 5
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        assert await redis_service.count_points(col, flt) == len(keep)
    finally:
        await redis_service.delete_collection(col)


async def test_the_move_reads_a_match_set_in_one_query_or_refuses_a_set_too_large_for_one(redis_service) -> None:
    from app.services.vector_db.errors import VectorsNotFetchable

    col = make_collection("redis_onequery")
    try:
        await _load(redis_service, col)                         # 11,700 points of one record: more than one query returns
        everything = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        with pytest.raises(VectorsNotFetchable):
            await redis_service.scroll_with_vectors(col, everything, 500)
        few = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID, "blockId": [f"keep-{i}" for i in range(300)]})
        page = await redis_service.scroll_with_vectors(col, few, 50)       # the limit is not a page size: all 300 come at once
        assert len(page.points) == 300 and page.next_offset is None and all(p.dense_vector for p in page.points)
        assert (await redis_service.scroll_with_vectors(col, few, 50, "50")).points == []
    finally:
        await redis_service.delete_collection(col)
