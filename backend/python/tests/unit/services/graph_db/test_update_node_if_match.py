"""`IGraphDBProvider.update_node_if_match` is a single-statement CAS write.

The query itself must carry the timestamp (or other field) check — a
read-then-`batch_upsert_nodes` would let two callers with the same token
both commit.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.validator = MagicMock()
    return provider


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.http_client = AsyncMock()
    return provider


class TestNeo4jUpdateNodeIfMatch:
    @pytest.mark.asyncio
    async def test_match_and_set_share_one_query(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[{"id": "k1"}])

        applied = await provider.update_node_if_match(
            "k1", "agentSkills", {"id": "k1", "content": "v2"},
            "updatedAtTimestamp", 100,
        )

        assert applied is True
        query = provider.client.execute_query.await_args.args[0]
        assert "WHERE n[$field] = $expected" in query
        assert "SET n = $node" in query
        assert "SET n += $node" not in query
        params = provider.client.execute_query.await_args.kwargs["parameters"]
        assert params["expected"] == 100
        assert params["field"] == "updatedAtTimestamp"

    @pytest.mark.asyncio
    async def test_empty_result_means_the_write_did_not_apply(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        applied = await provider.update_node_if_match(
            "k1", "agentSkills", {"id": "k1"}, "updatedAtTimestamp", 100,
        )

        assert applied is False


class TestArangoUpdateNodeIfMatch:
    @pytest.mark.asyncio
    async def test_filter_and_replace_share_one_query(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(return_value=["k1"])

        applied = await provider.update_node_if_match(
            "k1", "agentSkills", {"id": "k1", "content": "v2"},
            "updatedAtTimestamp", 100,
        )

        assert applied is True
        query, bind_vars = provider.http_client.execute_aql.await_args.args[:2]
        assert "FILTER doc._key == @key AND doc[@field] == @expected" in query
        assert "REPLACE doc WITH @node" in query
        assert bind_vars["expected"] == 100
        assert bind_vars["node"]["_key"] == "k1"

    @pytest.mark.asyncio
    async def test_empty_result_means_the_write_did_not_apply(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(return_value=[])

        applied = await provider.update_node_if_match(
            "k1", "agentSkills", {"id": "k1"}, "updatedAtTimestamp", 100,
        )

        assert applied is False
