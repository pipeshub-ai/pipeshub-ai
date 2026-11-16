"""Redis pages past RediSearch's 10,000-result window, so a record that large is read and swept whole.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
Run: pytest tests/integration/vector_db/test_redis_scroll_window.py -m integration
"""
from __future__ import annotations

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
    await svc.create_collection(col, make_collection_config())
    for field in ("metadata.blockId", "metadata.virtualRecordId"):
        await svc.create_index(col, field, {"type": "keyword"})
    points = _points()
    for i in range(0, len(points), 1000):
        await svc.upsert_points(col, points[i:i + 1000])


async def test_a_scroll_reads_every_point_of_a_record_past_the_window(redis_service) -> None:
    col = make_collection("redis_window")
    try:
        await _load(redis_service, col)
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        seen: set[str] = set()
        offset = None
        while True:
            page = await redis_service.scroll(col, flt, 500, offset, with_payload=["metadata.blockId"])
            seen.update(p.payload["metadata"]["blockId"] for p in page.points)
            if not page.next_offset:
                break
            offset = page.next_offset
        assert len(seen) == KEPT + STRAYS
        whole = await redis_service.scroll_with_vectors(col, flt, 5, "10500")
        assert len(whole.points) == 5 and all(p.dense_vector and p.payload["page_content"] for p in whole.points)
    finally:
        await redis_service.delete_collection(col)


async def test_the_sweep_removes_strays_past_the_window_in_bounded_passes(redis_service) -> None:
    col = make_collection("redis_sweep_window")
    bound = 4_000
    try:
        await _load(redis_service, col)
        keep = {f"keep-{i}" for i in range(KEPT)}
        resume, scans = None, []
        for _ in range(8):
            result = await sweep_stray_points(redis_service, col, org_id=ORG, virtual_record_id=VRID, keep_block_ids=keep,
                                              max_points=bound, resume=resume)
            scans.append(result.scanned)
            assert result.scanned <= bound
            resume = result.next_offset
            if resume is None:
                break
        assert len(scans) > 1 and resume is None
        flt = await redis_service.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
        assert await redis_service.count_points(col, flt) == KEPT
        again = await sweep_stray_points(redis_service, col, org_id=ORG, virtual_record_id=VRID, keep_block_ids=keep, max_points=20_000)
        assert again.stray_blocks == 0
    finally:
        await redis_service.delete_collection(col)
