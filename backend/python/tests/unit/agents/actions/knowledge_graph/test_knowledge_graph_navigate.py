"""Unit tests for the navigate tool in knowledge_graph.py.

Uses the _make_state pattern from test_retrieval.py.
All graph provider and service calls are mocked.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _navigate_args_summary,
    _time_range_error_message,
    _time_range_to_kh_filters,
)
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import RecordType, Status, TicketRecord


def _short_id_for(state: dict, full_id: str) -> str:
    """The short "R<n>" label `RecordIdShortener` assigned `full_id` in a
    prior navigate()/lookup_record() call on this `state` — TEMPORARY
    token-savings experiment (see `RecordIdShortener` in
    `utils/chat_helpers.py`). Idempotent — returns the existing mapping."""
    shortener = state.get("record_id_shortener")
    assert shortener is not None, "record_id_shortener not set on state — call a KG tool first"
    return shortener.get_or_create_short_id(full_id)


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
        # Opt into the RecordIdShortener (disabled by default — see
        # `ChatQuery.enableRecordIdShortening`) so this suite continues to
        # exercise the shortened-id path it was written against.
        "enable_record_id_shortening": True,
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
                    has_children: bool = False, created_at: int = 0, updated_at: int = 0):
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
    # Explicit ints (not left as auto-created MagicMocks) — `_node_item_to_row`
    # does epoch-ms arithmetic on these via `getattr(item, "createdAt", None)`,
    # which would break on an unset MagicMock attribute.
    item.createdAt = created_at
    item.updatedAt = updated_at
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
        assert f"Record ID: {_short_id_for(state, 'rec1')}" in text
        assert "PA-1787" in text
        assert "Jira" in text or "Payments" in text  # breadcrumbs
        assert f"record_id={_short_id_for(state, 'child1')}" in text
        assert "Related:" in text
        assert f"record_id={_short_id_for(state, 'rel1')}" in text


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
        assert "External ID: PA-1787" in text

    @pytest.mark.asyncio
    async def test_read_step_is_offered_for_a_record_node(self):
        state = self._state_on_ticket(_ticket_record())

        _success, text = await self._navigate(state)

        # The navigated record AND the children it just listed: a listing hands
        # back ids and metadata and nothing the children say, so a hint naming
        # only `rec1` left the model walking the tree without ever reading it.
        rec_id = _short_id_for(state, "rec1")
        child_id = _short_id_for(state, "child1")
        assert f'knowledgegraph__fetch_record(record_ids=["{rec_id}", "{child_id}"])' in text

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

        rg_id = _short_id_for(state, "rg1")
        child_id = _short_id_for(state, "child1")
        assert f"Node ID: {rg_id}" in text
        # Its record children are readable and are offered; the recordGroup's
        # own id must never reach a fetch call.
        assert f'knowledgegraph__fetch_record(record_ids=["{child_id}"])' in text
        assert rg_id not in text.split("Next:")[-1]

    @pytest.mark.asyncio
    async def test_metadata_load_failure_does_not_break_navigation(self):
        state = self._state_on_ticket(None)
        state["graph_provider"].get_record_by_id = AsyncMock(side_effect=RuntimeError("arango down"))

        success, text = await self._navigate(state)

        assert success
        assert f"Record ID: {_short_id_for(state, 'rec1')}" in text
        assert f"record_id={_short_id_for(state, 'child1')}" in text

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


class TestNavigateDepthSingleQuery:
    """`depth` (1-3) on a record/folder parent is passed straight through to
    a single `get_nodes(flattened=True, depth=...)` call — no per-level
    fan-out. Rows below the immediate parent get their `level` backfilled
    via a separate `get_node_depths_batch()` call — see
    `GraphNavigator.navigate`."""

    def _state_on_epic(self, *, record_by_id=None):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "epic1", "name": "SaaS Launch", "nodeType": "record", "subType": "EPIC",
            "connector": "JIRA", "webUrl": None, "indexingStatus": "COMPLETED",
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        gp.get_linked_records = AsyncMock(return_value=[])
        gp.get_record_by_id = AsyncMock(return_value=record_by_id)
        return state

    @pytest.mark.asyncio
    async def test_navigate_depth2_uses_single_query(self):
        """depth>1 on a record/folder parent calls `get_nodes()` (with
        `depth` passed through) and `get_node_depths_batch()` to backfill
        each row's level — rows from `get_nodes()` show up in the rendered
        output with their level markers."""
        state = self._state_on_epic()
        gp = state["graph_provider"]
        gp.get_node_depths_batch = AsyncMock(return_value={"story1": 1, "sub1": 2})

        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        sub_item = _make_node_item("sub1", "Stripe setup", "record", "SUBTASK")
        mock_resp = _make_knowledge_hub_response(items=[story_item, sub_item], total=2)
        mock_resp.typed_records = None

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="epic1", depth=2)

        assert success
        gp.get_node_depths_batch.assert_called_once()
        assert "Level 2" in text
        assert f"record_id={_short_id_for(state, 'story1')}" in text
        assert f"record_id={_short_id_for(state, 'sub1')}" in text

    @pytest.mark.asyncio
    async def test_navigate_depth2_app_parent_clamps_to_depth1(self):
        """An app parent with depth>1 never reaches the level-backfill
        step — its rows are recordGroups (is_record=False), so
        `get_node_depths_batch()` is not called."""
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "app-1", "name": "Jira", "nodeType": "app",
            "connector": "JIRA", "webUrl": None, "indexingStatus": None,
            "subType": "JIRA",
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        gp.get_node_depths_batch = AsyncMock()

        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        mock_resp.typed_records = None
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(node_id="app-1", depth=2)

        assert success
        gp.get_node_depths_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_depth_omitted_does_not_call_depths_batch(self):
        """depth omitted (default 1) never calls get_node_depths_batch —
        only the plain get_nodes browse path."""
        state = self._state_on_epic()
        gp = state["graph_provider"]
        gp.get_node_depths_batch = AsyncMock()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1)
        mock_resp.typed_records = None

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(node_id="epic1")

        assert success
        gp.get_node_depths_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_depth_clamped_to_max_three(self):
        """depth=99 must not crash — it clamps to the depth=3 cap and that
        clamped value is what reaches get_nodes()."""
        state = self._state_on_epic()
        gp = state["graph_provider"]
        gp.get_node_depths_batch = AsyncMock(return_value={})

        captured = {}

        async def capture_get_nodes(self_inner, **kwargs):
            captured.update(kwargs)
            mock_resp = _make_knowledge_hub_response(items=[], total=0)
            mock_resp.typed_records = None
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capture_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(node_id="epic1", depth=99)

        assert success
        assert captured.get("depth") == 3

    @pytest.mark.asyncio
    async def test_depth_ignored_on_page_greater_than_one(self):
        """depth is still passed through to get_nodes() on page>1 (the
        level-backfill call has no page guard) — the tool call simply
        succeeds and returns the requested page."""
        state = self._state_on_epic()
        gp = state["graph_provider"]
        gp.get_node_depths_batch = AsyncMock(return_value={"story1": 2})
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1, page=2)
        mock_resp.typed_records = None

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(node_id="epic1", depth=2, page=2)

        assert success

    @pytest.mark.asyncio
    async def test_depth2_reuses_same_scope_and_time_range(self):
        """The get_nodes() call at depth>1 must carry the same parent id
        and time-range filters as an equivalent depth=1 call would."""
        state = self._state_on_epic()
        gp = state["graph_provider"]
        gp.get_node_depths_batch = AsyncMock(return_value={})

        captured = {}

        async def capture_get_nodes(self_inner, **kwargs):
            captured.update(kwargs)
            mock_resp = _make_knowledge_hub_response(items=[], total=0)
            mock_resp.typed_records = None
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capture_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(
                node_id="epic1", depth=2, created_after="2026-07-01"
            )

        assert success
        assert captured.get("parent_id") == "epic1"
        assert captured.get("created_at") is not None
        assert captured["created_at"]["gte"] is not None


class TestDepth2ContextEnrichment:
    """depth>1 rows get enriched with `Record.to_llm_context()` metadata via
    typed Records returned by `get_nodes(include_typed_records=True)` — see
    `GraphNavigator._enrich_rows_from_typed_records`."""

    def _state_on_folder(self):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value={
            "id": "folder1", "name": "Project Docs", "nodeType": "folder",
            "connector": "GOOGLE_DRIVE", "webUrl": None,
            "indexingStatus": None, "subType": None,
        })
        gp.get_knowledge_hub_breadcrumbs = AsyncMock(return_value=[])
        gp.get_linked_records = AsyncMock(return_value=[])
        return state

    @pytest.mark.asyncio
    async def test_context_summary_enriched_from_typed_records(self):
        state = self._state_on_folder()
        gp = state["graph_provider"]

        mock_record = MagicMock()
        mock_record.to_llm_context.return_value = (
            "Record ID: t1\nName: Fix login\n"
            "* Status: In Progress\n* Priority: High\n* Assignee: Dana"
        )

        item = _make_node_item("t1", "Fix login", "record", "TICKET")
        mock_resp = _make_knowledge_hub_response(items=[item], total=1)
        mock_resp.typed_records = {"t1": mock_record}

        gp.get_node_depths_batch = AsyncMock(return_value={"t1": 1})
        gp.get_record_by_id = AsyncMock(return_value=None)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="folder1", depth=2)

        assert success
        assert "Status: In Progress" in text
        assert "Priority: High" in text
        assert "Assignee: Dana" in text


class TestNavigateNameFilter:
    @pytest.mark.asyncio
    async def test_navigate_without_name_filter(self):
        """navigate() works without name_filter (removed from public API)."""
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
            success, _ = await tool.navigate()
        assert success


class TestNavigateRecordIdShorteningFlag:
    """`enable_record_id_shortening` is opt-in and disabled by default (see
    `ChatQuery.enableRecordIdShortening`) — these pin the flag-off default
    behavior against `TestNavigateExposesRecordMetadata`'s flag-on cases."""

    def _state_on_ticket(self, *, enable_record_id_shortening: bool | None):
        overrides = (
            {} if enable_record_id_shortening is None
            else {"enable_record_id_shortening": enable_record_id_shortening}
        )
        state = _make_state(**overrides)
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
        gp.get_record_by_id = AsyncMock(return_value=_ticket_record())
        return state

    async def _navigate(self, state, node_id="rec1"):
        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            return await KnowledgeGraph(state=state).navigate(node_id=node_id)

    @pytest.mark.asyncio
    async def test_flag_off_shows_full_record_id_verbatim(self):
        """`enable_record_id_shortening=False` (the production default) — the
        full id must appear as-is and no shortener should have been minted."""
        state = self._state_on_ticket(enable_record_id_shortening=False)

        success, text = await self._navigate(state)

        assert success
        assert "Record ID: rec1" in text
        assert state.get("record_id_shortener") is None

    @pytest.mark.asyncio
    async def test_flag_on_shows_short_label_not_full_id(self):
        state = self._state_on_ticket(enable_record_id_shortening=True)

        success, text = await self._navigate(state)

        assert success
        short_id = _short_id_for(state, "rec1")
        assert short_id != "rec1"
        assert f"Record ID: {short_id}" in text
        assert "Record ID: rec1" not in text

    @pytest.mark.asyncio
    async def test_flag_off_node_id_passed_through_unresolved(self):
        """With the flag off, a `node_id` the model passes in is never run
        through `.resolve()` — there's no shortener to resolve against, and
        the raw id must reach the graph provider unchanged."""
        state = self._state_on_ticket(enable_record_id_shortening=False)
        gp = state["graph_provider"]

        await self._navigate(state, node_id="rec1")

        assert state.get("record_id_shortener") is None
        gp.get_knowledge_hub_node_access.assert_awaited()
        assert gp.get_knowledge_hub_node_access.await_args.kwargs.get("node_id") == "rec1"


class TestTimeRangeToKhFilters:
    """`_time_range_to_kh_filters` bridges `ops.time_range.parse_time_range`'s
    output shape (source_created_after_ms/... keys) to the {"gte":, "lte":}
    shape `KnowledgeHubService.get_nodes()` expects for created_at/updated_at."""

    def test_none_time_range_returns_none_none(self):
        assert _time_range_to_kh_filters(None) == (None, None)

    def test_empty_dict_returns_none_none(self):
        assert _time_range_to_kh_filters({}) == (None, None)

    def test_created_after_only(self):
        created_at, updated_at = _time_range_to_kh_filters({"source_created_after_ms": 100})
        assert created_at == {"gte": 100, "lte": None}
        assert updated_at is None

    def test_created_before_only(self):
        created_at, updated_at = _time_range_to_kh_filters({"source_created_before_ms": 200})
        assert created_at == {"gte": None, "lte": 200}
        assert updated_at is None

    def test_modified_after_and_before(self):
        created_at, updated_at = _time_range_to_kh_filters({
            "source_updated_after_ms": 300,
            "source_updated_before_ms": 400,
        })
        assert created_at is None
        assert updated_at == {"gte": 300, "lte": 400}

    def test_both_created_and_modified_present(self):
        created_at, updated_at = _time_range_to_kh_filters({
            "source_created_after_ms": 100,
            "source_updated_before_ms": 400,
        })
        assert created_at == {"gte": 100, "lte": None}
        assert updated_at == {"gte": None, "lte": 400}


class TestTimeRangeErrorMessage:
    def test_extracts_message_field(self):
        import json
        err = json.dumps({"status": "error", "message": "bad date"})
        assert _time_range_error_message(err) == "bad date"

    def test_non_json_string_passed_through_unchanged(self):
        assert _time_range_error_message("not json") == "not json"


class TestNavigateArgsSummaryTimeFilters:
    def test_created_filter_mentioned(self):
        summary = _navigate_args_summary({"created_after": "2026-07-01"})
        assert "time-filtered" in summary
        assert "created" in summary

    def test_modified_filter_mentioned(self):
        summary = _navigate_args_summary({"modified_before": "2026-07-01"})
        assert "time-filtered" in summary
        assert "modified" in summary

    def test_no_time_filters_not_mentioned(self):
        summary = _navigate_args_summary({"node_id": "rec1"})
        assert "time-filtered" not in summary


class TestNavigateTimeFilters:
    """created_after/created_before/modified_after/modified_before on
    navigate() — parsed via the same `ops.time_range.parse_time_range` as
    knowledgegraph__search, then bridged to KnowledgeHubService's
    created_at/updated_at {"gte":, "lte":} dicts."""

    def _state(self):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        return state

    async def _navigate_capturing(self, state, **kwargs):
        captured = {}
        mock_resp = _make_knowledge_hub_response(items=[], total=0)

        async def capture_get_nodes(self_inner, **inner_kwargs):
            captured.update(inner_kwargs)
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capture_get_nodes,
        ):
            result = await KnowledgeGraph(state=state).navigate(**kwargs)
        return result, captured

    @pytest.mark.asyncio
    async def test_created_after_only(self):
        (success, _text), captured = await self._navigate_capturing(
            self._state(), created_after="2026-07-01"
        )
        assert success
        assert captured["created_at"]["gte"] is not None
        assert captured["created_at"]["lte"] is None
        assert captured.get("updated_at") is None

    @pytest.mark.asyncio
    async def test_created_before_only(self):
        (success, _text), captured = await self._navigate_capturing(
            self._state(), created_before="2026-07-31"
        )
        assert success
        assert captured["created_at"]["gte"] is None
        assert captured["created_at"]["lte"] is not None

    @pytest.mark.asyncio
    async def test_created_after_and_before(self):
        (success, _text), captured = await self._navigate_capturing(
            self._state(), created_after="2026-07-01", created_before="2026-07-31"
        )
        assert success
        assert captured["created_at"]["gte"] is not None
        assert captured["created_at"]["lte"] is not None

    @pytest.mark.asyncio
    async def test_modified_after_and_before(self):
        (success, _text), captured = await self._navigate_capturing(
            self._state(), modified_after="2026-07-01", modified_before="2026-07-31"
        )
        assert success
        assert captured.get("created_at") is None
        assert captured["updated_at"]["gte"] is not None
        assert captured["updated_at"]["lte"] is not None

    @pytest.mark.asyncio
    async def test_created_and_modified_together(self):
        (success, _text), captured = await self._navigate_capturing(
            self._state(), created_after="2026-07-01", modified_after="2026-07-15"
        )
        assert success
        assert captured["created_at"]["gte"] is not None
        assert captured["updated_at"]["gte"] is not None

    @pytest.mark.asyncio
    async def test_no_time_filters_get_nodes_receives_none(self):
        (success, _text), captured = await self._navigate_capturing(self._state())
        assert success
        assert captured.get("created_at") is None
        assert captured.get("updated_at") is None

    @pytest.mark.asyncio
    async def test_invalid_iso_string_returns_error_without_calling_get_nodes(self):
        state = self._state()
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(side_effect=AssertionError("get_nodes must not be called on bad input")),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(created_after="not-a-date")
        assert not success
        assert "not a valid ISO 8601 date" in text

    @pytest.mark.asyncio
    async def test_naive_datetime_without_timezone_rejected(self):
        """A full datetime with no timezone offset is rejected outright
        (not silently assumed to be UTC) — the ambiguity is a strong signal
        the LLM guessed a timezone. Date-only strings ('2026-07-01') are
        still accepted and interpreted as UTC — see test_created_after_only."""
        state = self._state()
        tool = KnowledgeGraph(state=state)
        success, text = await tool.navigate(created_after="2026-07-01T00:00:00")
        assert not success
        assert "timezone" in text.lower()

    @pytest.mark.asyncio
    async def test_inverted_range_returns_error(self):
        state = self._state()
        tool = KnowledgeGraph(state=state)
        success, text = await tool.navigate(
            created_after="2026-07-31", created_before="2026-07-01"
        )
        assert not success
        assert "on or before" in text.lower() or "inverted" in text.lower()

    @pytest.mark.asyncio
    async def test_future_created_after_returns_error(self):
        state = self._state()
        tool = KnowledgeGraph(state=state)
        success, text = await tool.navigate(created_after="2099-01-01")
        assert not success
        assert "future" in text.lower()


class TestNavigateSparseResultHint:
    """When time filters were applied and few/no rows come back, the tool
    appends a retry nudge — but never on a plain, unfiltered empty listing,
    which already has its own "(no children)"/"(no items at root...)" text."""

    def _state(self):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        return state

    @pytest.mark.asyncio
    async def test_hint_shown_when_time_filtered_and_empty(self):
        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=self._state())
            success, text = await tool.navigate(created_after="2026-07-01")
        assert success
        assert "widen" in text.lower()
        assert "retry without time filters" in text.lower()

    @pytest.mark.asyncio
    async def test_hint_absent_without_time_filters_even_if_empty(self):
        mock_resp = _make_knowledge_hub_response(items=[], total=0)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=self._state())
            success, text = await tool.navigate()
        assert success
        assert "widen" not in text.lower()

    @pytest.mark.asyncio
    async def test_hint_absent_when_time_filtered_with_enough_rows(self):
        items = [_make_node_item(f"item{i}", f"Item {i}") for i in range(3)]
        mock_resp = _make_knowledge_hub_response(items=items, total=3)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=self._state())
            success, text = await tool.navigate(created_after="2026-07-01")
        assert success
        assert "widen" not in text.lower()


class TestNavigateTimestampRendering:
    """Rows print a compact `created: YYYY-MM-DD` when the underlying
    NodeItem carries a non-zero source creation timestamp."""

    def _state(self):
        state = _make_state()
        gp = state["graph_provider"]
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
        gp.get_knowledge_hub_node_access = AsyncMock(return_value=None)
        return state

    @pytest.mark.asyncio
    async def test_created_date_shown_on_child_row(self):
        created_ms = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
        item = _make_node_item("child1", "Some doc", created_at=created_ms)
        mock_resp = _make_knowledge_hub_response(items=[item], total=1)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=self._state())
            success, text = await tool.navigate()
        assert success
        assert "created: 2026-07-01" in text

    @pytest.mark.asyncio
    async def test_zero_timestamp_omits_created_label(self):
        item = _make_node_item("child1", "Some doc", created_at=0)
        mock_resp = _make_knowledge_hub_response(items=[item], total=1)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=self._state())
            success, text = await tool.navigate()
        assert success
        assert "created:" not in text
