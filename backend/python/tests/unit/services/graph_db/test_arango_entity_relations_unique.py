"""entityRelations: unique (_from, _to, edgeType) index, duplicate cleanup, 1210 tolerance."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_client import ArangoQueryError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

pytestmark = pytest.mark.asyncio

EDGE = {"_from": "records/r", "_to": "users/u", "edgeType": "CREATED_BY", "createdAtTimestamp": 1}


def _provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.get_indexes = AsyncMock(return_value=[])
    return p


def _unique_violation() -> ArangoQueryError:
    return ArangoQueryError("Query failed", 409, json.dumps({"errorNum": 1210, "errorMessage": "unique constraint violated"}))


async def test_index_spec_and_cleanup_runs_before_it() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(return_value=[])
    await p._ensure_indexes()
    calls = [c for c in p.http_client.ensure_persistent_index.await_args_list if c.args[0] == "entityRelations"]
    assert [(c.args[1], c.kwargs) for c in calls] == [(["_from", "_to", "edgeType"], {"unique": True})]
    assert "COLLECT" in p.http_client.execute_aql.await_args_list[0].args[0]


async def test_cleanup_skipped_once_the_index_exists() -> None:
    p = _provider()
    p.http_client.get_indexes = AsyncMock(return_value=[{"unique": True, "fields": ["_from", "_to", "edgeType"]}])
    p.http_client.execute_aql = AsyncMock(return_value=[])
    await p._ensure_indexes()
    p.http_client.execute_aql.assert_not_awaited()


async def test_cleanup_keeps_the_oldest_and_loops_until_clean() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(side_effect=[[1, 1], [1], []])
    assert await p._dedupe_entity_relations(batch=5) == 3
    query = p.http_client.execute_aql.await_args_list[0].args[0]
    assert "SORT x.ts, x.key" in query and "SLICE(" in query and "LIMIT @batch" in query
    assert p.http_client.execute_aql.await_args_list[0].args[1]["batch"] == 5


async def test_unique_violation_on_insert_is_success() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(side_effect=[_unique_violation(), [EDGE]])
    assert await p.batch_create_entity_relations([EDGE]) is True
    assert p.http_client.execute_aql.await_count == 2


async def test_other_errors_still_raise() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(side_effect=ArangoQueryError("Query failed", 400, json.dumps({"errorNum": 1620})))
    with pytest.raises(ArangoQueryError):
        await p.batch_create_entity_relations([EDGE])
