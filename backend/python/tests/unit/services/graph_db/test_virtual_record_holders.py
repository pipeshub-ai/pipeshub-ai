"""`get_virtual_record_holders` — who still holds a deleted connector's shared content.

The answer picks the record a shared copy is handed over to, so it must count
trashed holders (a restore needs the content), stay inside the org, batch every
VRID into one query, and raise rather than answer "nobody" when it fails.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(execute_query):
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=execute_query)
    provider.logger = MagicMock()
    return provider


def _arango(execute_aql):
    provider = object.__new__(ArangoHTTPProvider)
    provider.http_client = MagicMock(execute_aql=execute_aql)
    provider.logger = MagicMock()
    return provider


def _row(vid, key, deleted=False):
    return {
        "vid": vid, "id": key, "connectorId": "c2", "connectorName": "DRIVE",
        "recordGroupId": "rg", "recordName": key, "webUrl": None, "isDeleted": deleted,
    }


async def test_neo4j_groups_holders_by_vrid_in_one_query():
    execute = AsyncMock(return_value=[_row("v1", "r1"), _row("v1", "r2", True), _row("v2", "r3")])
    provider = _neo4j(execute)

    got = await provider.get_virtual_record_holders(["v1", "v2", "v1"], "org1")

    assert {v: [h["id"] for h in hs] for v, hs in got.items()} == {"v1": ["r1", "r2"], "v2": ["r3"]}
    assert got["v1"][1]["isDeleted"] is True and "vid" not in got["v1"][0]
    execute.assert_awaited_once()
    params = execute.await_args.kwargs["parameters"]
    assert params == {"vrids": ["v1", "v2"], "org_id": "org1"}
    query = execute.await_args.args[0]
    assert "isDeleted" not in query.split("RETURN")[0], "trashed holders must not be filtered out"
    assert "r.orgId = $org_id" in query


async def test_arango_groups_holders_by_vrid_in_one_query():
    execute = AsyncMock(return_value=[_row("v1", "r1", True)])
    provider = _arango(execute)

    got = await provider.get_virtual_record_holders(["v1"], "org1")

    assert got == {"v1": [{k: v for k, v in _row("v1", "r1", True).items() if k != "vid"}]}
    query, bind = execute.await_args.args[0], execute.await_args.args[1]
    assert bind == {"vrids": ["v1"], "org_id": "org1"}
    assert "isDeleted" not in query.split("RETURN")[0]
    assert "r.orgId == @org_id" in query


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_no_vrids_asks_nothing(make):
    execute = AsyncMock()
    assert await make(execute).get_virtual_record_holders([], "org1") == {}
    execute.assert_not_awaited()


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_a_failed_query_raises(make):
    with pytest.raises(RuntimeError):
        await make(AsyncMock(side_effect=RuntimeError("down"))).get_virtual_record_holders(["v1"], "org1")
