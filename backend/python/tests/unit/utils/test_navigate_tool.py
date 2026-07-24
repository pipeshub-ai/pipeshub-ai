"""Unit tests for ``app.utils.navigate_tool`` — the ``navigate`` tool."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.api.knowledge_hub_models import (
    BreadcrumbItem,
    CurrentNode,
    KnowledgeHubNodesResponse,
    NodeItem,
    PaginationInfo,
)
from app.utils.navigate_tool import NavigateArgs, _describe_navigate, create_navigate_tool
from app.utils.record_tool_helpers import NodeRefMapper


def _node_item(node_id: str, name: str, node_type: str = "recordGroup", **extra) -> NodeItem:
    base = dict(
        id=node_id,
        name=name,
        nodeType=node_type,
        origin="CONNECTOR",
        connector="Jira",
        createdAt=0,
        updatedAt=0,
        hasChildren=False,
    )
    base.update(extra)
    return NodeItem(**base)


def _response(**overrides) -> KnowledgeHubNodesResponse:
    base = dict(success=True, items=[])
    base.update(overrides)
    return KnowledgeHubNodesResponse(**base)


def _make_graph_provider(**overrides) -> MagicMock:
    provider = MagicMock()
    provider.get_knowledge_hub_node_info = AsyncMock(return_value=None)
    provider.get_related_records_by_relation_type = AsyncMock(return_value=[])
    provider.check_record_access_with_details = AsyncMock(return_value={"allowed": True})
    provider.get_document = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(provider, key, value)
    return provider


def _patched_service(get_nodes_return):
    """Patch KnowledgeHubService so create_navigate_tool's internal instance
    returns a canned KnowledgeHubNodesResponse (or raises, if given an exception)."""
    mock_service_cls = MagicMock()
    mock_service_instance = MagicMock()
    if isinstance(get_nodes_return, Exception):
        mock_service_instance.get_nodes = AsyncMock(side_effect=get_nodes_return)
    else:
        mock_service_instance.get_nodes = AsyncMock(return_value=get_nodes_return)
    mock_service_cls.return_value = mock_service_instance
    return mock_service_cls, mock_service_instance


class TestNavigateArgs:
    def test_defaults(self) -> None:
        args = NavigateArgs()
        assert args.node_id is None
        assert args.depth == 1
        assert args.page == 1
        assert args.limit == 20

    def test_depth_bounds(self) -> None:
        with pytest.raises(Exception):
            NavigateArgs(depth=4)

    def test_limit_bounds(self) -> None:
        with pytest.raises(Exception):
            NavigateArgs(limit=51)


class TestDescribeNavigate:
    def test_root_view(self) -> None:
        assert _describe_navigate({}) == "Browsing connected apps…"

    def test_name_filter(self) -> None:
        desc = _describe_navigate({"name_filter": "invoice"})
        assert "invoice" in desc

    def test_opening_node(self) -> None:
        desc = _describe_navigate({"node_id": "n3"})
        assert "n3" in desc

    def test_opening_node_with_page(self) -> None:
        desc = _describe_navigate({"node_id": "n3", "page": 2})
        assert "page 2" in desc


class TestNavigateToolAvailability:
    @pytest.mark.asyncio
    async def test_unavailable_without_graph_provider(self) -> None:
        tool = create_navigate_tool(graph_provider=None, org_id="org-1", user_id="user-1")
        result = await tool.ainvoke({})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_unavailable_without_user_id(self) -> None:
        provider = _make_graph_provider()
        with patch("app.utils.navigate_tool.KnowledgeHubService"):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id=None)
        result = await tool.ainvoke({})
        assert result["ok"] is False


class TestNavigateToolRootAndListing:
    @pytest.mark.asyncio
    async def test_root_view_lists_apps(self) -> None:
        provider = _make_graph_provider()
        response = _response(items=[_node_item("app-1", "Jira", node_type="app")])
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({})

        assert result["ok"] is True
        assert "Jira" in result["content"][0]["text"]
        service_instance.get_nodes.assert_awaited_once()
        _, kwargs = service_instance.get_nodes.call_args
        assert kwargs["parent_id"] is None

    @pytest.mark.asyncio
    async def test_descend_into_app_lists_record_groups(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "app-1", "name": "Jira", "nodeType": "app", "subType": "JIRA"})
        )
        response = _response(items=[_node_item("rg-1", "Payments Project")])
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "app-1"})

        assert result["ok"] is True
        _, kwargs = service_instance.get_nodes.call_args
        assert kwargs["parent_type"] == "app"
        assert kwargs["parent_id"] == "app-1"

    @pytest.mark.asyncio
    async def test_unknown_node_returns_error(self) -> None:
        provider = _make_graph_provider(get_knowledge_hub_node_info=AsyncMock(return_value=None))
        service_cls, _ = _patched_service(_response())
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "does-not-exist"})

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_name_filter_is_passed_through_as_q(self) -> None:
        provider = _make_graph_provider()
        response = _response(items=[])
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            await tool.ainvoke({"name_filter": "invoice"})

        _, kwargs = service_instance.get_nodes.call_args
        assert kwargs["q"] == "invoice"

    @pytest.mark.asyncio
    async def test_get_nodes_failure_response_returns_error(self) -> None:
        provider = _make_graph_provider()
        response = _response(success=False, error="boom")
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({})

        assert result == {"ok": False, "error": "boom"}

    @pytest.mark.asyncio
    async def test_get_nodes_exception_returns_error(self) -> None:
        provider = _make_graph_provider()
        service_cls, _ = _patched_service(RuntimeError("db down"))
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({})

        assert result["ok"] is False


class TestNavigateToolRecordAndFolderPermissionGate:
    @pytest.mark.asyncio
    async def test_record_node_permission_denied_returns_generic_error(self) -> None:
        """Knowledge Hub children queries are permission-filtered, but the header
        for the *current* node must be gated explicitly."""
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "r1", "name": "Secret Ticket", "nodeType": "record", "subType": "TICKET"}),
            check_record_access_with_details=AsyncMock(return_value=None),
        )
        service_cls, _ = _patched_service(_response())
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "r1"})

        assert result["ok"] is False
        assert "Secret Ticket" not in str(result)

    @pytest.mark.asyncio
    async def test_folder_node_also_permission_gated(self) -> None:
        """Folders are records with a folder mimeType — same permission-gate
        regression as record nodes."""
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "f1", "name": "Secret Folder", "nodeType": "folder", "subType": "FILE"}),
            check_record_access_with_details=AsyncMock(return_value=None),
        )
        service_cls, _ = _patched_service(_response())
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "f1"})

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_accessible_record_shows_header_with_metadata(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "r1", "name": "Payment outage", "nodeType": "record", "subType": "TICKET"}),
            get_document=AsyncMock(return_value={
                "id": "r1", "webUrl": "https://x/PA-1", "indexingStatus": "COMPLETED", "connectorName": "Jira",
            }),
        )
        response = _response(
            currentNode=CurrentNode(id="r1", name="Payment outage", nodeType="record", subType="TICKET"),
            items=[],
        )
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "r1"})

        assert result["ok"] is True
        text = result["content"][0]["text"]
        assert "Payment outage" in text
        assert "https://x/PA-1" in text

    @pytest.mark.asyncio
    async def test_related_records_fetched_only_for_record_type_page_one(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "r1", "name": "Ticket", "nodeType": "record", "subType": "TICKET"}),
            get_document=AsyncMock(return_value={"id": "r1"}),
            get_related_records_by_relation_type=AsyncMock(return_value=[{"id": "r9"}]),
        )
        provider.check_record_access_with_details = AsyncMock(return_value={"allowed": True})

        async def _get_document_side_effect(record_id, _collection):
            if record_id == "r1":
                return {"id": "r1"}
            return {"id": "r9", "recordName": "Linked Confluence Page", "recordType": "CONFLUENCE_PAGE", "connectorName": "Confluence"}
        provider.get_document = AsyncMock(side_effect=_get_document_side_effect)

        response = _response(currentNode=CurrentNode(id="r1", name="Ticket", nodeType="record", subType="TICKET"), items=[])
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "r1"})

        assert result["ok"] is True
        assert "Linked Confluence Page" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_related_records_not_fetched_on_page_two(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "r1", "name": "Ticket", "nodeType": "record", "subType": "TICKET"}),
            get_document=AsyncMock(return_value={"id": "r1"}),
        )
        response = _response(items=[])
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            await tool.ainvoke({"node_id": "r1", "page": 2})

        provider.get_related_records_by_relation_type.assert_not_awaited()


class TestNavigateToolPaginationAndRefs:
    @pytest.mark.asyncio
    async def test_page_one_requests_breadcrumbs(self) -> None:
        provider = _make_graph_provider()
        response = _response(items=[])
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            await tool.ainvoke({"page": 1})

        _, kwargs = service_instance.get_nodes.call_args
        assert kwargs["include"] == ["breadcrumbs"]

    @pytest.mark.asyncio
    async def test_page_two_skips_breadcrumbs_request(self) -> None:
        provider = _make_graph_provider()
        response = _response(items=[], pagination=PaginationInfo(page=2, limit=20, totalItems=40, totalPages=2, hasNext=False, hasPrev=True))
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"page": 2})

        _, kwargs = service_instance.get_nodes.call_args
        assert kwargs["include"] == []
        assert "Path:" not in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_node_ref_is_resolved_to_underlying_id(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "real-uuid", "name": "Jira", "nodeType": "app", "subType": "JIRA"})
        )
        mapper = NodeRefMapper()
        mapper.get_or_create_ref("real-uuid")  # mints "n1"
        response = _response(items=[])
        service_cls, service_instance = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1", node_ref_mapper=mapper)
            await tool.ainvoke({"node_id": "n1"})

        provider.get_knowledge_hub_node_info.assert_awaited()
        call_args = provider.get_knowledge_hub_node_info.call_args
        assert call_args[0][0] == "real-uuid"

    @pytest.mark.asyncio
    async def test_navigation_payload_included_on_success(self) -> None:
        provider = _make_graph_provider()
        response = _response(
            items=[_node_item("app-1", "Jira", node_type="app")],
            breadcrumbs=[BreadcrumbItem(id="app-1", name="Jira", nodeType="app", subType="JIRA")],
        )
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({})

        assert "navigation" in result
        assert result["navigation"]["page"] == 1
        assert result["summary"] == "1 of 1 items"


class TestNavigateToolTreeMode:
    @pytest.mark.asyncio
    async def test_depth_greater_than_one_uses_tree_mode(self) -> None:
        provider = _make_graph_provider()
        response = _response(items=[_node_item("app-1", "Jira", node_type="app", hasChildren=False)])
        service_cls, _ = _patched_service(response)
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"depth": 2})

        assert result["ok"] is True
        assert result["navigation"]["mode"] == "tree"
        assert "Tree view" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_tree_mode_root_permission_gate_for_record(self) -> None:
        provider = _make_graph_provider(
            get_knowledge_hub_node_info=AsyncMock(return_value={"id": "r1", "name": "Secret", "nodeType": "record", "subType": "TICKET"}),
            check_record_access_with_details=AsyncMock(return_value=None),
        )
        service_cls, _ = _patched_service(_response())
        with patch("app.utils.navigate_tool.KnowledgeHubService", service_cls):
            tool = create_navigate_tool(graph_provider=provider, org_id="org-1", user_id="user-1")
            result = await tool.ainvoke({"node_id": "r1", "depth": 2})

        assert result["ok"] is False
