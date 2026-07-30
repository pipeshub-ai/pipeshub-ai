"""Unit tests for the navigate tool in knowledge_graph.py.

Uses the _make_state pattern from test_retrieval.py.
All graph provider and service calls are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
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


class TestNavigateDepthExpansion:
    """`depth` (1-3) inlines extra levels of children in one navigate()
    call — see `GraphNavigator._expand_depth`/`_attach_context_summaries`."""

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
    async def test_depth2_expands_children_of_children(self):
        state = self._state_on_epic()

        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        level1_resp = _make_knowledge_hub_response(items=[story_item], total=1)
        subtask_item = _make_node_item("sub1", "Stripe setup", "record", "SUBTASK")
        level2_resp = _make_knowledge_hub_response(items=[subtask_item], total=1)

        async def fake_get_nodes(self_inner, **kwargs):
            if kwargs.get("parent_id") == "epic1":
                return level1_resp
            if kwargs.get("parent_id") == "story1":
                return level2_resp
            return _make_knowledge_hub_response(items=[], total=0)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=fake_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="epic1", depth=2)

        assert success
        assert f"record_id={_short_id_for(state, 'story1')}" in text
        assert f"record_id={_short_id_for(state, 'sub1')}" in text

    @pytest.mark.asyncio
    async def test_depth_omitted_does_not_expand(self):
        """depth omitted (default 1) → identical call count to before this
        feature existed: only the top-level fetch, no expansion query."""
        state = self._state_on_epic()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1)

        call_count = {"n": 0}

        async def counting_get_nodes(self_inner, **kwargs):
            call_count["n"] += 1
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=counting_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="epic1")

        assert success
        assert call_count["n"] == 1
        assert "Status:" not in text.split("Children")[-1]  # no condensed context at depth=1

    @pytest.mark.asyncio
    async def test_depth_clamped_to_max_three(self):
        """depth=99 must not crash or fan out beyond the depth=3 cap."""
        state = self._state_on_epic()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=False)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1)
        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, _text = await tool.navigate(node_id="epic1", depth=99)
        assert success

    @pytest.mark.asyncio
    async def test_depth_ignored_on_page_greater_than_one(self):
        """Depth expansion is page=1 only — a page>1 request must not
        trigger any extra child-of-child queries."""
        state = self._state_on_epic()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1, page=2)

        call_count = {"n": 0}

        async def counting_get_nodes(self_inner, **kwargs):
            call_count["n"] += 1
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=counting_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            await tool.navigate(node_id="epic1", depth=2, page=2)

        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_depth2_adds_condensed_context_from_ticket_record(self):
        """Rows shown at depth>=2 get a one-line `* `-derived metadata
        summary pulled from the real typed `Record`."""
        state = self._state_on_epic(record_by_id=_ticket_record(id="story1"))
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=False)
        mock_resp = _make_knowledge_hub_response(items=[story_item], total=1)

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=AsyncMock(return_value=mock_resp),
        ):
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="epic1", depth=2)

        assert success
        assert "Status: IN_PROGRESS" in text
        assert "Assignee: Dana" in text

    @pytest.mark.asyncio
    async def test_depth2_expansion_reuses_same_scope(self):
        """The child-of-child fetch must carry the same connector/KB scope
        as the top-level call — permission narrowing is identical at every
        level because both go through the same `get_nodes` path."""
        state = self._state_on_epic()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        level1_resp = _make_knowledge_hub_response(items=[story_item], total=1)
        level2_resp = _make_knowledge_hub_response(items=[], total=0)

        captured_calls: list[dict] = []

        async def capturing_get_nodes(self_inner, **kwargs):
            captured_calls.append(dict(kwargs))
            if kwargs.get("parent_id") == "epic1":
                return level1_resp
            return level2_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capturing_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            await tool.navigate(node_id="epic1", depth=2)

        top_call = next(c for c in captured_calls if c.get("parent_id") == "epic1")
        expansion_call = next(c for c in captured_calls if c.get("parent_id") == "story1")
        assert expansion_call.get("connector_ids") == top_call.get("connector_ids")
        assert expansion_call.get("record_group_ids") == top_call.get("record_group_ids")

    @pytest.mark.asyncio
    async def test_depth2_uses_reduced_limit_for_children(self):
        """Level-2 expansion uses a shrunk per-parent limit (max(5, limit//2))
        so a wide hierarchy can't fan out into an unbounded number of reads."""
        state = self._state_on_epic()
        story_item = _make_node_item("story1", "Payment Integration", "record", "STORY", has_children=True)
        level1_resp = _make_knowledge_hub_response(items=[story_item], total=1)
        level2_resp = _make_knowledge_hub_response(items=[], total=0)

        captured_limits: dict[str, int] = {}

        async def capturing_get_nodes(self_inner, **kwargs):
            captured_limits[kwargs.get("parent_id")] = kwargs.get("limit")
            if kwargs.get("parent_id") == "epic1":
                return level1_resp
            return level2_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=capturing_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            await tool.navigate(node_id="epic1", depth=2, limit=20)

        assert captured_limits["epic1"] == 20
        assert captured_limits["story1"] == 10  # max(5, 20 // 2)

    @pytest.mark.asyncio
    async def test_depth2_skips_expansion_for_leaf_rows(self):
        """A row with `has_children=False` costs no extra query at
        depth>=2 — only rows that actually have children are expanded."""
        state = self._state_on_epic()
        leaf_item = _make_node_item("leaf1", "Standalone task", "record", "TASK", has_children=False)
        mock_resp = _make_knowledge_hub_response(items=[leaf_item], total=1)

        call_count = {"n": 0}

        async def counting_get_nodes(self_inner, **kwargs):
            call_count["n"] += 1
            return mock_resp

        with patch(
            "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
            new=counting_get_nodes,
        ):
            tool = KnowledgeGraph(state=state)
            await tool.navigate(node_id="epic1", depth=3)

        assert call_count["n"] == 1  # only the top-level fetch


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
