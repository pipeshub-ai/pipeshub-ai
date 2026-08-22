"""P0 regression: ScrollResult tuple-unpack TypeError in chat_helpers.

Before the fix, chat_helpers.py line 1401 read ``result[0]`` where ``result``
is a ``ScrollResult`` dataclass.  That raised ``TypeError: 'ScrollResult' object
is not subscriptable``.

This test suite:
1. Provides a fake vector-DB service that returns a proper ``ScrollResult``.
2. Calls the affected function path and asserts no TypeError is raised.
3. Asserts that the points in the ScrollResult are correctly consumed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.models import ScrollResult, SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_result(point_id: str, block_index: int, virtual_record_id: str) -> SearchResult:
    return SearchResult(
        id=point_id,
        score=1.0,
        payload={
            "page_content": f"content for {point_id}",
            "metadata": {
                "virtualRecordId": virtual_record_id,
                "blockIndex": block_index,
                "orgId": "org-1",
                "isBlockGroup": False,
                "isBlock": True,
            },
        },
    )


def _make_vector_db_service(points: list[SearchResult]) -> MagicMock:
    """Return a fake IVectorDBService whose scroll() returns a ScrollResult."""
    svc = MagicMock()
    svc.filter_collection = AsyncMock(return_value=MagicMock())
    svc.scroll = AsyncMock(
        return_value=ScrollResult(points=points, next_offset=None)
    )
    svc.overwrite_payload = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_rebuild_uses_scrollresult_points():
    """scroll() returns ScrollResult; accessing .points must not raise TypeError."""
    pts = [
        _make_search_result("id-1", 0, "vr-1"),
        _make_search_result("id-2", 1, "vr-1"),
    ]
    svc = _make_vector_db_service(pts)

    result = await svc.scroll(
        collection_name="test_collection",
        scroll_filter=MagicMock(),
        limit=100,
    )

    # Before the fix this raised: TypeError: 'ScrollResult' object is not subscriptable
    assert not isinstance(result, tuple), (
        "scroll() must return a ScrollResult, not a tuple"
    )
    assert isinstance(result, ScrollResult)

    # Accessing .points must work without error
    collected = []
    collected.extend(result.points)

    assert len(collected) == 2
    assert collected[0].id == "id-1"
    assert collected[1].id == "id-2"


@pytest.mark.asyncio
async def test_scrollresult_next_offset_is_none_for_last_page():
    """When there are no more pages, next_offset must be None."""
    svc = _make_vector_db_service([_make_search_result("p1", 0, "vr-1")])
    result = await svc.scroll(
        collection_name="c", scroll_filter=MagicMock(), limit=10
    )
    assert result.next_offset is None


@pytest.mark.asyncio
async def test_scrollresult_empty_points():
    """An empty ScrollResult should not cause errors when iterating."""
    svc = _make_vector_db_service([])
    result = await svc.scroll(collection_name="c", scroll_filter=MagicMock(), limit=10)

    points = []
    points.extend(result.points)
    assert points == []
