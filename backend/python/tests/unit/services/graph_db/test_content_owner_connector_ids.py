"""`get_content_owner_connector_ids`: which other connectors store content that a
request scope's records share.

Keyword search greps those connectors' storage, so the query must stay inside
the org on both sides, count only content live records of the scope hold, never
name a scope connector, and raise rather than answer "nothing shared".
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import (
    aql_live_record,
    cypher_live_record,
)
from app.services.graph_db.interface.graph_db_provider import SharedContentCount
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ROWS = [
    {"cid": "kb-archive", "shared": 812, "records": 2400},
    {"cid": None, "shared": 1, "records": 1},
    {"cid": "kb-empty", "shared": 1, "records": None},
]


def _neo4j(run: AsyncMock) -> Neo4jProvider:
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=run)
    provider.logger = MagicMock()
    return provider


def _arango(run: AsyncMock) -> ArangoHTTPProvider:
    provider = object.__new__(ArangoHTTPProvider)
    provider.http_client = MagicMock(execute_aql=run)
    provider.logger = MagicMock()
    return provider


async def _query(make, run: AsyncMock) -> tuple[str, dict]:
    await make(run).get_content_owner_connector_ids(["kb-policies", "kb-handbook", "kb-policies"], "org-1")
    call = run.await_args
    params = call.kwargs.get("parameters") if "parameters" in call.kwargs else call.args[1]
    return call.args[0], params


@pytest.mark.asyncio
@pytest.mark.parametrize(("make", "live"), [(_neo4j, cypher_live_record("r")), (_arango, aql_live_record("r"))])
async def test_the_query_is_bounded_by_org_scope_and_liveness(make, live) -> None:
    query, params = await _query(make, AsyncMock(return_value=[]))

    assert params == {"scope_ids": ["kb-handbook", "kb-policies"], "org_id": "org-1"}
    assert len(re.findall(r"\borgId (?:=|==) [$@]org_id", query)) == 3
    assert "NOT o.connectorId IN $scope_ids" in query or "o.connectorId NOT IN @scope_ids" in query
    assert live in query


@pytest.mark.asyncio
@pytest.mark.parametrize(("make", "option"), [(_neo4j, "timeout"), (_arango, "max_runtime")])
async def test_the_server_stops_the_query_at_the_callers_limit(make, option) -> None:
    run = AsyncMock(return_value=[])

    await make(run).get_content_owner_connector_ids(["kb-handbook"], "org-1", timeout_seconds=4.0)
    await make(run).get_content_owner_connector_ids(["kb-handbook"], "org-1")

    assert run.await_args_list[0].kwargs[option] == 4.0
    assert option not in run.await_args_list[1].kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_rows_without_a_connector_are_dropped(make) -> None:
    result = await make(AsyncMock(return_value=ROWS)).get_content_owner_connector_ids(["kb-handbook"], "org-1")

    assert result == {
        "kb-archive": SharedContentCount(shared=812, records=2400),
        "kb-empty": SharedContentCount(shared=1, records=0),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango])
@pytest.mark.parametrize(("scope", "org"), [([], "org-1"), (["kb-handbook"], "")])
async def test_nothing_to_look_up(make, scope, org) -> None:
    run = AsyncMock()

    assert await make(run).get_content_owner_connector_ids(scope, org) == {}
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_a_failure_raises(make) -> None:
    with pytest.raises(RuntimeError):
        await make(AsyncMock(side_effect=RuntimeError("down"))).get_content_owner_connector_ids(["kb-handbook"], "org-1")
