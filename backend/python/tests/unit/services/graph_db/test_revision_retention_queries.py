"""The retention sweep's provider reads and deletes, without a database.

The rules themselves are checked against real engines in
``tests/integration/test_revision_retention_e2e.py``; these pin batching,
empty inputs and that failures raise.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(execute: AsyncMock) -> Neo4jProvider:
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=execute)
    provider.logger = MagicMock()
    return provider


def _arango(execute: AsyncMock) -> ArangoHTTPProvider:
    provider = object.__new__(ArangoHTTPProvider)
    provider.http_client = MagicMock(execute_aql=execute)
    provider.logger = MagicMock()
    return provider


Make = Callable[[AsyncMock], Any]


def _params(make: Make, execute: AsyncMock, index: int = -1) -> dict[str, Any]:
    call = execute.await_args_list[index]
    return call.kwargs["parameters"] if make is _neo4j else call.args[1]


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_empty_inputs_ask_nothing(make: Make) -> None:
    execute = AsyncMock()
    provider = make(execute)
    assert await provider.stage_states_unheld_revisions(["", ""], updated_before_ms=5) == []
    assert await provider.stage_states_delete_unheld_revisions([("v", ""), ("", "r")], updated_before_ms=5) == 0
    assert await provider.get_revision_pointers(None, 0) == []
    execute.assert_not_awaited()


async def test_neo4j_unheld_revisions_batch_by_a_thousand_virtual_records() -> None:
    execute = AsyncMock(return_value=[{"vrid": "v1", "rev": "r1"}])
    provider = _neo4j(execute)

    got = await provider.stage_states_unheld_revisions([f"v{i}" for i in range(1500)], updated_before_ms=7)

    assert got == [("v1", "r1"), ("v1", "r1")]
    assert [len(_params(_neo4j, execute, i)["ids"]) for i in range(2)] == [1000, 500]
    assert _params(_neo4j, execute, 0)["before"] == 7


async def test_arango_unheld_revisions_read_pairs() -> None:
    execute = AsyncMock(return_value=[["v1", "r1"]])
    provider = _arango(execute)

    assert await provider.stage_states_unheld_revisions(["v1", "v1"], updated_before_ms=7) == [("v1", "r1")]
    bind = execute.await_args.args[1]
    assert bind["ids"] == ["v1"] and bind["before"] == 7


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_the_delete_sends_sorted_unique_pairs_and_sums_counts(make: Make) -> None:
    execute = AsyncMock(return_value=[{"deleted": 3}] if make is _neo4j else [3])
    provider = make(execute)

    deleted = await provider.stage_states_delete_unheld_revisions(
        [("v2", "r1"), ("v1", "r2"), ("v2", "r1")], updated_before_ms=9
    )

    assert deleted == 3
    params = _params(make, execute)
    assert params["pairs"] == [["v1", "r2"], ["v2", "r1"]] and params["before"] == 9


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_the_pointer_page_continues_after_the_key(make: Make) -> None:
    row = {"virtualRecordId": "v2", "orgId": "o", "currentRev": "r2", "previousRev": None}
    execute = AsyncMock(return_value=[row])
    provider = make(execute)

    assert await provider.get_revision_pointers("v1", 50) == [row]
    params = _params(make, execute)
    assert params["after_key"] == "v1" and params["limit"] == 50


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_failures_raise(make: Make) -> None:
    provider = make(AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(RuntimeError):
        await provider.get_revision_pointers(None, 10)
    with pytest.raises(RuntimeError):
        await provider.stage_states_unheld_revisions(["v"], updated_before_ms=1)
    with pytest.raises(RuntimeError):
        await provider.stage_states_delete_unheld_revisions([("v", "r")], updated_before_ms=1)
