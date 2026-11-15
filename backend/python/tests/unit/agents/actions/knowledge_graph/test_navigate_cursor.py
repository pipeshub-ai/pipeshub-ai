"""Navigate and list_files page by cursor, and say when a total stopped at a cap.

Page numbers stop at 50 (the service walks forward to a page by number); a
cursor resumes where the last page ended. A cursor is bound to the user, the
organisation and the place it was issued for, and the sources it was issued
over, so it can never list more than a first page there would.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.actions.knowledge_graph.models import (
    NavigationView,
    NodeRef,
    NodeRow,
    PaginationInfo,
)
from app.agents.actions.knowledge_graph.navigator import (
    GraphNavigator,
    ListingFailedError,
)
from app.agents.actions.knowledge_graph.ops.listing import execute_list_files
from app.agents.actions.knowledge_graph.ops.scope import KnowledgeScope
from app.agents.actions.knowledge_graph.views import render_navigation_view
from app.connectors.api.router import knowledge_graph_navigate
from tests.support.openapi_spec import openapi_spec


def _config() -> AsyncMock:
    config = AsyncMock()
    config.get_config.return_value = {"scopedJwtSecret": "test-secret"}
    return config


def _row(row_id: str, name: str, node_type: str = "record") -> dict:
    return {
        "id": row_id, "name": name, "nodeType": node_type, "sortKey": name.lower(), "nullRank": 0,
        "parentId": None, "parentType": None, "parentName": "Parent", "origin": "CONNECTOR",
        "createdAt": 1, "updatedAt": 2, "hasChildren": False,
    }


def _scope(node_id: str) -> dict:
    return {
        "admitted": True, "nodeId": node_id,
        "currentNode": {"id": node_id, "name": "Space", "nodeType": "recordGroup"},
        "parentNode": {"id": "app1", "name": "App One", "nodeType": "app"},
        "breadcrumbs": [{"id": "app1", "name": "App One", "nodeType": "app"}],
    }


def _graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_user_by_user_id.return_value = {"_key": "uk1", "email": "u@example.com"}
    graph.get_knowledge_hub_access_context_v2.return_value = {"grantee_ids": ["uk1"], "gated_app_ids": ["app1", "app2"]}
    graph.get_hidden_app_ids.return_value = []
    graph.get_typed_records_batch.return_value = {}
    graph.get_linked_records.return_value = []
    graph.get_record_by_id.return_value = None
    graph.get_knowledge_hub_node_access.side_effect = lambda node_id, **_: {"nodeType": "recordGroup", "name": node_id}
    graph.get_knowledge_hub_connector_page_v3.side_effect = lambda **kw: {
        "rows": [_row("r1", "Report")], "hasMore": True, "total": 120, "counts": None,
        "scope": _scope(kw.get("record_group_id") or kw.get("parent_id") or "space-1"),
    }
    graph.get_knowledge_hub_root_nodes_v2.return_value = {
        "partitions": [{
            "partitionId": "p1", "partitionKind": "BROWSE", "appId": None, "rows": [_row("g1", "Group", "recordGroup")],
            "hasMore": True, "exhausted": False, "total": 70, "ids": [], "countsByType": None,
        }],
        "scope": None,
    }
    return graph


def _navigator(graph: AsyncMock, user_id: str = "u1") -> GraphNavigator:
    return GraphNavigator(graph_provider=graph, user_id=user_id, user_key="uk1", org_id="o1", config_service=_config())


class TestTheNavigatorPagesByCursor:
    @pytest.mark.asyncio
    async def test_a_page_hands_out_a_cursor_and_the_next_page_resumes_after_its_last_row(self) -> None:
        graph = _graph()
        first = await _navigator(graph).navigate(node_id="space-1", limit=50)
        assert first.pagination.next_cursor

        graph.get_linked_records.reset_mock()
        # Page 51 is past what a page number reaches; a cursor goes on.
        second = await _navigator(graph).navigate(node_id="space-1", page=51, limit=50, cursor=first.pagination.next_cursor)

        kwargs = graph.get_knowledge_hub_connector_page_v3.call_args.kwargs
        assert kwargs["after"] == {"nullRank": 0, "sortKey": "report", "id": "r1"}
        assert second.pagination.has_prev is True
        # A cursor page is never the first: no breadcrumbs or related records again.
        assert second.breadcrumbs == []

    @pytest.mark.asyncio
    async def test_a_cursor_issued_to_another_user_is_refused(self) -> None:
        graph = _graph()
        issued = (await _navigator(graph, "u1").navigate(node_id="space-1", limit=50)).pagination.next_cursor
        calls = graph.get_knowledge_hub_connector_page_v3.await_count

        with pytest.raises(ListingFailedError) as refused:
            await _navigator(graph, "u2").navigate(node_id="space-1", limit=50, cursor=issued)

        assert refused.value.status_code == 400
        assert graph.get_knowledge_hub_connector_page_v3.await_count == calls

    @pytest.mark.asyncio
    async def test_a_cursor_from_another_node_or_garbage_is_refused(self) -> None:
        graph = _graph()
        issued = (await _navigator(graph).navigate(node_id="space-1", limit=50)).pagination.next_cursor

        for cursor in (issued, "not-a-cursor"):
            with pytest.raises(ListingFailedError) as refused:
                await _navigator(graph).navigate(node_id="space-2", limit=50, cursor=cursor)
            assert refused.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_cursor_issued_over_wider_sources_does_not_resume_over_narrower_ones(self) -> None:
        graph = _graph()
        issued = (await _navigator(graph).navigate(limit=50, connector_ids=["app1", "app2"])).pagination.next_cursor

        with pytest.raises(ListingFailedError) as refused:
            await _navigator(graph).navigate(limit=50, connector_ids=["app1"], cursor=issued)
        assert refused.value.status_code == 400

        # The control: the same sources resume.
        resumed = await _navigator(graph).navigate(limit=50, connector_ids=["app2", "app1"], cursor=issued)
        assert [row.id for row in resumed.rows] == ["g1"]

    @pytest.mark.asyncio
    async def test_a_scoped_browse_resumes_inside_its_sources(self) -> None:
        graph = _graph()
        issued = (await _navigator(graph).navigate(node_id="space-1", limit=50, connector_ids=["app1"])).pagination.next_cursor

        resumed = await _navigator(graph).navigate(node_id="space-1", limit=50, connector_ids=["app1"], cursor=issued)

        assert [row.id for row in resumed.rows] == ["r1"]

    @pytest.mark.asyncio
    async def test_without_a_signing_key_no_cursor_is_handed_out_or_taken(self) -> None:
        graph = _graph()
        unsigned = GraphNavigator(graph_provider=graph, user_id="u1", user_key="uk1", org_id="o1")
        assert (await unsigned.navigate(node_id="space-1", limit=50)).pagination.next_cursor is None
        issued = (await _navigator(graph).navigate(node_id="space-1", limit=50)).pagination.next_cursor
        with pytest.raises(ListingFailedError):
            await unsigned.navigate(node_id="space-1", limit=50, cursor=issued)


def _view(current: NodeRef | None, *, capped: bool, cursor: str | None) -> NavigationView:
    return NavigationView(
        current=current, breadcrumbs=[], related=[], web_url=None, indexing_status=None, connector=None,
        rows=[NodeRow(id="r1", name="Report", node_type="record", sub_type=None, is_record=True, has_children=False, detail=None)],
        pagination=PaginationInfo(page=1, limit=50, total=120, total_capped=capped, has_next=True, has_prev=False, next_cursor=cursor),
    )


class TestTheRenderedView:
    def test_a_capped_total_reads_as_a_lower_bound_and_more_is_by_cursor(self) -> None:
        space = NodeRef(id="space-1", name="Space", node_type="recordGroup", sub_type=None, is_record=False)
        text = render_navigation_view(_view(space, capped=True, cursor="tok-2"), 1)
        assert "Children 1-1 of at least 120:" in text
        assert 'navigate(node_id="space-1", cursor="tok-2") for more' in text

    def test_the_root_names_its_next_page_too(self) -> None:
        assert 'navigate(cursor="tok-2") for more' in render_navigation_view(_view(None, capped=False, cursor="tok-2"), 1)
        assert "navigate(page=2) for more" in render_navigation_view(_view(None, capped=False, cursor=None), 1)

    def test_an_exact_total_reads_as_exact(self) -> None:
        assert "of 120:" in render_navigation_view(_view(None, capped=False, cursor=None), 1)


class TestACursorPageHasAPositionNotANumber:
    @pytest.mark.asyncio
    async def test_after_a_short_page_the_range_is_the_listings_own_position(self) -> None:
        """A page reached by cursor has no number; one worked out from its position is wrong after a short page (the range must come from the listing's start index)."""
        graph = _graph()
        first = await _navigator(graph).navigate(node_id="space-1", limit=50)
        graph.get_knowledge_hub_connector_page_v3.side_effect = lambda **kw: {
            "rows": [_row("r2", "Second")], "hasMore": True, "total": 120, "counts": None, "scope": _scope("space-1")}
        second = await _navigator(graph).navigate(node_id="space-1", limit=50, cursor=first.pagination.next_cursor)

        assert second.pagination.page is None
        assert second.pagination.start_index == 2              # one row came before it, not a whole page of 50
        text = render_navigation_view(second, 2)
        assert "Children 2-2 of 120:" in text and "Children 51" not in text

    def test_a_cursor_page_with_no_position_lists_its_rows_without_a_range(self) -> None:
        view = _view(None, capped=False, cursor="tok-3")
        view.pagination = view.pagination.model_copy(update={"page": None, "start_index": None})
        text = render_navigation_view(view, 2)
        assert "\nChildren:" in text and 'navigate(cursor="tok-3") for more' in text


class TestTheGoBackHint:
    def test_a_numbered_page_with_a_next_cursor_still_names_the_page_before_it(self) -> None:
        space = NodeRef(id="space-1", name="Space", node_type="recordGroup", sub_type=None, is_record=False)
        view = _view(space, capped=False, cursor="tok-4")
        view.pagination = view.pagination.model_copy(update={"page": 3, "has_prev": True})
        text = render_navigation_view(view, 3)
        assert 'navigate(node_id="space-1", page=2) to go back' in text
        assert 'navigate(node_id="space-1", cursor="tok-4") for more' in text

    def test_a_cursor_page_names_no_page_before_it(self) -> None:
        space = NodeRef(id="space-1", name="Space", node_type="recordGroup", sub_type=None, is_record=False)
        view = _view(space, capped=False, cursor="tok-4")
        view.pagination = view.pagination.model_copy(update={"page": None, "start_index": 101, "has_prev": True})
        assert "to go back" not in render_navigation_view(view, 2)


def _request(user_id: str = "u1") -> MagicMock:
    request = MagicMock()
    user = {"userId": user_id, "orgId": "o1"}
    request.state.user = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.app.container.config_service = MagicMock(return_value=_config())
    return request


async def _route(graph: AsyncMock, **params: object) -> dict:
    catalog = MagicMock(connector_ids=MagicMock(return_value=["app1", "app2"]), connectors=[])
    kwargs: dict[str, Any] = {
        "node_id": None, "page": 1, "limit": 50, "depth": 1, "node_types": None, "created_after": None,
        "created_before": None, "modified_after": None, "modified_before": None, "cursor": None,
    }
    kwargs.update(params)
    with patch("app.connectors.api.router.ConnectorCatalog.build", AsyncMock(return_value=catalog)), \
         patch("app.connectors.api.router.refuse_outside_connection", AsyncMock()):
        return await knowledge_graph_navigate(request=_request(), graph_provider=graph, **kwargs)


class TestTheNavigateEndpoint:
    @pytest.mark.asyncio
    async def test_the_next_page_is_reached_by_the_cursor_it_returned(self) -> None:
        graph = _graph()
        first = await _route(graph, node_id="space-1")
        assert first["pagination"]["next_cursor"]
        assert first["pagination"]["total_capped"] is False

        second = await _route(graph, node_id="space-1", page=51, cursor=first["pagination"]["next_cursor"])
        assert second["pagination"]["has_prev"] is True
        assert not second["text"].startswith("Path:")

    @pytest.mark.asyncio
    async def test_a_cursor_from_another_node_or_garbage_is_a_400(self) -> None:
        graph = _graph()
        issued = (await _route(graph, node_id="space-1"))["pagination"]["next_cursor"]
        for cursor in (issued, "garbage"):
            with pytest.raises(HTTPException) as refused:
                await _route(graph, node_id="space-2", cursor=cursor)
            assert refused.value.status_code == 400

    @pytest.mark.asyncio
    async def test_a_root_listed_whole_takes_no_cursor(self) -> None:
        with patch("app.connectors.api.router.connection_limit", return_value={"apps": [], "recordGroups": ["g1"]}), \
             patch("app.connectors.api.router.root_lists_what_the_limit_names", return_value=True), \
             pytest.raises(HTTPException) as refused:
            await _route(_graph(), cursor="tok")
        assert refused.value.status_code == 400

    def test_the_spec_documents_the_cursor_and_every_pagination_field(self) -> None:
        spec = openapi_spec()
        schema = spec["components"]["schemas"]["KnowledgeGraphPaginationSchema"]
        # The schema is closed, so a field the model sends and the spec lacks fails a strict client.
        assert set(schema["properties"]) == set(PaginationInfo.model_fields)
        params = {p["name"] for p in spec["paths"]["/connectors/navigate"]["get"]["parameters"]}
        assert "cursor" in params


async def _list_files(response: SimpleNamespace, **kwargs: object) -> tuple[bool, str, object, MagicMock]:
    service = AsyncMock()
    service.get_nodes = AsyncMock(return_value=response)
    state = {"graph_provider": AsyncMock(), "org_id": "o1", "user_id": "u1", "logger": logging.getLogger("t"),
             "config_service": _config()}
    with patch("app.agents.actions.knowledge_graph.ops.scope.resolve_scope", AsyncMock(
            return_value=KnowledgeScope(app_ids=("app1",), kb_ids=()))), \
         patch("app.agents.actions.knowledge_graph.ops.listing.KnowledgeHubService", return_value=service) as built, \
         patch("app.modules.agents.qna.chat_state.remember_record_ids"), \
         patch("app.utils.chat_helpers.get_record_id_shortener_if_enabled", return_value=None):
        ok, text = await execute_list_files(state, **kwargs)
    return ok, text, service.get_nodes.call_args, built


def _listing(next_cursor: str | None, issued_apps: list[str] | None = None) -> SimpleNamespace:
    item = SimpleNamespace(nodeType=SimpleNamespace(value="record"), id="r1", name="Report", webUrl=None)
    return SimpleNamespace(
        success=True, error=None, total=None, items=[item],
        pagination=SimpleNamespace(hasNext=True, nextCursor=next_cursor),
        filters=SimpleNamespace(applied=SimpleNamespace(connectorIds=issued_apps)),
    )


class TestListFilesByCursor:
    @pytest.mark.asyncio
    async def test_the_more_line_names_the_cursor_and_the_cursor_reaches_the_service(self) -> None:
        ok, text, call, built = await _list_files(_listing("tok-2", ["app1"]), cursor="tok-1")
        assert ok
        assert call.kwargs["cursor"] == "tok-1"
        assert built.call_args.kwargs["config_service"] is not None
        assert 'call again with cursor="tok-2"' in text

    @pytest.mark.asyncio
    async def test_a_cursor_issued_over_wider_sources_is_refused(self) -> None:
        ok, text, _call, _built = await _list_files(_listing("tok-2", ["app1", "app9"]), cursor="tok-1")
        assert not ok
        assert "not a page of this listing" in text
        assert "Report" not in text
