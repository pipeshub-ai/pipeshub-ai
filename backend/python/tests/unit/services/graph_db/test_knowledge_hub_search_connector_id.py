"""Knowledge-hub search: the connector filter must see a knowledge-base file's
own connectorId (the knowledge base's id), so a search scoped to a knowledge
base can find its files. Both providers filter a minimal projection of each
node; these tests capture the generated query and read the connectorId that
projection hands to the filter. There is no database in a unit test, so the
checks are on the query text, like the other query-builder tests here.
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

KB_ID = "kb-hr"


def _projected_connector_id(query: str, start: str, end: str) -> str:
    block = query[query.index(start):query.index(end)]
    found = re.findall(r"connectorId:\s*(.+?),\s*\n", block)
    assert len(found) == 1, block
    return found[0].strip()


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.get_user_app_ids = AsyncMock(return_value=[KB_ID])
    provider.get_user_permission_app_ids = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.get_user_app_ids = AsyncMock(return_value=[KB_ID])
    provider.get_user_permission_app_ids = AsyncMock(return_value=[])
    return provider


async def _arango_phase1(provider: ArangoHTTPProvider) -> tuple[str, dict]:
    provider.http_client.execute_aql = AsyncMock(return_value=[{"total": 0, "paginated_refs": []}])
    await provider.get_knowledge_hub_search(
        "org1", "user1", skip=0, limit=10, sort_field="name", sort_dir="ASC",
        search_query="budget", connector_ids=[KB_ID],
    )
    call = provider.http_client.execute_aql.await_args_list[0]
    return call.args[0], call.kwargs["bind_vars"]


class TestArangoSearchKeepsKbConnectorId:
    async def test_record_projection_keeps_its_own_connector_id(self, arango_provider) -> None:
        aql, bind_vars = await _arango_phase1(arango_provider)

        assert _projected_connector_id(aql, "LET record_nodes", "LET all_nodes") == "record.connectorId"
        assert "node.connectorId IN @connector_ids" in aql
        assert bind_vars["connector_ids"] == [KB_ID]

    async def test_record_group_projection_keeps_its_own_connector_id(self, arango_provider) -> None:
        aql, _ = await _arango_phase1(arango_provider)
        assert _projected_connector_id(aql, "LET rg_nodes", "LET record_nodes") == "rg.connectorId"


class TestNeo4jSearchKeepsKbConnectorId:
    async def test_record_projection_keeps_its_own_connector_id(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"total": 1}], [{"paginated_ids": []}]],
        )
        await neo4j_provider.get_knowledge_hub_search(
            org_id="org1", user_key="user1", skip=0, limit=10, sort_field="name", sort_dir="ASC",
            search_query="budget", connector_ids=[KB_ID],
        )

        calls = neo4j_provider.client.execute_query.await_args_list
        assert len(calls) == 2, "count and paginated-ids queries both filter the projection"
        for call in calls:
            cypher = call.args[0]
            projected = _projected_connector_id(
                cypher, "// Build minimal Record nodes", "// Combine RG and record nodes",
            )
            assert projected == "record.connectorId"
            assert "node.connectorId IN $connector_ids" in cypher
            assert call.kwargs["parameters"]["connector_ids"] == [KB_ID]
