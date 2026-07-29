"""Unit tests for the navigate tool in knowledge_graph.py.

Uses the _make_state pattern from test_retrieval.py.
All graph provider and service calls are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import RecordType, Status, TicketRecord


def _make_state(**overrides):
    """Create a ChatState-like dict with sensible defaults."""
    gp = AsyncMock()
    # Provide sensible defaults so catalog doesn't try the lazy DB fallback
    gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": gp,
        "logger": MagicMock(),
        "agent_knowledge": [
            {"connectorId": "conn-jira", "type": "JIRA"},
            {"connectorId": "conn-confluence", "type": "CONFLUENCE"},
        ],
    }
    state.update(overrides)
    return state


def _make_knowledge_hub_response(items=None, page=1, total=0):
    """Build a mock KnowledgeHubNodesResponse."""
    resp = MagicMock()
    resp.success = True
    resp.items = items or []
    resp.error = None
    pag = MagicMock()
    pag.page = page
    pag.limit = 20
    pag.totalItems = total
    pag.hasNext = False
    pag.hasPrev = False
    resp.pagination = pag
    return resp


def _ticket_record(id: str = "rec1") -> TicketRecord:
    """A real `TicketRecord`, as `get_record_by_id` returns — the point of the
    enrichment is the type-specific fields, so a MagicMock would assert
    nothing."""
    return TicketRecord(
        id=id,
        record_name="SaaS launch",
        record_type=RecordType.TICKET,
        external_record_id="PA-1787",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="jira-conn",
        weburl="https://example.atlassian.net/browse/PA-1787",
        indexing_status="COMPLETED",
        status=Status.IN_PROGRESS,
        assignee="Dana",
        priority="High",
    )


def _make_node_item(id: str, name: str, node_type: str = "record", record_type: str = "TICKET",
                    has_children: bool = False):
    item = MagicMock()
    item.id = id
    item.name = name
    item.nodeType = node_type
    item.recordType = record_type
    item.recordGroupType = None
    item.connector = "JIRA"
    item.hasChildren = has_children
    item.indexingStatus = "COMPLETED"
    item.sizeInBytes = None
    item.webUrl = None
    return item


class TestNavigateNoKnowledge:
    @pytest.mark.asyncio
    async def test_no_knowledge_returns_error(self):
        """Empty agent_knowledge AND has_knowledge=False → error."""
        state = _make_state(agent_knowledge=[], has_knowledge=False)
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})
        tool = KnowledgeGraph(state=state)
        success, text = await tool.navigate()
        assert not success
        assert "No knowledge sources" in text

    @pytest.mark.asyncio
    async def test_chatbot_mode_empty_scope_uses_catalog(self):
        """Regression: navigate() must NOT error in chat mode where
        apps/kb/agent_knowledge are empty but has_knowledge=True."""
        state = {
            "org_id": "org-1",
            "user_id": "user-1",
            "has_knowledge": True,
            "available_connectors": [
                {"id": "conn-jira", "name": "Jira Cloud", "type": "JIRA"},
            ],
            "graph_provider": AsyncMock(),
        }
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        gp.get_knowledge_hub_filter_options = AsyncMock(return_value={"apps": []})

        item = _make_node_item("app1", "Jira", node_type="app")
        mock_resp = _make_knowledge_hub_response(items=[item], total=1)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate()

        assert success


class TestNavigateNoState:
    @pytest.mark.asyncio
    async def test_no_state_returns_error(self):
        tool = KnowledgeGraph(state=None)
        success, text = await tool.navigate()
        assert not success


class TestNavigateNoGraphProvider:
    @pytest.mark.asyncio
    async def test_no_provider_returns_error(self):
        state = _make_state(graph_provider=None)
        tool = KnowledgeGraph(state=state)
        success, text = await tool.navigate()
        assert not success
        assert "Graph provider" in text


class TestNavigateRoot:
    @pytest.mark.asyncio
    async def test_root_returns_apps(self):
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        graph_provider.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        item = _make_node_item("app1", "Jira", node_type="app")
        mock_resp = _make_knowledge_hub_response(items=[item], total=1)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate()

        assert success
        assert "app1" in text or "Jira" in text


class TestNavigateDeniedNode:
    @pytest.mark.asyncio
    async def test_denied_node_returns_empty(self):
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        # Node access denied → None
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="denied-node")

        # Denied and missing must produce identical-looking output
        assert success  # the tool itself succeeds, the result is empty
        assert "Record ID:" not in text


class TestNavigateMissingNode:
    @pytest.mark.asyncio
    async def test_missing_node_same_as_denied(self):
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        # Missing node → None (same code path as denied)
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="does-not-exist")

        assert success
        assert "Record ID:" not in text


class TestNavigateRecordWithChildren:
    @pytest.mark.asyncio
    async def test_record_node_shows_children_and_related(self):
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "rec1",
            "name": "PA-1787 Payment outage",
            "nodeType": "record",
            "subType": "TICKET",
            "connector": "JIRA",
            "webUrl": "https://example.atlassian.net/browse/PA-1787",
            "indexingStatus": "COMPLETED",
        })
        graph_provider.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[
            {"id": "app1", "name": "Jira", "nodeType": "app", "subType": "JIRA"},
            {"id": "rg1", "name": "Payments", "nodeType": "recordGroup", "subType": "PROJECT"},
        ])
        graph_provider.get_linked_records = AsyncMock(return_value=[
            {
                "id": "rel1",
                "name": "Agent Loop Doc",
                "recordType": "CONFLUENCE_PAGE",
                "connectorName": "CONFLUENCE",
                "webUrl": "https://example.atlassian.net/wiki/spaces/ENG/pages/123",
                "relationshipType": "LINKED_TO",
                "hasChildren": False,
                "indexingStatus": "COMPLETED",
            }
        ])

        child_item = _make_node_item("child1", "PA-1801 Fix retry", "record", "TICKET", has_children=True)
        mock_resp = _make_knowledge_hub_response(items=[child_item], total=1)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec1")

        assert success
        assert "Record ID: rec1" in text
        assert "PA-1787" in text
        assert "Jira" in text or "Payments" in text  # breadcrumbs
        assert "record_id=child1" in text
        assert "Related:" in text
        assert "record_id=rel1" in text


class TestNavigateExposesRecordMetadata:
    """`get_knowledge_hub_node_access` returns identity and indexing state
    only, so opening a ticket used to show no status, assignee or priority —
    the fields a question about that ticket usually turns on. The navigator
    loads the typed Record for the current node and renders its
    `to_llm_context()` block instead."""

    def _state_on_ticket(self, record):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "rec1",
            "name": "SaaS launch",
            "nodeType": "record",
            "subType": "TICKET",
            "connector": "JIRA",
            "webUrl": "https://example.atlassian.net/browse/PA-1787",
            "indexingStatus": "COMPLETED",
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        gp.get_linked_records = AsyncMock(return_value=[])
        gp.get_record_by_id = AsyncMock(return_value=record)
        return state

    async def _navigate(self, state, node_id="rec1", **kwargs):
        mock_resp = _make_knowledge_hub_response(
            items=[_make_node_item("child1", "PA-1801 Fix retry")], total=1,
        )
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            return await KnowledgeGraph(state=state).navigate(node_id=node_id, **kwargs)

    @pytest.mark.asyncio
    async def test_ticket_status_and_assignee_are_shown(self):
        state = self._state_on_ticket(_ticket_record())

        success, text = await self._navigate(state)

        assert success
        assert "* Status: IN_PROGRESS" in text
        assert "* Priority: High" in text
        assert "* Assignee: Dana" in text
        assert "External ID     : PA-1787" in text

    @pytest.mark.asyncio
    async def test_read_step_is_offered_for_a_record_node(self):
        state = self._state_on_ticket(_ticket_record())

        _success, text = await self._navigate(state)

        assert 'knowledgegraph__fetch_record(record_ids=["rec1"])' in text

    @pytest.mark.asyncio
    async def test_read_step_is_not_offered_for_a_non_record_node(self):
        """Regression: the fetch hint used to be printed for every node,
        including apps and recordGroups, whose ids cannot be fetched."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "rg1", "name": "Payments", "nodeType": "recordGroup",
            "subType": "PROJECT", "connector": "JIRA",
            "webUrl": None, "indexingStatus": None,
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])

        _success, text = await self._navigate(state, node_id="rg1")

        assert "Node ID: rg1" in text
        assert "knowledgegraph__fetch_record" not in text

    @pytest.mark.asyncio
    async def test_metadata_load_failure_does_not_break_navigation(self):
        state = self._state_on_ticket(None)
        state["graph_provider"].get_record_by_id = AsyncMock(side_effect=RuntimeError("arango down"))

        success, text = await self._navigate(state)

        assert success
        assert "Record ID: rec1" in text
        assert "record_id=child1" in text

    @pytest.mark.asyncio
    async def test_later_pages_skip_the_extra_record_read(self):
        state = self._state_on_ticket(_ticket_record())

        await self._navigate(state, page=2)

        state["graph_provider"].get_record_by_id.assert_not_awaited()


class TestNavigateRemembersRecordIds:
    """Reporting the IDs is what makes `knowledgegraph__fetch_record` available
    for them — see `hooks/citations.py`."""

    @pytest.mark.asyncio
    async def test_current_and_child_record_ids_are_remembered(self):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "rec1", "name": "Epic", "nodeType": "record", "subType": "TICKET",
            "connector": "JIRA", "webUrl": None, "indexingStatus": "COMPLETED",
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        gp.get_linked_records = AsyncMock(return_value=[])
        gp.get_record_by_id = AsyncMock(return_value=None)

        mock_resp = _make_knowledge_hub_response(
            items=[_make_node_item("child1", "Story A")], total=1,
        )
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            await KnowledgeGraph(state=state).navigate(node_id="rec1")

        assert state["known_record_ids"] == {"rec1", "child1"}

    @pytest.mark.asyncio
    async def test_container_node_ids_are_not_remembered(self):
        """An app or recordGroup id is not fetchable, so offering it as one
        would only produce a failed call."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])

        mock_resp = _make_knowledge_hub_response(
            items=[_make_node_item("app1", "Jira", node_type="app", record_type=None)],
            total=1,
        )
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            await KnowledgeGraph(state=state).navigate()

        assert not state.get("known_record_ids")


class TestNavigateAgentScoping:
    @pytest.mark.asyncio
    async def test_agent_scoping_applied(self):
        """connector_ids from agent_knowledge are passed to get_nodes."""
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        graph_provider.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])

        captured = {}
        mock_resp = _make_knowledge_hub_response(items=[], total=0)

        async def capture_get_nodes(self_inner, **kwargs):
            captured.update(kwargs)
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capture_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            await tool.navigate()

        # Scoping connector IDs should be passed (for root browse)
        assert "connector_ids" in captured or "record_group_ids" in captured


class TestNavigateNameFilter:
    @pytest.mark.asyncio
    async def test_name_filter_short_ignored(self):
        """name_filter shorter than 2 chars is dropped."""
        state = _make_state()
        graph_provider = state["graph_provider"]
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        graph_provider.get_knowledge_hub_node_access = AsyncMock(return_value=None)

        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, _ = await tool.navigate(name_filter="a")
        assert success  # no crash from short filter
