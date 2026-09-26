"""`get_virtual_record_ids_shared_outside_connector` — which of a connector's
VRIDs other connectors' records still hold.

Deleting a connector re-indexes the surviving holders of these VRIDs, because
deduplicated content is stored once, under whichever connector indexed it first.
Missing one leaves a record in another connector reading storage that is gone;
so the query must count every other connector, never soft-deleted records, and
must raise rather than answer "nothing is shared" when it fails.
"""

import pathlib
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

NAME = "get_virtual_record_ids_shared_outside_connector"


def _method_source(path: str, name: str) -> str:
    # Both backends need a live database to run, so the query text is checked.
    text = pathlib.Path(path).read_text(encoding="utf-8")
    rest = text[text.index(f"async def {name}("):]
    match = re.search(r"\n    (?:async )?def ", rest[10:])
    return rest[: match.start() + 10] if match else rest


SOURCES = {
    "arango": "app/services/graph_db/arango/arango_http_provider.py",
    "neo4j": "app/services/graph_db/neo4j/neo4j_provider.py",
}


@pytest.fixture(params=sorted(SOURCES), ids=sorted(SOURCES))
def source(request) -> str:
    return _method_source(SOURCES[request.param], NAME)


def test_counts_only_other_connectors(source):
    assert "<> $connector_id" in source or "!= @connector_id" in source


def test_ignores_soft_deleted_holders(source):
    assert "coalesce(o.isDeleted, false) = false" in source or "o.isDeleted != true" in source


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


class TestExecution:
    @pytest.mark.asyncio
    async def test_neo4j_returns_the_shared_vrids(self):
        provider = _neo4j(AsyncMock(return_value=[{"vid": "v1"}, {"vid": None}, {"vid": "v2"}]))

        assert await provider.get_virtual_record_ids_shared_outside_connector("c1") == ["v1", "v2"]
        assert provider.client.execute_query.await_args.kwargs["parameters"] == {"connector_id": "c1"}

    @pytest.mark.asyncio
    async def test_arango_returns_the_shared_vrids(self):
        provider = _arango(AsyncMock(return_value=["v1", None, "v2"]))

        assert await provider.get_virtual_record_ids_shared_outside_connector("c1") == ["v1", "v2"]
        assert provider.http_client.execute_aql.await_args.args[1] == {"connector_id": "c1"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("make", [
        lambda: _neo4j(AsyncMock(side_effect=RuntimeError("down"))),
        lambda: _arango(AsyncMock(side_effect=RuntimeError("down"))),
    ], ids=["neo4j", "arango"])
    async def test_a_failed_query_raises_instead_of_reporting_nothing_shared(self, make):
        with pytest.raises(RuntimeError):
            await make().get_virtual_record_ids_shared_outside_connector("c1")
