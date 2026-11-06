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


# The page key ends in the edge's own key, so parallel edges never tie at a page boundary.
async def test_the_arango_cursor_ends_with_the_edge_key() -> None:
    p, run = _arango()
    await p.get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS"], "outbound", after="")
    assert "edge.relationshipType, edge._key)" in run.call_args.args[0]


async def test_the_neo4j_cursor_ends_with_the_edge_key() -> None:
    p, run = _neo4j()
    await p.get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS"], "outbound", after="")
    q = run.call_args.args[0]
    assert "+ elementId(rel) AS cursor" in q
    # labels() has no order, so a label in the key could differ between two pages of the same edge.
    assert "labels(m)[0]" not in q.split("AS cursor")[0]


async def test_the_postgres_cursor_ends_with_the_edge_key() -> None:
    from app.core.org_context import OrgContext, bind
    from app.services.graph_db.postgres.codegraph import CodeGraphMixin

    seen: list[str] = []

    class Tx:
        async def fetch(self, sql: str, *args: object) -> list:
            seen.append(sql)
            return []

    class P(CodeGraphMixin):
        def __init__(self) -> None:
            self.logger = MagicMock()

        async def _q(self, fn: object, transaction: object = None) -> list:
            return await fn(Tx())  # type: ignore[operator]

    with bind(OrgContext("org-1", None, "job", "job:test", "t")):
        await P().get_neighbors_for_nodes_by_relationship_types(["a"], "blocks", ["CALLS"], "outbound", after="")
    assert "e.edge_coll, e.ekey) COLLATE" in seen[0]
