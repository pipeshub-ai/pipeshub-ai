"""`get_records_by_external_ids`: the indexed copies of live items (P1b C-18).

One query per call on both backends, scoped to the caller's org, the asked
connector kinds and records that are not deleted. A failed read raises: an
empty answer would read as "no indexed copy".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import GraphQueryError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ROWS = [
    {"id": "r1", "externalRecordId": "f1", "connectorName": "DRIVE"},
    {"id": "r2", "externalRecordId": "f1", "connectorName": "DRIVE WORKSPACE"},
]


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    return provider


class TestArango:
    @pytest.mark.asyncio
    async def test_one_query_scoped_to_org_connectors_and_live_records(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(return_value=ROWS)
        rows = await provider.get_records_by_external_ids("org-1", ["f1", "f2", "f1"], ["DRIVE", "DRIVE WORKSPACE"])
        assert rows == ROWS
        provider.http_client.execute_aql.assert_awaited_once()
        query = provider.http_client.execute_aql.await_args.args[0]
        bind = provider.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert bind["org_id"] == "org-1"
        assert bind["external_ids"] == ["f1", "f2"]
        assert bind["connector_names"] == ["DRIVE", "DRIVE WORKSPACE"]
        for clause in ("externalRecordId IN @external_ids", "orgId == @org_id", "connectorName IN @connector_names", "isDeleted != true"):
            assert clause in query

    @pytest.mark.asyncio
    async def test_nothing_asked_is_no_query(self) -> None:
        provider = _arango()
        assert await provider.get_records_by_external_ids("org-1", [], ["DRIVE"]) == []
        provider.http_client.execute_aql.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_raises(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("down"))
        with pytest.raises(GraphQueryError):
            await provider.get_records_by_external_ids("org-1", ["f1"], ["DRIVE"])


class TestNeo4j:
    @pytest.mark.asyncio
    async def test_one_query_scoped_to_org_connectors_and_live_records(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=ROWS)
        rows = await provider.get_records_by_external_ids("org-1", ["f1", "f1"], ["DRIVE"])
        assert rows == ROWS
        provider.client.execute_query.assert_awaited_once()
        query = provider.client.execute_query.await_args.args[0]
        params = provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["org_id"] == "org-1"
        assert params["external_ids"] == ["f1"]
        assert params["connector_names"] == ["DRIVE"]
        assert "UNWIND $external_ids" in query
        # The existence predicate lets the (externalRecordId, connectorId) index serve the lookup.
        assert "r.connectorId IS NOT NULL" in query
        assert "r.orgId = $org_id" in query
        assert "r.connectorName IN $connector_names" in query

    @pytest.mark.asyncio
    async def test_nothing_asked_is_no_query(self) -> None:
        provider = _neo4j()
        assert await provider.get_records_by_external_ids("org-1", ["f1"], []) == []
        provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_read_raises(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("down"))
        with pytest.raises(GraphQueryError):
            await provider.get_records_by_external_ids("org-1", ["f1"], ["DRIVE"])
