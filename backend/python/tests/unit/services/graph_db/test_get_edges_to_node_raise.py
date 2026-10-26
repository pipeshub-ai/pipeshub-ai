"""``get_edges_to_node(raise_on_error=True)`` lets a caller tell a failed read
from "no edges". The full-sync membership step needs it: a failed read taken
for "no members" left a group holding a grant with nobody on it. The default
still swallows, so existing callers are unchanged."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(error: Exception | None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=error) if error else AsyncMock(return_value=[])
    return p


def _arango(error: Exception | None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=error) if error else AsyncMock(return_value=[])
    return p


@pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
class TestRaiseOnError:
    async def test_failure_is_swallowed_by_default(self, make) -> None:
        p = make(RuntimeError("db down"))
        assert await p.get_edges_to_node("groups/g1", "permission") == []

    async def test_failure_is_raised_when_requested(self, make) -> None:
        p = make(RuntimeError("db down"))
        with pytest.raises(RuntimeError, match="db down"):
            await p.get_edges_to_node("groups/g1", "permission", raise_on_error=True)

    async def test_success_is_unchanged(self, make) -> None:
        p = make(None)
        assert await p.get_edges_to_node("groups/g1", "permission", raise_on_error=True) == []
