"""A status write that carries a Force reindex's request raises when the store refuses it; one without it logs and goes on.

The walk stores the request in the statement that clears the status, before the publish. If the store swallowed a failure there,
the walk would publish a batch whose request never landed, and a lost delivery would drop the Force reindex.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind
from app.modules.indexing import refresh_intent as ri
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider

FIELDS = ri.intent_fields("i1", 1_800_000_000_000)


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=RuntimeError("store down"))
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("store down"))
    return provider


def _postgres() -> PostgresGraphProvider:
    provider = PostgresGraphProvider(logging.getLogger("t"), MagicMock())
    provider._q = AsyncMock(side_effect=RuntimeError("store down"))
    return provider


@pytest.fixture(autouse=True)
def _org() -> object:
    with bind(OrgContext("org-1", None, "job", "job:test", "t")):
        yield


@pytest.mark.parametrize("make", [_neo4j, _arango, _postgres], ids=["neo4j", "arango", "postgres"])
async def test_a_refused_write_with_the_request_raises(make: object) -> None:
    with pytest.raises(RuntimeError, match="store down"):
        await make().update_indexing_status_for_record_ids(["r1"], "NOT_STARTED", fields=FIELDS)  # type: ignore[operator]


@pytest.mark.parametrize("make", [_neo4j, _arango, _postgres], ids=["neo4j", "arango", "postgres"])
async def test_a_refused_write_without_the_request_is_still_logged_and_swallowed(make: object) -> None:
    assert await make().update_indexing_status_for_record_ids(["r1"], "NOT_STARTED") is None  # type: ignore[operator]
