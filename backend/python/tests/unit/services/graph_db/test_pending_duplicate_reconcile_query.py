"""On both providers: ``get_records_pending_duplicate_reconcile`` (records
whose duplicate reconcile is pending and due, served from an index rather than
a scan of every record) and ``update_node_fields_if_match`` (the merge write
that clears the flag only if no newer promotion re-armed it)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(rows: list | Exception) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


def _arango(rows: list | Exception) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = (
        AsyncMock(side_effect=rows) if isinstance(rows, Exception) else AsyncMock(return_value=rows)
    )
    return p


ROW = {"_key": "r1", "duplicateReconcileAttempts": 2, "duplicateReconcileDueAt": 99}


class TestNeo4j:
    async def test_query_and_rows(self) -> None:
        p = _neo4j([dict(ROW)])
        rows = await p.get_records_pending_duplicate_reconcile(due_before_ms=123, limit=20)
        query = p.client.execute_query.await_args.args[0]
        params = p.client.execute_query.await_args.kwargs["parameters"]
        assert "MATCH (r:Record)" in query
        assert "r.duplicateReconcilePending = true" in query
        assert "coalesce(r.duplicateReconcileDueAt, 0) < $due_before_ms" in query
        assert "LIMIT $limit" in query
        assert params == {"due_before_ms": 123, "limit": 20}
        assert rows == [ROW]

    def test_flag_is_indexed(self) -> None:
        statements = _neo4j([])._generate_performance_indexes()
        assert any(
            "FOR (n:Record) ON (n.duplicateReconcilePending, n.duplicateReconcileDueAt)" in s
            for s in statements
        )


class TestArango:
    async def test_query_and_rows(self) -> None:
        p = _arango([dict(ROW)])
        rows = await p.get_records_pending_duplicate_reconcile(due_before_ms=123, limit=20)
        query = p.http_client.execute_aql.await_args.args[0]
        binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert "FILTER r.duplicateReconcilePending == true" in query
        assert "FILTER NOT_NULL(r.duplicateReconcileDueAt, 0) < @due_before_ms" in query
        assert "LIMIT @limit" in query
        assert binds == {"due_before_ms": 123, "limit": 20}
        assert rows == [ROW]

    async def test_flag_is_indexed(self) -> None:
        p = _arango([])
        p.http_client.ensure_persistent_index = AsyncMock()
        await p._ensure_indexes()
        fields = [tuple(c.args[1]) for c in p.http_client.ensure_persistent_index.await_args_list
                  if c.args[0] == "records"]
        assert ("duplicateReconcilePending", "duplicateReconcileDueAt") in fields


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestBoth:
    async def test_failure_raises(self, make) -> None:
        p = make(RuntimeError("db down"))
        with pytest.raises(RuntimeError, match="db down"):
            await p.get_records_pending_duplicate_reconcile(due_before_ms=1, limit=5)

    async def test_limit_is_at_least_one(self, make) -> None:
        p = make([])
        await p.get_records_pending_duplicate_reconcile(due_before_ms=1, limit=0)
        call = (p.client.execute_query if isinstance(p, Neo4jProvider) else p.http_client.execute_aql).await_args
        params = call.kwargs.get("parameters") or call.kwargs.get("bind_vars")
        assert params["limit"] == 1


class TestUpdateFieldsIfMatch:
    """Merges ``updates`` (never replaces the document) only while every
    ``expected`` field still holds; a None expectation means absent."""

    async def test_neo4j(self) -> None:
        p = _neo4j([{"n": 1}])
        ok = await p.update_node_fields_if_match(
            "r1", "records", {"a": 1, "b": None}, {"c": True, "d": None},
        )
        query = p.client.execute_query.await_args.args[0]
        params = p.client.execute_query.await_args.kwargs["parameters"]
        assert ok is True
        assert "MATCH (n:Record {id: $key})" in query
        assert "n[$f0] = $v0" in query and "n[$f1] IS NULL" in query
        assert "SET n += $updates" in query and "SET n = " not in query
        assert params["f0"] == "c" and params["v0"] is True and params["f1"] == "d"
        assert params["updates"] == {"a": 1, "b": None}

    async def test_neo4j_no_match(self) -> None:
        p = _neo4j([])
        assert await p.update_node_fields_if_match("r1", "records", {"a": 1}, {"c": True}) is False

    async def test_arango(self) -> None:
        p = _arango([1])
        ok = await p.update_node_fields_if_match(
            "r1", "records", {"a": 1, "b": None}, {"c": True, "d": None},
        )
        query = p.http_client.execute_aql.await_args.args[0]
        binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert ok is True
        assert "FILTER doc[@f0] == @v0" in query and "FILTER doc[@f1] == null" in query
        assert "UPDATE doc WITH @updates IN @@collection" in query
        assert "REPLACE" not in query
        assert binds["@collection"] == "records" and binds["updates"] == {"a": 1, "b": None}

    async def test_arango_no_match(self) -> None:
        p = _arango([])
        assert await p.update_node_fields_if_match("r1", "records", {"a": 1}, {"c": True}) is False

    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_requires_an_expectation(self, make) -> None:
        with pytest.raises(ValueError):
            await make([]).update_node_fields_if_match("r1", "records", {"a": 1}, {})
