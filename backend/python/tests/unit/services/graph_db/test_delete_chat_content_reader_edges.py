from unittest.mock import AsyncMock

import pytest

from tests.unit.services.graph_db.test_kb_team_edge_role import (  # noqa: F401
    arango_provider,
    neo4j_provider,
)


class TestDeleteChatContentReaderEdges:
    @pytest.mark.asyncio
    async def test_neo4j_loops_until_zero_and_sums(self, neo4j_provider) -> None:  # noqa: F811
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"deleted": 2}], [{"deleted": 2}], [{"deleted": 1}], [{"deleted": 0}]]
        )

        assert await neo4j_provider.delete_chat_content_reader_edges(batch_size=2) == 5

        calls = neo4j_provider.client.execute_query.call_args_list
        assert len(calls) == 4
        query = calls[0].args[0]
        assert "p.type = 'USER' AND p.role = 'READER'" in query
        assert "(:User)-[p:PERMISSION]->(r:Record)" in query
        assert "WITH p LIMIT $batch_size" in query
        assert calls[0].args[1] == {"attachments_connector": "ATTACHMENTS", "batch_size": 2}

    @pytest.mark.asyncio
    async def test_arango_walks_key_pages_once_and_sums(self, arango_provider) -> None:  # noqa: F811
        pages = [
            [{"last": "k2", "removed": 2}],
            [{"last": "k4", "removed": 0}],
            [{"last": "k6", "removed": 3}],
            [{"last": None, "removed": 0}],
        ]
        seen_after: list[str] = []
        queries: list[str] = []

        async def fake_aql(query, bind_vars=None, **_) -> object:
            queries.append(query)
            seen_after.append(bind_vars["after"])
            assert bind_vars["batch_size"] == 2 and bind_vars["attachments_connector"] == "ATTACHMENTS"
            assert bind_vars["@artifacts_collection"] == "artifacts" and bind_vars["users_prefix"] == "users/"
            return pages.pop(0)

        arango_provider.http_client.execute_aql = AsyncMock(side_effect=fake_aql)

        assert await arango_provider.delete_chat_content_reader_edges(batch_size=2) == 5

        # Each page resumes after the last key seen, so no edge is scanned twice.
        assert seen_after == ["", "k2", "k4", "k6"]
        assert 'p.type == "USER" AND p.role == "READER"' in queries[0]
        assert "FILTER p._key > @after" in queries[0] and "SORT p._key" in queries[0]

    @pytest.mark.asyncio
    async def test_invalid_batch_size_rejected(self, neo4j_provider, arango_provider) -> None:  # noqa: F811
        with pytest.raises(ValueError):
            await neo4j_provider.delete_chat_content_reader_edges(batch_size=0)
        with pytest.raises(ValueError):
            await arango_provider.delete_chat_content_reader_edges(batch_size=0)
