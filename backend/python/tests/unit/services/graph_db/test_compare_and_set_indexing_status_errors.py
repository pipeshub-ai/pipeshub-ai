"""compare_and_set_indexing_status answers [] only when no record held the expected status; a database error is raised."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

BOOM = ConnectionError("the database dropped the connection")


def _neo4j(result: object) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
    return p


def _arango(result: object) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
    return p


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestCompareAndSetIndexingStatus:
    async def test_a_database_error_is_raised_not_answered_as_no_match(self, make) -> None:
        with pytest.raises(ConnectionError, match="dropped"):
            await make(BOOM).compare_and_set_indexing_status(["r1"], "QUEUED", "FAILED")

    async def test_no_record_holding_the_expected_status_is_an_empty_list(self, make) -> None:
        assert await make([]).compare_and_set_indexing_status(["r1"], "QUEUED", "FAILED") == []

    async def test_the_updated_ids_are_returned(self, make) -> None:
        rows = [{"id": "r1"}] if make is _neo4j else ["r1"]
        assert await make(rows).compare_and_set_indexing_status(["r1", "r2"], "QUEUED", "FAILED") == ["r1"]

    async def test_no_ids_is_an_empty_list_without_asking_the_database(self, make) -> None:
        p = make(BOOM)
        assert await p.compare_and_set_indexing_status([], "QUEUED", "FAILED") == []
