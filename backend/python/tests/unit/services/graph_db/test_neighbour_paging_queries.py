"""The paged neighbour query on Neo4j and ArangoDB: ordered by the cursor, resumed after it, and unchanged when no cursor is asked for."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j() -> tuple[Neo4jProvider, AsyncMock]:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[{"cursor": "c1", "anchorKey": "a", "labels": ["Block"], "key": "b"}])
    return p, p.client.execute_query


def _arango() -> tuple[ArangoHTTPProvider, AsyncMock]:
    p = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    p.http_client = MagicMock()
    p.http_client.execute_aql = AsyncMock(return_value=[{"cursor": "c1", "key": "b"}])
    return p, p.http_client.execute_aql


@pytest.mark.parametrize("make, bind_key", [(_neo4j, "parameters"), (_arango, "bind_vars")])
async def test_a_paged_call_orders_by_the_cursor_and_binds_after(make, bind_key) -> None:
    p, run = make()
    await p.get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS"], "any", limit=3, after="x")
    query, binds = run.call_args.args[0], run.call_args.kwargs[bind_key]
    assert binds["after"] == "x" and "cursor" in query and ("ORDER BY cursor" in query or "SORT row.cursor" in query)


@pytest.mark.parametrize("make, bind_key", [(_neo4j, "parameters"), (_arango, "bind_vars")])
async def test_an_unpaged_call_is_the_old_query(make, bind_key) -> None:
    p, run = make()
    await p.get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS"], "any", limit=3)
    query, binds = run.call_args.args[0], run.call_args.kwargs[bind_key]
    assert "after" not in binds and "cursor" not in query and "ORDER BY" not in query and "SORT" not in query
