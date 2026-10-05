"""``list_trashed_records`` on both providers: arguments, empty input and the row shape.

What the queries select (batch roots only, newest first, a file organizer's
single files, other orgs left out) is checked on real graphs in
tests/integration/test_trash_list_e2e.py.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.execute_query = AsyncMock()
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


def _query(provider) -> AsyncMock:
    return provider.execute_query if isinstance(provider, ArangoHTTPProvider) else provider.client.execute_query


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
@pytest.mark.parametrize(("connector_id", "org_id", "limit"), [("", "o1", 25), ("kb1", "", 25), ("kb1", "o1", 0)])
async def test_nothing_to_scope_by_reads_nothing(backend, connector_id, org_id, limit) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    assert await provider.list_trashed_records(connector_id, org_id, limit=limit) == {"items": [], "total": 0}
    _query(provider).assert_not_called()


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_the_page_and_the_total_are_two_scoped_reads(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    query = _query(provider)
    if backend == "arango":
        query.side_effect = [[{"record": {"_key": "r1"}, "deletedByName": ""}], [7]]
    else:
        query.side_effect = [[], [{"total": 7}]]

    found = await provider.list_trashed_records("kb1", "o1", skip=-5, limit=3, single_file_batches_only=True)

    assert found["total"] == 7
    page, count = query.await_args_list
    key = "bind_vars" if backend == "arango" else "parameters"
    for sent in (page, count):
        params = sent.kwargs[key]
        assert (params["connector_id"], params["org_id"], params["single_only"]) == ("kb1", "o1", True)
    # Paging happens inside the query, never by slicing every root in Python.
    assert (page.kwargs[key]["skip"], page.kwargs[key]["limit"]) == (0, 3)
    statement = page.args[0] if page.args else page.kwargs.get("query", "")
    assert ("LIMIT @skip, @limit" in statement) if backend == "arango" else ("SKIP $skip LIMIT $limit" in statement)
    if backend == "arango":
        assert found["items"] == [{"record": {"_key": "r1"}, "deletedByName": None}]


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_an_empty_answer_is_an_empty_page(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    _query(provider).side_effect = [[], []]
    assert await provider.list_trashed_records("kb1", "o1") == {"items": [], "total": 0}


async def test_neo4j_shapes_each_item() -> None:
    provider = _neo4j()
    rows = [
        {"rec": {"id": "new", "recordName": "Sub"}, "parent_id": "p1", "parent_name": "Docs", "parent_deleted": True,
         "is_file": False, "file_mime": None, "size": None, "batch_size": 3,
         "user_name": "Ada Admin", "user_email": "ada@acme.test"},
        {"rec": {"id": "old", "recordName": "old.pdf"}, "parent_id": None, "parent_name": None, "parent_deleted": None,
         "is_file": True, "file_mime": "application/pdf", "size": 10, "batch_size": 1,
         "user_name": "", "user_email": None},
    ]
    provider.client.execute_query = AsyncMock(side_effect=[rows, [{"total": 2}]])

    found = await provider.list_trashed_records("kb1", "o1")

    assert found["total"] == 2
    assert [item["record"]["_key"] for item in found["items"]] == ["new", "old"]
    new, old = found["items"]
    assert (new["parentId"], new["parentName"], new["parentIsDeleted"]) == ("p1", "Docs", True)
    assert (new["isFile"], new["batchSize"], new["deletedByName"], new["deletedByEmail"]) == (
        False, 3, "Ada Admin", "ada@acme.test",
    )
    assert (old["parentId"], old["parentIsDeleted"], old["deletedByName"], old["sizeInBytes"]) == (None, None, None, 10)


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_read_raises(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    _query(provider).side_effect = RuntimeError("graph down")
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.list_trashed_records("kb1", "o1")
