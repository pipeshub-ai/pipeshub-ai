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


async def test_failed_cleanup_or_index_build_does_not_stop_setup() -> None:
    p = _provider()
    p._dedupe_entity_relations = AsyncMock(side_effect=RuntimeError("boom"))
    p.http_client.ensure_persistent_index = AsyncMock(return_value=False)  # duplicates left: build refused
    await p._ensure_indexes()
    assert p.http_client.ensure_persistent_index.await_count > 1


async def test_unique_violation_inside_a_transaction_is_not_retried() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(side_effect=_unique_violation())
    with pytest.raises(ArangoQueryError):
        await p.batch_create_entity_relations([EDGE], transaction="txn1")
    assert p.http_client.execute_aql.await_count == 1


async def test_cleanup_ends_when_a_pass_removes_nothing_whatever_the_client_answers() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(return_value=MagicMock())  # truthy, with no rows: this once looped for ever
    assert await p._dedupe_entity_relations() == 0
    assert p.http_client.execute_aql.await_count == 1


async def test_cleanup_stops_at_its_pass_limit() -> None:
    p = _provider()
    p.http_client.execute_aql = AsyncMock(return_value=[1])
    assert await p._dedupe_entity_relations() == p.http_client.execute_aql.await_count


async def test_a_write_conflict_outside_a_transaction_is_retried() -> None:
    p = _provider()
    conflict = Exception('write-write conflict "errorNum": 1200')
    p.http_client.execute_aql = AsyncMock(side_effect=[conflict, [{"_key": "e"}]])
    assert await p._upsert_entity_relations([], "UPDATE edge", None) == [{"_key": "e"}]


async def test_a_write_conflict_on_the_retry_after_a_unique_violation_is_retried_too() -> None:
    p = _provider()
    conflict = Exception('write-write conflict "errorNum": 1200')
    p.http_client.execute_aql = AsyncMock(side_effect=[_unique_violation(), conflict, [{"_key": "e"}]])
    assert await p._upsert_entity_relations([], "UPDATE edge", None) == [{"_key": "e"}]


async def test_an_unreadable_index_list_does_not_stop_setup() -> None:
    p = _provider()
    p.http_client.get_indexes = AsyncMock(side_effect=RuntimeError("index list unavailable"))
    p.http_client.execute_aql = AsyncMock(return_value=[])
    await p._ensure_indexes()
    p.http_client.execute_aql.assert_awaited()  # the cleanup still runs, so duplicates can't block the build
    fields = [c.args[1] for c in p.http_client.ensure_persistent_index.await_args_list]
    assert ["_from", "_to", "edgeType"] in fields and ["tableFqn", "orgId"] in fields
