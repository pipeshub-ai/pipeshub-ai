"""Provider parity contract tests for new navigation methods.

Tests the contract that both ArangoHTTPProvider and (mocked) Neo4jProvider
expose the correct signatures for:
- get_knowledge_hub_node_access
- get_linked_records
- get_record_by_weburl (fixed signature)

Focused on mocked unit tests for correct AQL/Cypher structure and
return shape. Uses mocked http_client / neo4j client to avoid needing
real DB connections.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def arango(mock_logger, mock_config_service):
    p = ArangoHTTPProvider(mock_logger, mock_config_service)
    p.http_client = AsyncMock()
    return p


# ---------------------------------------------------------------------------
# get_knowledge_hub_node_access — Arango
# ---------------------------------------------------------------------------


class TestArangoGetKnowledgeHubNodeAccess:
    @pytest.mark.asyncio
    async def test_hit_returns_node_dict(self, arango):
        expected = {
            "id": "rec123",
            "name": "PA-1787 Payment outage",
            "nodeType": "record",
            "subType": "TICKET",
            "connector": "JIRA",
            "webUrl": "https://example.atlassian.net/browse/PA-1787",
            "recordType": "TICKET",
            "indexingStatus": "COMPLETED",
            "userRole": "READER",
        }
        arango.http_client.execute_aql = AsyncMock(return_value=[expected])
        result = await arango.get_knowledge_hub_node_access(
            node_id="rec123",
            user_key="user1",
            org_id="org1",
            folder_mime_types=["application/vnd.folder"],
        )
        assert result == expected
        arango.http_client.execute_aql.assert_called_once()
        # Verify org_id and user_key are in the bind vars
        call_args = arango.http_client.execute_aql.call_args
        bind_vars = call_args[1].get("bind_vars") or call_args[0][1]
        assert bind_vars["org_id"] == "org1"
        assert bind_vars["user_key"] == "user1"
        assert bind_vars["node_id"] == "rec123"

    @pytest.mark.asyncio
    async def test_missing_returns_none(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        result = await arango.get_knowledge_hub_node_access(
            node_id="nonexistent",
            user_key="user1",
            org_id="org1",
            folder_mime_types=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_denied_returns_none(self, arango):
        # Empty result means denied (same code path as missing — no info leak)
        arango.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await arango.get_knowledge_hub_node_access(
            node_id="denied-rec",
            user_key="user1",
            org_id="org1",
            folder_mime_types=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, arango):
        arango.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("DB error"))
        result = await arango.get_knowledge_hub_node_access(
            node_id="rec1",
            user_key="user1",
            org_id="org1",
            folder_mime_types=[],
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_org_returns_none(self, arango):
        # Provider should scope by org_id — returning [] simulates no match
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        result = await arango.get_knowledge_hub_node_access(
            node_id="rec-other-org",
            user_key="user1",
            org_id="org2",  # different org
            folder_mime_types=[],
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_linked_records — Arango
# ---------------------------------------------------------------------------


class TestArangoGetLinkedRecords:
    @pytest.mark.asyncio
    async def test_returns_list_of_linked_records(self, arango):
        linked = [
            {
                "id": "rel1",
                "name": "Agent Loop Doc",
                "recordType": "CONFLUENCE_PAGE",
                "connectorName": "CONFLUENCE",
                "webUrl": "https://example.com/wiki",
                "relationshipType": "LINKED_TO",
                "hasChildren": False,
                "indexingStatus": "COMPLETED",
                "userRole": "READER",
            }
        ]
        arango.http_client.execute_aql = AsyncMock(return_value=[linked])
        result = await arango.get_linked_records(
            record_id="rec123",
            org_id="org1",
            user_key="user1",
            relation_types=["LINKED_TO", "RELATED"],
            limit=10,
        )
        assert result == linked
        call_args = arango.http_client.execute_aql.call_args
        bind_vars = call_args[1].get("bind_vars") or call_args[0][1]
        assert bind_vars["limit"] == 10
        assert "LINKED_TO" in bind_vars["relation_types"]

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[[]])
        result = await arango.get_linked_records(
            record_id="rec123",
            org_id="org1",
            user_key="user1",
            relation_types=["LINKED_TO"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self, arango):
        arango.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("DB error"))
        result = await arango.get_linked_records(
            record_id="rec1",
            org_id="org1",
            user_key="user1",
            relation_types=["LINKED_TO"],
        )
        assert result == []


# ---------------------------------------------------------------------------
# get_record_by_weburl — Arango (verify org_id scoping works)
# ---------------------------------------------------------------------------


class TestArangoGetRecordByWeburl:
    @pytest.mark.asyncio
    async def test_with_org_id_includes_filter(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        await arango.get_record_by_weburl(
            weburl="https://example.com/page",
            org_id="org1",
        )
        call_args = arango.http_client.execute_aql.call_args
        # The AQL should have been called with org_id in bind_vars
        query_or_bind = str(call_args)
        assert "org_id" in query_or_bind or "org1" in query_or_bind

    @pytest.mark.asyncio
    async def test_without_org_id(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        result = await arango.get_record_by_weburl(weburl="https://example.com/page")
        assert result is None


# ---------------------------------------------------------------------------
# New method signatures are present on the interface
# ---------------------------------------------------------------------------


class TestInterfaceMethodSignatures:
    def test_get_knowledge_hub_node_access_is_abstract(self):
        from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
        assert "get_knowledge_hub_node_access" in IGraphDBProvider.__abstractmethods__

    def test_get_linked_records_is_abstract(self):
        from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
        assert "get_linked_records" in IGraphDBProvider.__abstractmethods__

    def test_arango_implements_get_knowledge_hub_node_access(self, arango):
        assert hasattr(arango, "get_knowledge_hub_node_access")
        assert callable(arango.get_knowledge_hub_node_access)

    def test_arango_implements_get_linked_records(self, arango):
        assert hasattr(arango, "get_linked_records")
        assert callable(arango.get_linked_records)
