"""`knowledgehub__list_files` holds to the turn's limits.

The toolset does not load in the agent loop today (`factory.py` skips it when the
turn has knowledge, `tool_loader.py` when it has none), but it is still
registered. Its listing checked only what the user may read: `parent_id` browsed
any node, and a root listing ran over the apps a selection merely touches. It now
applies the same checks as `knowledgegraph__navigate` / `list_files`.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.agents.actions.knowledge_graph.ops.scope import SELECTION_OUTSIDE_MESSAGE
from app.agents.actions.knowledge_hub.knowledge_hub import KnowledgeHub
from app.services.graph_db.interface.graph_db_provider import AccessCheck

CONNECTOR_OF = {"app-a": "app-a", "folder-a": "app-a", "app-b": "app-b", "folder-b": "app-b"}


@pytest.fixture(autouse=True)
def _clear_memo():
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})

    async def check_access(_user_key, _org, *, node_ids=(), scopes=(), **_):
        return AccessCheck(
            node_ids=frozenset(node_ids),
            node_ids_in_scope=frozenset(
                i for i in node_ids
                if all(s.admits({"id": i, "connectorId": CONNECTOR_OF.get(i), "groupIds": []}) for s in scopes)
            ),
        )

    graph.check_access = AsyncMock(side_effect=check_access)
    graph.get_selection_nodes = AsyncMock(side_effect=lambda org_id, *, group_ids, record_ids, exact_record_ids, limit: {
        "groups": [], "records": [{"id": r, "vrid": f"v-{r}", "connectorId": CONNECTOR_OF[r]} for r in record_ids],
    })
    graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, **_: [
        {"id": i, "recordName": i, "mimeType": "text/directory"} for i in ids if collection == "records"
    ])
    return graph


def _tool(filters: dict) -> tuple[KnowledgeHub, MagicMock]:
    graph = _graph()
    state = {
        "graph_provider": graph, "org_id": "org-1", "user_id": "user-1",
        "apps": ["app-a", "app-b"], "kb": [], "filters": filters,
    }
    return KnowledgeHub(state), graph


def _listing() -> AsyncMock:
    response = MagicMock(success=True)
    response.items = []
    response.model_dump = MagicMock(return_value={"items": []})
    return AsyncMock(return_value=response)


SERVICE = "app.agents.actions.knowledge_hub.knowledge_hub.KnowledgeHubService.get_nodes"


class TestBrowsingAParent:
    @pytest.mark.asyncio
    async def test_a_node_outside_the_selection_is_refused(self) -> None:
        tool, _ = _tool({"apps": [], "kb": [], "records": ["folder-a"], "selectionApps": ["app-a"]})
        with patch(SERVICE, new=_listing()) as get_nodes:
            success, payload = await tool.list_files(parent_id="folder-b", parent_type="folder")
        assert success is False
        assert json.loads(payload)["message"] == SELECTION_OUTSIDE_MESSAGE
        get_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_node_of_an_app_not_picked_is_refused(self) -> None:
        tool, _ = _tool({"apps": ["app-a"], "kb": []})
        with patch(SERVICE, new=_listing()) as get_nodes:
            success, payload = await tool.list_files(parent_id="folder-b", parent_type="folder")
        assert success is False
        assert json.loads(payload)["message"] == SELECTION_OUTSIDE_MESSAGE
        get_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_node_inside_is_listed(self) -> None:
        tool, _ = _tool({"apps": [], "kb": [], "records": ["folder-a"], "selectionApps": ["app-a"]})
        with patch(SERVICE, new=_listing()) as get_nodes:
            success, _ = await tool.list_files(parent_id="folder-a", parent_type="folder")
        assert success is True
        get_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_turn_limited_by_nothing_is_not_checked(self) -> None:
        tool, graph = _tool({"apps": [], "kb": []})
        with patch(SERVICE, new=_listing()) as get_nodes:
            success, _ = await tool.list_files(parent_id="folder-b", parent_type="folder")
        assert success is True
        get_nodes.assert_awaited_once()
        graph.check_access.assert_not_awaited()


class TestTheRootListing:
    @pytest.mark.asyncio
    async def test_under_a_selection_it_names_the_selection_not_the_apps_it_touches(self) -> None:
        tool, _ = _tool({"apps": [], "kb": [], "records": ["folder-a"], "selectionApps": ["app-a"]})
        with patch(SERVICE, new=_listing()) as get_nodes:
            success, payload = await tool.list_files()
        assert success is True
        message = json.loads(payload)["message"]
        assert message.startswith("This conversation is limited to these items:") and "folder-a" in message
        get_nodes.assert_not_awaited()
