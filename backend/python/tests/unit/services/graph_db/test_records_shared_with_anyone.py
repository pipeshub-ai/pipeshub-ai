"""``filter_records_shared_with_anyone`` on both providers (KG-37): the
records of a list that are shared with anyone in the org, so the entity
tools see what content search already returns for them."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(rows: list | Exception) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    return p


def _arango(rows: list | Exception) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    return p


async def test_neo4j_query() -> None:
    p = _neo4j([{"id": "r1"}])
    shared = await p.filter_records_shared_with_anyone(["r1", "r2", "r1"], "org-1")
    query = p.client.execute_query.await_args.args[0]
    params = p.client.execute_query.await_args.kwargs["parameters"]
    assert "MATCH (a:Anyone)" in query
    assert "a.file_key IN $record_ids AND a.organization = $org_id" in query
    assert "coalesce(a.active, true) = true" in query
    assert params == {"record_ids": ["r1", "r2"], "org_id": "org-1"}
    assert shared == {"r1"}


async def test_arango_query() -> None:
    p = _arango(["r1"])
    shared = await p.filter_records_shared_with_anyone(["r1", "r2"], "org-1")
    query = p.http_client.execute_aql.await_args.args[0]
    binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
    assert "FILTER a.file_key IN @record_ids AND a.organization == @org_id" in query
    assert "FILTER a.active == true" in query
    assert binds["@anyone"] == "anyone" and binds["record_ids"] == ["r1", "r2"]
    assert shared == {"r1"}


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestBoth:
    async def test_nothing_to_check_makes_no_query(self, make) -> None:
        p = make([])
        assert await p.filter_records_shared_with_anyone([], "org-1") == set()
        assert await p.filter_records_shared_with_anyone(["r1"], "") == set()
        client = p.client.execute_query if isinstance(p, Neo4jProvider) else p.http_client.execute_aql
        client.assert_not_awaited()

    async def test_failure_raises(self, make) -> None:
        with pytest.raises(RuntimeError):
            await make(RuntimeError("down")).filter_records_shared_with_anyone(["r1"], "org-1")
