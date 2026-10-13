"""The Neo4j purge walk's index hint, and what happens when its index is not ready.

On a server with ``dbms.cypher.hints_error`` on, a hinted query whose index is
missing or still building fails with ``Neo.ClientError.Schema.IndexNotFound``
(checked on Neo4j 5.26). The walk then drops the hint for that call and says so
once; any other error is raised.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import Neo4jError

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

HINT = "USING INDEX r:Record(orgId, deletedAtTimestamp, id)"


def _error(code: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message="No such index")


def _row(key: str) -> dict:
    return {"rec": {"id": key, "orgId": "org-1", "deletedAtTimestamp": 5, "connectorId": "c"},
            "type_doc": None, "skip": False, "held": False}


def _provider(execute: AsyncMock) -> Neo4jProvider:
    provider = Neo4jProvider(logging.getLogger("purge-walk-test"), MagicMock())
    provider.client = MagicMock()
    provider.client.execute_query = execute
    return provider


async def test_the_walk_hints_both_seeks() -> None:
    execute = AsyncMock(return_value=[_row("a")])
    provider = _provider(execute)

    page = await provider.get_purgeable_trashed_records("org-1", 10, limit=5)

    [query] = [call.args[0] for call in execute.await_args_list]
    assert query.count(HINT) == 2
    assert [r["id"] for r in page["records"]] == ["a"]


async def test_without_its_index_the_walk_drops_the_hint_and_says_so_once(caplog: pytest.LogCaptureFixture) -> None:
    missing = _error("Neo.ClientError.Schema.IndexNotFound")
    execute = AsyncMock(side_effect=[missing, [_row("a")], missing, [_row("b")]])
    provider = _provider(execute)

    with caplog.at_level(logging.WARNING):
        first = await provider.get_purgeable_trashed_records("org-1", 10, limit=5)
        second = await provider.get_purgeable_trashed_records("org-1", 10, after=(5, "a"), limit=5)

    queries = [call.args[0] for call in execute.await_args_list]
    assert [HINT in q for q in queries] == [True, False, True, False]
    assert [r["id"] for r in first["records"] + second["records"]] == ["a", "b"]
    assert caplog.text.count("walking the trash without it") == 1


async def test_any_other_error_is_raised() -> None:
    provider = _provider(AsyncMock(side_effect=_error("Neo.ClientError.Statement.SyntaxError")))
    with pytest.raises(Neo4jError):
        await provider.get_purgeable_trashed_records("org-1", 10, limit=5)


async def test_the_index_counts_as_ready_only_when_online() -> None:
    for rows, ready in (([{"state": "ONLINE"}], True), ([{"state": "POPULATING"}], False), ([], False)):
        assert await _provider(AsyncMock(return_value=rows)).is_trash_walk_index_ready() is ready


async def test_the_index_is_found_by_its_definition_not_its_name() -> None:
    """CREATE INDEX ... IF NOT EXISTS keeps an equivalent index under its own name."""
    execute = AsyncMock(return_value=[{"state": "ONLINE"}])
    assert await _provider(execute).is_trash_walk_index_ready() is True
    query = execute.await_args.args[0]
    assert "properties = ['orgId', 'deletedAtTimestamp', 'id']" in query
    assert "labelsOrTypes = ['Record']" in query
    assert "name" not in query
