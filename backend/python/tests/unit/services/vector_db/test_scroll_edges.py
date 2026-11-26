"""Edges of the paged reads the move and the sweep rely on: OpenSearch's page ceiling, Redis's cursor cache."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.models import FilterExpression
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.redis.redis_vector import (
    _SCROLL_CURSORS_KEPT,
    RedisVectorService,
)


def _hit(i: int) -> dict:
    return {"_id": f"id-{i}", "sort": [f"id-{i}"], "_source": {"metadata": {"blockId": f"b{i}"}, "page_content": "t", "dense_embedding": [1.0]}}


def _opensearch(total: int) -> OpenSearchService:
    service = OpenSearchService.__new__(OpenSearchService)
    service._assert_connected = AsyncMock()
    client = MagicMock()

    async def search(index: str, body: dict) -> dict:
        after = body["search_after"][0] if "search_after" in body else None
        start = int(after.split("-")[1]) + 1 if after else 0
        return {"hits": {"hits": [_hit(i) for i in range(start, min(total, start + body["size"]))]}}

    client.search = search
    service.client = client
    return service


@pytest.mark.parametrize("method", ["scroll", "scroll_with_vectors"])
async def test_a_limit_above_the_page_ceiling_still_continues(method: str) -> None:
    """12,000 matches and a limit of 20,000: the first page is the ceiling's 10,000, and the cursor says more is left."""
    service = _opensearch(12_000)
    read = getattr(service, method)
    first = await read("c", FilterExpression(), 20_000)
    assert len(first.points) == 10_000 and first.next_offset is not None
    second = await read("c", FilterExpression(), 20_000, first.next_offset)
    assert len(second.points) == 2_000 and second.next_offset is None


async def test_an_evicted_cursor_is_deleted_with_the_index_that_owns_it() -> None:
    service = RedisVectorService.__new__(RedisVectorService)
    service._scroll_cursors = {}
    client = MagicMock()
    calls: list[tuple] = []

    async def execute_command(*args: object) -> object:
        calls.append(args)
        return [[10_100, *([[b"__key", b"c:" + str(n).encode()]] * 10)], 7]  # a page of 10 keys and a cursor left open

    client.execute_command = execute_command
    service.client = client
    for n in range(_SCROLL_CURSORS_KEPT + 1):
        await service._cursor_keys(f"idx-{n}", "q", 10_000, 10)
    deleted = [c for c in calls if c[:2] == ("FT.CURSOR", "DEL")]
    assert deleted and deleted[0][2] == "idx-0", "the first index's cursor goes with the first index's name"
