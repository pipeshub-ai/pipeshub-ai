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

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import AccessCheck


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
    p.get_authenticated_as = AsyncMock(return_value=[])
    p._get_authenticated_as_by_user_id = AsyncMock(return_value=[])
    p._resolve_acting_user_key_for_node = AsyncMock(side_effect=lambda user_key, *_: user_key)
    p._authenticated_as_apps = AsyncMock(return_value=[])
    return p


# ---------------------------------------------------------------------------
# get_knowledge_hub_node_access — Arango: the batch check decides
# ---------------------------------------------------------------------------


def _allow(*ids: str) -> AsyncMock:
    return AsyncMock(return_value=AccessCheck(node_ids=frozenset(ids)))


class TestArangoGetKnowledgeHubNodeAccess:
    @pytest.mark.asyncio
    async def test_hit_returns_node_dict(self, arango):
        node = {
            "id": "rec123",
            "name": "PA-1787 Payment outage",
            "nodeType": "record",
            "subType": "TICKET",
            "connector": "JIRA",
            "webUrl": "https://example.atlassian.net/browse/PA-1787",
            "recordType": "TICKET",
            "indexingStatus": "COMPLETED",
        }
        arango.check_access = _allow("rec123")
        arango.http_client.execute_aql = AsyncMock(return_value=[{"result": node, "kbId": None}])
        result = await arango.get_knowledge_hub_node_access(
            node_id="rec123",
            user_key="user1",
            org_id="org1",
            folder_mime_types=["application/vnd.folder"],
        )
        assert result == {**node, "userRole": None}     # a connector item carries no role
        arango.check_access.assert_awaited_once_with("user1", "org1", node_ids=["rec123"], transaction=None)
        bind_vars = arango.http_client.execute_aql.call_args.kwargs["bind_vars"]
        assert bind_vars == {"node_id": "rec123", "org_id": "org1",
                             "folder_mime_types": ["application/vnd.folder"]}

    @pytest.mark.asyncio
    async def test_a_collection_item_carries_the_collection_role(self, arango):
        arango.check_access = _allow("rec1")
        arango.http_client.execute_aql = AsyncMock(
            return_value=[{"result": {"id": "rec1", "nodeType": "record"}, "kbId": "kb1"}]
        )
        arango.get_user_kb_permission = AsyncMock(return_value="WRITER")
        result = await arango.get_knowledge_hub_node_access("rec1", "user1", "org1", [])
        assert result["userRole"] == "WRITER"
        arango.get_user_kb_permission.assert_awaited_once_with("kb1", "user1", transaction=None)

    @pytest.mark.asyncio
    async def test_denied_is_none_and_the_node_is_never_read(self, arango):
        arango.check_access = _allow()
        arango.http_client.execute_aql = AsyncMock()
        result = await arango.get_knowledge_hub_node_access("denied-rec", "user1", "org1", [])
        assert result is None
        arango.http_client.execute_aql.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_returns_none(self, arango):
        arango.check_access = _allow("gone")
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        assert await arango.get_knowledge_hub_node_access("gone", "user1", "org1", []) is None

    @pytest.mark.asyncio
    async def test_no_org_is_none_without_asking(self, arango):
        arango.check_access = _allow("rec1")
        assert await arango.get_knowledge_hub_node_access("rec1", "user1", "", []) is None
        arango.check_access.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unavailable_check_is_not_reported_as_absence(self, arango):
        arango.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
        with pytest.raises(PermissionVerificationUnavailableError):
            await arango.get_knowledge_hub_node_access("rec1", "user1", "org1", [])


# ---------------------------------------------------------------------------
# get_linked_records — Arango: neighbours first, then the batch check
# ---------------------------------------------------------------------------


def _link(record_id: str) -> dict:
    return {
        "id": record_id, "name": record_id, "recordType": "CONFLUENCE_PAGE",
        "connectorName": "CONFLUENCE", "webUrl": None, "relationshipType": "LINKED_TO",
        "hasChildren": False, "indexingStatus": "COMPLETED", "userRole": None,
    }


class TestArangoGetLinkedRecords:
    @pytest.mark.asyncio
    async def test_returns_only_the_accessible_links(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[_link("rel1"), _link("rel2")])
        arango.check_access = _allow("rel2")
        result = await arango.get_linked_records(
            record_id="rec123",
            org_id="org1",
            user_key="user1",
            relation_types=["LINKED_TO", "RELATED"],
            limit=10,
        )
        assert result == [_link("rel2")]
        bind_vars = arango.http_client.execute_aql.call_args.kwargs["bind_vars"]
        assert bind_vars == {"record_id": "rec123", "org_id": "org1", "relation_types": ["LINKED_TO", "RELATED"]}
        arango.check_access.assert_awaited_once_with("user1", "org1", node_ids=["rel1", "rel2"], transaction=None)

    @pytest.mark.asyncio
    async def test_the_limit_applies_to_what_the_user_can_see(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[_link(f"rel{i}") for i in range(4)])
        arango.check_access = _allow("rel1", "rel2", "rel3")
        result = await arango.get_linked_records("rec1", "org1", "user1", ["LINKED_TO"], limit=2)
        assert [r["id"] for r in result] == ["rel1", "rel2"]

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self, arango):
        arango.http_client.execute_aql = AsyncMock(return_value=[])
        arango.check_access = _allow()
        result = await arango.get_linked_records(
            record_id="rec123",
            org_id="org1",
            user_key="user1",
            relation_types=["LINKED_TO"],
        )
        assert result == []
        arango.check_access.assert_not_awaited()

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
