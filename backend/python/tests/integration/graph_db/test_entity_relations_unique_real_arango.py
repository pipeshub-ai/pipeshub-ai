"""entityRelations stays one edge per (_from, _to, edgeType) under concurrent writers.

  docker run -d --rm --name aru-arango -p 127.0.0.1:28606:8529 -e ARANGO_ROOT_PASSWORD=aru-pass-1 arangodb:3.12
  ARANGO_IT_URL=http://127.0.0.1:28606 ARANGO_IT_PASSWORD=aru-pass-1 \\
    pytest tests/integration/graph_db/test_entity_relations_unique_real_arango.py -m integration
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("entity-relations-unique-it")
COLL = CollectionNames.ENTITY_RELATIONS.value
FIELDS = ["_from", "_to", "edgeType"]


@pytest.fixture
async def arango() -> AsyncIterator[tuple]:
    try:
        provider = await connect_arango(logger, "entity_relations_unique_it")
    except Exception as exc:
        backend_unavailable("arango", exc)
    prefix = f"eru-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, prefix
    finally:
        await provider.http_client.execute_aql(
            f"FOR d IN {COLL} FILTER STARTS_WITH(d._from, @p) REMOVE d IN {COLL}", {"p": f"records/{prefix}"}
        )
        await provider.disconnect()


def _edge(prefix: str, n: int = 1, **extra) -> dict:
    return {"_from": f"records/{prefix}-{n}", "_to": f"users/{prefix}", "edgeType": "CREATED_BY",
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1, **extra}


async def _count(provider, prefix: str) -> int:
    rows = await provider.http_client.execute_aql(
        f"RETURN LENGTH(FOR d IN {COLL} FILTER STARTS_WITH(d._from, @p) RETURN 1)", {"p": f"records/{prefix}"}
    )
    return rows[0]


async def _unique_index(provider) -> dict | None:
    return next(
        (i for i in await provider.http_client.get_indexes(COLL) if i.get("unique") and i.get("fields") == FIELDS),
        None,
    )


async def test_setup_removes_duplicates_then_builds_the_unique_index(arango) -> None:
    provider, prefix = arango
    client = provider.http_client
    index = await _unique_index(provider)
    if index:
        await client.drop_index(index["id"])
    # createdAtTimestamp 5 is inserted first but is not the oldest.
    await client.execute_aql(
        f"FOR e IN @edges INSERT e INTO {COLL}",
        {"edges": [_edge(prefix, createdAtTimestamp=5, origin="LATER"), _edge(prefix, origin="OLDEST"),
                   _edge(prefix, createdAtTimestamp=9), _edge(prefix, 2)]},
    )
    assert await _count(provider, prefix) == 4

    await provider._ensure_indexes()
    await provider._ensure_indexes()  # idempotent

    assert await _count(provider, prefix) == 2
    kept = await client.execute_aql(
        f"FOR d IN {COLL} FILTER d._from == @f RETURN d.origin", {"f": f"records/{prefix}-1"}
    )
    assert kept == ["OLDEST"]
    assert await _unique_index(provider)


async def test_concurrent_writers_of_one_edge_leave_one(arango) -> None:
    provider, prefix = arango
    await provider._ensure_indexes()
    for round_ in range(10):
        edge = _edge(prefix, 100 + round_)
        results = await asyncio.gather(*(provider.batch_create_entity_relations([dict(edge)]) for _ in range(8)))
        assert results == [True] * 8
    assert await _count(provider, prefix) == 10
    # The index itself refuses a raw second insert.
    with pytest.raises(Exception, match="1210|unique constraint"):
        await provider.http_client.execute_aql(f"INSERT @e INTO {COLL}", {"e": _edge(prefix, 100)})
