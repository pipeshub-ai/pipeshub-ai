"""find_blocks_by_external_ref_prefix on Neo4j and ArangoDB: scoped to the org and connector,
prefixes passed as data, nothing queried for no prefix, and a failure raised when asked to."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

pytestmark = pytest.mark.asyncio

ARGS = {"org_id": "o1", "connector_id": "c1", "prefixes": ["requests:", "requests."], "limit": 10}


def _neo4j(rows=None, error: Exception | None = None) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=rows or [], side_effect=error)
    return provider


def _arango(rows=None, error: Exception | None = None) -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(return_value=rows or [], side_effect=error)
    return provider


async def test_neo4j_scopes_to_the_connector_and_returns_blocks_as_documents() -> None:
    provider = _neo4j([{"b": {"id": "b1", "orgId": "o1", "externalRefs": ["requests:get"]}}])
    out = await provider.find_blocks_by_external_ref_prefix(**ARGS)
    assert out[0]["_key"] == "b1" and out[0]["externalRefs"] == ["requests:get"]
    query = provider.client.execute_query.call_args.args[0]
    params = provider.client.execute_query.call_args.kwargs["parameters"]
    assert "b.orgId = $orgId AND b.connectorId = $connectorId" in query and "STARTS WITH p" in query
    assert "$groups IS NULL OR b.recordGroupId IN $groups" in query
    assert params == {"orgId": "o1", "connectorId": "c1", "prefixes": ARGS["prefixes"], "limit": 10,
                      "groups": None}


@pytest.mark.parametrize(("make", "args_of"), [
    (_neo4j, lambda p: p.client.execute_query.call_args.kwargs["parameters"]),
    (_arango, lambda p: p.http_client.execute_aql.call_args.kwargs["bind_vars"])])
async def test_record_groups_scope_the_query_itself(make, args_of) -> None:
    provider = make()
    await provider.find_blocks_by_external_ref_prefix(**ARGS, record_group_ids=["g1", "g2"])
    assert args_of(provider)["groups"] == ["g1", "g2"]


async def test_arango_scopes_to_the_connector_and_binds_the_prefixes() -> None:
    provider = _arango([{"_key": "b1"}])
    assert await provider.find_blocks_by_external_ref_prefix(**ARGS) == [{"_key": "b1"}]
    call = provider.http_client.execute_aql.call_args
    query, bind_vars = call.args[0], call.kwargs["bind_vars"]
    assert "b.orgId == @orgId AND b.connectorId == @connectorId" in query and "STARTS_WITH(r, p)" in query
    assert "@groups == null OR b.recordGroupId IN @groups" in query
    assert bind_vars == {"orgId": "o1", "connectorId": "c1", "prefixes": ARGS["prefixes"], "limit": 10,
                         "groups": None}


@pytest.mark.parametrize(("make", "query_of"), [
    (_neo4j, lambda p: p.client.execute_query), (_arango, lambda p: p.http_client.execute_aql)])
async def test_no_prefix_queries_nothing(make, query_of) -> None:
    provider = make()
    assert await provider.find_blocks_by_external_ref_prefix(**{**ARGS, "prefixes": []}) == []
    assert query_of(provider).await_count == 0


@pytest.mark.parametrize("make", [_neo4j, _arango])
async def test_a_failure_raises_when_asked_and_is_empty_otherwise(make) -> None:
    assert await make(error=RuntimeError("down")).find_blocks_by_external_ref_prefix(**ARGS) == []
    with pytest.raises(RuntimeError):
        await make(error=RuntimeError("down")).find_blocks_by_external_ref_prefix(**ARGS, raise_on_error=True)
