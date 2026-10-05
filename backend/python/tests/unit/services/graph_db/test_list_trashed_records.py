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


async def test_arango_scopes_the_query_by_org_and_connector_and_pages() -> None:
    provider = _arango()
    provider.execute_query.return_value = [{"items": [{"record": {"_key": "r1"}, "deletedByName": ""}], "total": 7}]

    found = await provider.list_trashed_records("kb1", "o1", skip=-5, limit=3, single_file_batches_only=True)

    assert found == {"items": [{"record": {"_key": "r1"}, "deletedByName": None}], "total": 7}
    bind = provider.execute_query.await_args.kwargs["bind_vars"]
    assert (bind["connector_id"], bind["org_id"], bind["skip"], bind["limit"], bind["single_only"]) == (
        "kb1", "o1", 0, 3, True,
    )


async def test_arango_an_empty_answer_is_an_empty_page() -> None:
    provider = _arango()
    provider.execute_query.return_value = []
    assert await provider.list_trashed_records("kb1", "o1") == {"items": [], "total": 0}


async def test_neo4j_an_empty_page_skips_the_detail_read() -> None:
    provider = _neo4j()
    provider.client.execute_query = AsyncMock(return_value=[{"total": 4, "page": []}])

    assert await provider.list_trashed_records("kb1", "o1", skip=30, limit=10) == {"items": [], "total": 4}

    provider.client.execute_query.assert_awaited_once()
    params = provider.client.execute_query.await_args.kwargs["parameters"]
    assert (params["connector_id"], params["org_id"], params["skip"], params["end"]) == ("kb1", "o1", 30, 40)


async def test_neo4j_keeps_the_page_order_and_shapes_each_item() -> None:
    provider = _neo4j()
    page = [
        {"id": "new", "parent": {"id": "p1", "name": "Docs", "deleted": True}},
        {"id": "gone", "parent": None},
        {"id": "old", "parent": None},
    ]
    details = [
        {"id": "old", "rec": {"id": "old", "recordName": "old.pdf"}, "is_file": True, "file_mime": "application/pdf",
         "size": 10, "batch_size": 1, "user_name": "", "user_email": None},
        {"id": "new", "rec": {"id": "new", "recordName": "Sub"}, "is_file": False, "file_mime": None,
         "size": None, "batch_size": 3, "user_name": "Ada Admin", "user_email": "ada@acme.test"},
    ]
    provider.client.execute_query = AsyncMock(side_effect=[[{"total": 3, "page": page}], details])

    found = await provider.list_trashed_records("kb1", "o1")

    assert found["total"] == 3
    assert [item["record"]["_key"] for item in found["items"]] == ["new", "old"]
    new, old = found["items"]
    assert (new["parentId"], new["parentName"], new["parentIsDeleted"]) == ("p1", "Docs", True)
    assert (new["isFile"], new["batchSize"], new["deletedByName"], new["deletedByEmail"]) == (
        False, 3, "Ada Admin", "ada@acme.test",
    )
    assert (old["parentId"], old["parentIsDeleted"], old["deletedByName"], old["sizeInBytes"]) == (None, None, None, 10)
    second = provider.client.execute_query.await_args_list[1].kwargs["parameters"]
    assert second == {"ids": ["new", "gone", "old"], "connector_id": "kb1"}


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_a_failed_read_raises(backend) -> None:
    provider = _arango() if backend == "arango" else _neo4j()
    _query(provider).side_effect = RuntimeError("graph down")
    with pytest.raises(RuntimeError, match="graph down"):
        await provider.list_trashed_records("kb1", "o1")
