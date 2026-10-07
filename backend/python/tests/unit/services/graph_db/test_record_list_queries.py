"""The All Records and KB record list queries: bind parameters and failed reads.

ArangoDB rejects a query that is sent a bind parameter it does not declare, as
well as one that is missing a parameter it uses. Both list queries used to fail
that way on every call, and the error was swallowed into an empty page. These
tests compare what each query declares with what it is sent, for every source
and filter combination, and check that a failed read raises.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_BIND = re.compile(r"@@?[A-Za-z_][A-Za-z0-9_]*")


def _declared(query: str) -> set[str]:
    return {m.lstrip("@") if not m.startswith("@@") else m[1:] for m in _BIND.findall(query)}


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


FILTERS = {
    "search": "report", "record_types": ["FILE"], "origins": ["UPLOAD"], "connectors": ["DRIVE"],
    "indexing_status": ["COMPLETED"], "date_from": 1, "date_to": 2,
}


@pytest.mark.parametrize("source", ["all", "local", "connector"])
@pytest.mark.parametrize("permissions", [None, ["READER"], ["OWNER", "WRITER"]])
@pytest.mark.parametrize("filtered", [False, True])
async def test_all_records_sends_exactly_the_binds_it_declares(source, permissions, filtered) -> None:
    provider = _arango()
    provider.execute_query = AsyncMock(return_value=[{"records": [], "total": 0}])
    filters = FILTERS if filtered else dict.fromkeys(FILTERS)
    await provider.list_all_records(
        "uk1", "org1", 0, 10, permissions=permissions, sort_by="recordName", sort_order="desc",
        source=source, **filters,
    )
    (call,) = provider.execute_query.await_args_list
    query, binds = call.args[0], call.kwargs["bind_vars"]
    assert _declared(query) == set(binds)


@pytest.mark.parametrize("folder_id", [None, "f1"])
@pytest.mark.parametrize("filtered", [False, True])
async def test_kb_records_sends_exactly_the_binds_it_declares(folder_id, filtered) -> None:
    provider = _arango()
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[{"records": [], "total": 0}], [[]]])
    filters = FILTERS if filtered else dict.fromkeys(FILTERS)
    filters = {k: v for k, v in filters.items() if k != "permissions"}
    await provider.list_kb_records(
        "kb1", "uk1", "org1", 0, 10, sort_by="recordName", sort_order="asc", folder_id=folder_id, **filters,
    )
    for call in provider.execute_query.await_args_list:
        assert _declared(call.args[0]) == set(call.kwargs["bind_vars"])


async def test_an_unknown_sort_field_is_not_spliced_into_the_query() -> None:
    provider = _arango()
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[{"records": [], "total": 0}], [[]]])
    await provider.list_kb_records(
        "kb1", "uk1", "org1", 0, 10, None, None, None, None, None, None, None,
        sort_by="x REMOVE record IN records", sort_order="asc; drop",
    )
    query = provider.execute_query.await_args_list[0].args[0]
    assert "REMOVE" not in query and "drop" not in query
    assert "SORT record.recordName ASC" in query


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    return provider


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_all_records_read_raises(backend) -> None:
    """An empty page tells the user they have no records; a failure must not."""
    if backend == "arango":
        provider = _arango()
        provider.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    else:
        provider = _neo4j()
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.list_all_records(
            "uk1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all",
        )


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_kb_records_read_raises(backend) -> None:
    if backend == "arango":
        provider = _arango()
        provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
        provider.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    else:
        provider = _neo4j()
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.list_kb_records(
            "kb1", "uk1", "org1", 0, 10, None, None, None, None, None, None, None, "recordName", "asc",
        )


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_get_records_takes_the_graph_key_on_both_backends(backend) -> None:
    """/api/v1/records resolves the caller and passes the users node key."""
    provider = _arango() if backend == "arango" else _neo4j()
    provider.list_all_records = AsyncMock(return_value=([{"id": "r1"}], 1, {}))
    args = ("uk1", "org1", 0, 10, None, None, None, None, None, None, None, None, "recordName", "asc", "all")
    assert await provider.get_records(*args) == ([{"id": "r1"}], 1, {})
    provider.list_all_records.assert_awaited_once_with(*args)


async def test_a_user_on_two_teams_is_deduplicated_to_one_grant_per_kb() -> None:
    provider = _arango()
    provider.execute_query = AsyncMock(return_value=[{"records": [], "total": 0}])
    await provider.list_all_records("uk1", "org1", 0, 10, None, None, None, None, None, None, None, None,
                                    "recordName", "asc", "all")
    query = provider.execute_query.await_args.args[0]
    assert "COLLECT kb_id = access.kb_id" in query
    assert "FOR access IN allKbAccess" in query
