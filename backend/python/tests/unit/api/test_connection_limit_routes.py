"""PP-7: every route a connected app reaches data through keeps to its limit.

The limit rides on the request user as ``knowledgeLimit`` (see
``app.api.middlewares.auth``). These call the handlers directly with a graph
whose ``check_access`` applies the scopes it is given, so what is checked is
the real ``outside_connection`` path, not a stub of it.
"""

import json
from collections.abc import Iterable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.actions.knowledge_graph.models import (
    LookupMatch,
    LookupResult,
    NavigationView,
    NodeRef,
    NodeRow,
)
from app.api.connection_limit import (
    ids_outside_connection,
    ids_outside_connection_on_restore,
    keep_path_inside_connection,
    refuse_outside_connection,
)
from app.api.routes.agent import _resolve_turn_filters
from app.api.routes.records import read_record_content
from app.api.routes.search import SearchQuery, search
from app.connectors.api.router import (
    get_record_by_id,
    get_record_content,
    knowledge_graph_lookup,
    knowledge_graph_navigate,
    stream_record,
)
from app.connectors.sources.localKB.api.knowledge_hub_router import _handle_get_nodes
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.modules.retrieval.selection_scope import (
    CONNECTION_LIMIT_TOO_LARGE_MESSAGE,
    CONNECTION_OUTSIDE_MESSAGE,
    SelectionScope,
)
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.attachment_utils import keep_accessible_attachments

ROUTER = "app.connectors.api.router"

#: Each node the fake graph knows, with the app it lives in.
ROWS = {
    "app-in": {"id": "app-in"},
    "app-out": {"id": "app-out"},
    "rec-in": {"id": "rec-in", "connectorId": "app-in", "groupIds": []},
    "rec-out": {"id": "rec-out", "connectorId": "app-out", "groupIds": []},
}
LIMIT = {"apps": ["app-in"], "recordGroups": [], "records": []}


def _graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "ukey-1"})

    async def check_access(
        user_key: str, org_id: str, *, node_ids: Iterable[str] = (),
        scopes: Iterable[SelectionScope] = (), **_: object,
    ) -> AccessCheck:
        ids = frozenset(i for i in node_ids if i in ROWS)
        return AccessCheck(
            node_ids=ids,
            node_ids_in_scope=frozenset(i for i in ids if all(s.admits(ROWS[i]) for s in scopes)),
        )

    graph.check_access = AsyncMock(side_effect=check_access)
    return graph


def _request(limit: dict | None = LIMIT, graph: AsyncMock | None = None) -> MagicMock:
    user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    if limit is not None:
        user.update(isOAuth=True, knowledgeLimit=limit)
    request = MagicMock()
    request.state.user = user
    request.app.state.graph_provider = graph
    return request


class TestTheSharedCheck:
    @pytest.mark.asyncio
    async def test_a_request_without_a_limit_asks_the_graph_nothing(self) -> None:
        graph = _graph()
        assert await ids_outside_connection(_request(None), graph, "org-1", "user-1", ["rec-out"]) == set()
        graph.get_user_by_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_record_outside_the_limit_is_refused_with_its_reason(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await refuse_outside_connection(_request(), _graph(), "org-1", "user-1", "rec-out")
        assert exc.value.status_code == 403 and exc.value.detail == CONNECTION_OUTSIDE_MESSAGE
        await refuse_outside_connection(_request(), _graph(), "org-1", "user-1", "rec-in")

    @pytest.mark.asyncio
    async def test_a_limit_too_large_to_resolve_is_a_422_not_a_500(self) -> None:
        from app.modules.retrieval.selection_scope import SelectionTooLargeError

        with patch("app.api.connection_limit.outside_connection", AsyncMock(side_effect=SelectionTooLargeError)):
            with pytest.raises(HTTPException) as exc:
                await refuse_outside_connection(_request(), _graph(), "org-1", "user-1", "rec-in")
        assert exc.value.status_code == 422 and exc.value.detail == CONNECTION_LIMIT_TOO_LARGE_MESSAGE

    @pytest.mark.asyncio
    async def test_a_node_the_graph_does_not_know_is_outside(self) -> None:
        """Deleted, or never there: never treated as inside."""
        assert await ids_outside_connection(_request(), _graph(), "org-1", "user-1", ["gone"]) == {"gone"}


def _trashed(key: str, parent: str | None = None) -> dict:
    return {"record": {"_key": key}, "parentId": parent}


class TestRestoreInTheTrash:
    """A deleted node is never returned by check_access, so a restore judges a
    delete batch by where it sits."""

    @staticmethod
    async def _outside(trashed: list[dict], kb_id: str = "app-in", live_ids: list[str] | None = None,
                       limit: dict | None = LIMIT, graph: AsyncMock | None = None) -> set[str]:
        return await ids_outside_connection_on_restore(
            _request(limit), graph or _graph(), "org-1", "user-1", kb_id, live_ids or [], trashed,
        )

    @pytest.mark.asyncio
    async def test_a_file_from_a_collection_inside_the_limit_comes_back(self) -> None:
        assert await self._outside([_trashed("gone")]) == set()
        assert await self._outside([_trashed("gone")], kb_id="app-out") == {"gone"}

    @pytest.mark.asyncio
    async def test_a_batch_is_judged_by_the_live_folder_it_came_from(self) -> None:
        folder = [_trashed("dir"), _trashed("file", "dir")]
        assert await self._outside([{**folder[0], "parentId": "rec-in"}, folder[1]]) == set()
        assert await self._outside([{**folder[0], "parentId": "rec-out"}, folder[1]]) == {"dir", "file"}

    @pytest.mark.asyncio
    async def test_one_member_outside_the_limit_is_named_for_the_refusal(self) -> None:
        batch = [_trashed("a", "rec-in"), _trashed("b", "rec-out")]
        assert await self._outside(batch) == {"b"}

    @pytest.mark.asyncio
    async def test_a_limit_that_names_a_folder_in_the_trash_admits_what_was_deleted_with_it(self) -> None:
        graph = _graph()
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [], "records": [{"id": "dir", "vrid": "", "connectorId": "app-in"}], "truncated": False,
        })
        limit = {"apps": [], "recordGroups": [], "records": ["dir"]}
        batch = [_trashed("dir", "rec-out"), _trashed("file", "dir")]
        assert await self._outside(batch, limit=limit, graph=graph) == set()
        assert await self._outside([_trashed("other", "rec-out")], limit=limit, graph=graph) == {"other"}

    @pytest.mark.asyncio
    async def test_a_live_id_is_checked_as_it_is_and_no_limit_asks_nothing(self) -> None:
        assert await self._outside([], live_ids=["rec-in", "rec-out"]) == {"rec-out"}
        graph = _graph()
        assert await self._outside([_trashed("gone")], limit=None, graph=graph) == set()
        graph.get_user_by_user_id.assert_not_awaited()


class TestRecordsById:
    @staticmethod
    def _graph_with_access() -> AsyncMock:
        graph = _graph()
        graph.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})
        graph.get_document = AsyncMock(return_value={"connectorId": "app-out", "_key": "org-1"})
        return graph

    @pytest.mark.asyncio
    async def test_record_metadata(self) -> None:
        request = _request()
        request.app.container.config_service.return_value = AsyncMock()
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await get_record_by_id("rec-out", request, graph_provider=self._graph_with_access())
            assert exc.value.status_code == 403
            assert await get_record_by_id("rec-in", request, graph_provider=self._graph_with_access())

    @pytest.mark.asyncio
    async def test_record_content_is_never_fetched_outside_the_limit(self) -> None:
        request = _request()
        request.app.container.config_service.return_value = AsyncMock()
        fetch = AsyncMock(return_value={"ok": True, "records": []})
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._fetch_multiple_records_impl", fetch):
            with pytest.raises(HTTPException) as exc:
                await get_record_content("rec-out", request, graph_provider=self._graph_with_access())
        assert exc.value.status_code == 403
        fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_streaming_a_file_outside_the_limit(self) -> None:
        graph = self._graph_with_access()
        graph.get_record_by_id = AsyncMock(return_value=SimpleNamespace(
            id="rec-out", org_id="org-1", connector_id="app-out",
        ))
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._resolve_record_content_response", AsyncMock()) as serve:
            with pytest.raises(HTTPException) as exc:
                await stream_record(_request(), "rec-out", graph_provider=graph, config_service=AsyncMock())
        assert exc.value.status_code == 403
        serve.assert_not_awaited()

    @staticmethod
    def _reader_request(graph: AsyncMock) -> MagicMock:
        request = _request(graph=graph)
        request.app.container.graph_provider = AsyncMock(return_value=graph)
        request.app.container.blob_store = AsyncMock(return_value=MagicMock())
        return request

    @pytest.mark.asyncio
    async def test_a_budgeted_read_outside_the_limit_is_refused_before_any_fetch(self) -> None:
        graph = self._graph_with_access()
        fetch = AsyncMock(return_value={"ok": True, "records": []})
        with patch("app.api.routes.records.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch("app.api.routes.records._fetch_multiple_records_impl", fetch):
            with pytest.raises(HTTPException) as exc:
                await read_record_content("rec-out", self._reader_request(graph), mode="record", query=None,
                                          start_block=None, start_record=0, max_chars=40_000)
        assert exc.value.status_code == 403 and exc.value.detail == CONNECTION_OUTSIDE_MESSAGE
        fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_switched_off_demo_record_outside_the_limit_is_still_a_404(self) -> None:
        graph = self._graph_with_access()
        with patch("app.api.routes.records.is_hidden_demo_record", AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await read_record_content("rec-out", self._reader_request(graph), mode="record", query=None,
                                          start_block=None, start_record=0, max_chars=40_000)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_thread_read_is_bounded_to_the_limit(self) -> None:
        """The reader filters replies, so its note, summaries and skipped ids
        name nothing outside the limit either."""
        from app.agents.actions.knowledge_graph.ops.scope import ids_within_scope

        graph = self._graph_with_access()
        fetch = AsyncMock(return_value={"ok": True, "records": []})
        with patch("app.api.routes.records.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch("app.api.routes.records._resolve_thread_record_group", AsyncMock(return_value={"id": "t"})), \
             patch("app.api.routes.records._fetch_thread_records_impl", fetch), \
             patch("app.api.routes.records.read_thread", AsyncMock(return_value={"content": ""})):
            await read_record_content("rec-in", self._reader_request(graph), mode="thread", query=None,
                                      start_block=None, start_record=0, max_chars=40_000)
        state = fetch.call_args.kwargs["scope_state"]
        assert await ids_within_scope(state, graph, "ukey-1", "org-1", ["rec-in", "rec-out"]) == {"rec-in"}


class TestBrowsing:
    @staticmethod
    async def _navigate(request: MagicMock, graph: AsyncMock, node_id: str | None = None,
                        view: NavigationView | None = None, apps: list[str] | None = None,
                        navigator: MagicMock | None = None) -> dict:
        catalog = MagicMock(connector_ids=MagicMock(return_value=apps or ["app-in", "app-out"]), connectors=[])
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=catalog)), \
             patch(f"{ROUTER}.GraphNavigator", navigator or MagicMock()) as patched:
            patched.return_value.navigate = AsyncMock(return_value=view or NavigationView(
                current=None, breadcrumbs=[], related=[], pagination=None, web_url=None,
                indexing_status=None, connector=None,
                rows=[
                    NodeRow(id=i, name=i, node_type="app", sub_type=None, is_record=False,
                            has_children=True, detail=None)
                    for i in ("app-in", "app-out")
                ],
            ))
            return await knowledge_graph_navigate(
                request=request, graph_provider=graph, node_id=node_id, page=1, limit=50, depth=1,
                node_types=None, created_after=None, created_before=None,
                modified_after=None, modified_before=None,
            )

    @pytest.mark.asyncio
    async def test_the_root_lists_only_what_is_shared(self) -> None:
        result = await self._navigate(_request(), _graph())
        assert [row["id"] for row in result["rows"]] == ["app-in"]
        assert "app-out" not in result["text"]

    @pytest.mark.asyncio
    async def test_the_root_is_bounded_before_paging(self) -> None:
        """Rows outside the limit never fill a page, so no page comes back empty."""
        navigator = MagicMock()
        await self._navigate(_request(), _graph(), navigator=navigator)
        assert navigator.return_value.navigate.call_args.kwargs["connector_ids"] == ["app-in"]
        await self._navigate(_request(None), _graph(), navigator=navigator)
        assert navigator.return_value.navigate.call_args.kwargs["connector_ids"] is None

    @pytest.mark.asyncio
    async def test_a_root_with_no_app_inside_the_limit_is_empty(self) -> None:
        navigator = MagicMock()
        unreachable = {"apps": ["app-gone"], "recordGroups": [], "records": []}
        result = await self._navigate(_request(unreachable), _graph(), navigator=navigator)
        assert result["rows"] == []
        navigator.return_value.navigate.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_root_lists_a_collection_the_limit_names(self) -> None:
        """The real catalog leaves collections out (filter options skip type KB),
        so a limit naming only a collection must still reach it at the root."""
        graph = _graph()
        graph.get_knowledge_hub_filter_options = AsyncMock(
            return_value={"apps": [{"id": "app-out", "name": "Drive", "type": "DRIVE"}]},
        )
        with patch(f"{ROUTER}.GraphNavigator") as navigator:
            navigator.return_value.navigate = AsyncMock(return_value=NavigationView(
                current=None, breadcrumbs=[], related=[], pagination=None, web_url=None,
                indexing_status=None, connector=None,
                rows=[NodeRow(id="rec-in", name="rec-in", node_type="record", sub_type=None,
                              is_record=True, has_children=False, detail=None)],
            ))
            result = await knowledge_graph_navigate(
                request=_request(), graph_provider=graph, node_id=None, page=1, limit=50, depth=1,
                node_types=None, created_after=None, created_before=None,
                modified_after=None, modified_before=None,
            )
        assert navigator.return_value.navigate.call_args.kwargs["connector_ids"] == ["app-in"]
        assert [row["id"] for row in result["rows"]] == ["rec-in"]

    @pytest.mark.asyncio
    async def test_opening_a_node_outside_the_limit_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await self._navigate(_request(), _graph(), node_id="app-out")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_the_path_above_a_shared_node_names_only_what_is_shared(self) -> None:
        crumbs = [NodeRef(id=i, name=f"name-{i}", node_type="app", sub_type=None, is_record=False)
                  for i in ("app-out", "app-in")]
        view = NavigationView(current=None, breadcrumbs=crumbs, rows=[], related=[], pagination=None,
                              web_url=None, indexing_status=None, connector=None)
        result = await self._navigate(_request(), _graph(), node_id="rec-in", view=view)
        assert [crumb["id"] for crumb in result["breadcrumbs"]] == ["app-in"]
        assert "name-app-out" not in result["text"]

    @pytest.mark.asyncio
    async def test_without_a_limit_the_listing_is_untouched(self) -> None:
        result = await self._navigate(_request(None), _graph())
        assert [row["id"] for row in result["rows"]] == ["app-in", "app-out"]

    @pytest.mark.asyncio
    async def test_lookup_answers_a_record_outside_the_limit_as_not_found(self) -> None:
        catalog = MagicMock(connector_ids=MagicMock(return_value=["app-in", "app-out"]),
                            is_empty=MagicMock(return_value=False), connectors=[])
        matches = [
            LookupMatch(id=i, name=i, record_type="FILE", connector_name="KB", web_url=None,
                        indexing_status="COMPLETED", identifier_used=f"ref-{i}")
            for i in ("rec-in", "rec-out")
        ]
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=catalog)), \
             patch(f"{ROUTER}.RecordResolver") as resolver:
            resolver.return_value.resolve_many = AsyncMock(return_value=LookupResult(
                matches=matches, ambiguous=True, not_found_identifiers=[],
            ))
            result = await knowledge_graph_lookup(
                request=_request(), graph_provider=_graph(),
                identifiers=["ref-rec-in", "ref-rec-out"], connector_name=None,
            )
        assert [m["id"] for m in result["matches"]] == ["rec-in"]
        assert result["not_found_identifiers"] == ["ref-rec-out"]
        assert result["ambiguous"] is False

    @pytest.mark.asyncio
    async def test_the_knowledge_hub_hides_nodes_and_refuses_parents_outside_the_limit(self) -> None:
        graph = _graph()
        service = AsyncMock()
        service.get_nodes = AsyncMock(return_value=SimpleNamespace(
            success=True, error=None, errorCode=None, typed_records={"rec-out": {}, "rec-in": {}},
            items=[SimpleNamespace(id=i, parentId=None, parent=None) for i in ("rec-in", "rec-out")],
            breadcrumbs=None, parentNode=None,
        ))
        kwargs = {
            "parent_type": None, "only_containers": False, "page": 1, "limit": 50, "sort_by": "updatedAt",
            "sort_order": "desc", "q": None, "node_types": None, "record_types": None, "origins": None,
            "connector_ids": None, "indexing_status": None, "created_at": None, "updated_at": None, "size": None,
            "flattened": True, "include": None,
        }
        result = await _handle_get_nodes(
            request=_request(graph=graph), knowledge_hub_service=service, parent_id=None, **kwargs,
        )
        assert [item.id for item in result.items] == ["rec-in"]
        assert list(result.typed_records) == ["rec-in"]

        outside_parent = "6f1c2b9e-0000-4000-8000-000000000000"
        with pytest.raises(HTTPException) as exc:
            await _handle_get_nodes(
                request=_request(graph=graph), knowledge_hub_service=service,
                parent_id=outside_parent, **kwargs,
            )
        assert exc.value.status_code == 403


class TestSearchRoute:
    @staticmethod
    async def _search(request, filters) -> dict:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={"searchResults": [], "status_code": 200})
        request.app.container.logger.return_value = MagicMock()
        chain = MagicMock(ainvoke=AsyncMock(return_value=""))
        with patch("app.api.routes.search.setup_query_transformation", return_value=(chain, chain)), \
             patch("app.api.routes.search.resolve_llm_for_search", AsyncMock(return_value=MagicMock())):
            await search(request=request, body=SearchQuery(query="q", filters=filters),
                         retrieval_service=retrieval, graph_provider=MagicMock())
        return retrieval.search_with_filters.call_args.kwargs["filter_groups"]

    @pytest.mark.asyncio
    async def test_a_limited_search_runs_inside_the_limit(self) -> None:
        sent = await self._search(_request(), {})
        assert sent["apps"] == ["app-in"] and sent["connectionApps"] == ["app-in"] and sent["strictScope"]

    @pytest.mark.asyncio
    async def test_a_client_sent_bound_is_dropped_and_cannot_widen(self) -> None:
        sent = await self._search(_request(), {"kb": ["app-out"], "connectionApps": ["app-out"],
                                               "allowedApps": ["app-out"]})
        assert sent["connectionApps"] == ["app-in"] and "allowedApps" not in sent
        assert sent["kb"] == ["app-out"]  # the pick stays; the bound drops every hit outside the limit

    @pytest.mark.asyncio
    async def test_an_unlimited_search_drops_client_sent_bounds_only(self) -> None:
        sent = await self._search(_request(None), {"apps": ["a"], "connectionApps": ["x"]})
        assert sent == {"apps": ["a"]}


class TestKnowledgeBaseRoutes:
    @staticmethod
    def _path_request(limit=LIMIT, **path_params) -> MagicMock:
        request = _request(limit, graph=_graph())
        request.path_params = path_params
        return request

    @pytest.mark.asyncio
    async def test_a_knowledge_base_folder_or_record_outside_the_limit_is_refused_by_id(self) -> None:
        for params in ({"kb_id": "app-out"}, {"kb_id": "app-in", "folder_id": "rec-out"}, {"record_id": "rec-out"},
                       {"record_group_id": "rec-out"}):
            with pytest.raises(HTTPException) as exc:
                await keep_path_inside_connection(self._path_request(**params))
            assert exc.value.status_code == 403, params
        await keep_path_inside_connection(self._path_request(kb_id="app-in", record_id="rec-in"))

    @pytest.mark.asyncio
    async def test_routes_without_ids_and_unlimited_tokens_pass_untouched(self) -> None:
        await keep_path_inside_connection(self._path_request())
        request = self._path_request(None, kb_id="app-out")
        await keep_path_inside_connection(request)
        request.app.state.graph_provider.get_user_by_user_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_folder_only_limit_reaches_its_folder_through_its_collection(self) -> None:
        limit = {"apps": [], "recordGroups": [], "records": ["rec-in"]}
        graph = _graph()
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [], "records": [{"id": "rec-in", "vrid": "", "connectorId": "app-in"}], "truncated": False,
        })

        def request(**params) -> MagicMock:
            req = _request(limit, graph=graph)
            req.path_params = params
            return req

        graph.get_document = AsyncMock(return_value={"connectorId": "app-in"})
        await keep_path_inside_connection(request(kb_id="app-in", folder_id="rec-in"))
        for params in ({"kb_id": "app-in"}, {"kb_id": "app-in", "folder_id": "rec-out"}):
            with pytest.raises(HTTPException):
                await keep_path_inside_connection(request(**params))
        graph.get_document = AsyncMock(return_value={"connectorId": "another-kb"})
        with pytest.raises(HTTPException):
            await keep_path_inside_connection(request(kb_id="app-in", folder_id="rec-in"))

    @pytest.mark.asyncio
    async def test_a_move_lands_only_inside_the_limit(self) -> None:
        from app.connectors.sources.localKB.api.kb_router import move_record

        async def move(new_parent_id: str | None) -> AsyncMock:
            request = _request(graph=_graph())
            request.json = AsyncMock(return_value={"newParentId": new_parent_id})
            service = AsyncMock()
            service.move_record = AsyncMock(return_value={"success": True})
            await move_record("app-in", "rec-in", request, kb_service=service)
            return service.move_record

        (await move("rec-in")).assert_awaited_once()
        (await move(None)).assert_awaited_once()
        for outside in ("rec-out", "app-out"):
            with pytest.raises(HTTPException) as exc:
                await move(outside)
            assert exc.value.status_code == 403
        limit = {"apps": [], "recordGroups": [], "records": ["rec-in"]}
        graph = _graph()
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [], "records": [{"id": "rec-in", "vrid": "", "connectorId": "app-in"}], "truncated": False,
        })
        request = _request(limit, graph=graph)
        request.json = AsyncMock(return_value={"newParentId": None})
        service = AsyncMock()
        with pytest.raises(HTTPException):
            await move_record("app-in", "rec-in", request, kb_service=service)
        service.move_record.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_restore_is_left_to_the_restore_itself(self) -> None:
        request = self._path_request(record_id="rec-out")
        request.scope = {"route": SimpleNamespace(path="/api/v1/kb/record/{record_id}/restore")}
        await keep_path_inside_connection(request)

    def test_every_knowledge_base_route_carries_the_check(self) -> None:
        from app.connectors.sources.localKB.api.kb_router import kb_router

        for route in kb_router.routes:
            calls = [d.dependency for d in route.dependencies]
            assert keep_path_inside_connection in calls, route.path

    @pytest.mark.asyncio
    async def test_the_knowledge_base_list_hides_what_is_not_shared(self) -> None:
        from app.connectors.sources.localKB.api.kb_router import (
            list_user_knowledge_bases,
        )

        service = AsyncMock()
        service.list_user_knowledge_bases = AsyncMock(return_value={
            "knowledgeBases": [{"id": "app-in"}, {"id": "app-out"}], "pagination": {}, "filters": {},
        })
        result = await list_user_knowledge_bases(
            request=_request(graph=_graph()), page=1, limit=20, search=None, permissions=None,
            sort_by="name", sort_order="asc", kb_service=service,
        )
        assert [kb["id"] for kb in result["knowledgeBases"]] == ["app-in"]

    def test_record_delete_and_reindex_carry_the_check(self) -> None:
        from app.connectors.api.router import router

        guarded = {
            (route.path, method)
            for route in router.routes
            for method in getattr(route, "methods", ())
            if keep_path_inside_connection in [d.dependency for d in route.dependencies]
        }
        assert ("/api/v1/records/{record_id}", "DELETE") in guarded
        assert ("/api/v1/records/{record_id}/reindex", "POST") in guarded
        assert ("/api/v1/record-groups/{record_group_id}/reindex", "POST") in guarded


class TestChatTurns:
    @pytest.mark.asyncio
    async def test_an_attached_record_outside_the_limit_is_dropped_and_an_upload_kept(self) -> None:
        graph = _graph()
        access = graph.check_access.side_effect

        async def check_access(user_key: str, org_id: str, **kwargs: object) -> AccessCheck:
            if kwargs.get("virtual_record_ids"):
                return AccessCheck(records_by_vrid={"v-in": "rec-in", "v-out": "rec-out", "v-up": "upload"})
            return await access(user_key, org_id, **kwargs)

        graph.check_access = AsyncMock(side_effect=check_access)
        graph.get_nodes_by_field_in = AsyncMock(return_value=[
            {"id": "upload", "orgId": "org-1", "origin": "UPLOAD", "connectorId": "attachments_org-1"},
        ])
        attachments = [{"virtualRecordId": v} for v in ("v-in", "v-out", "v-up")]
        history = [{"attachments": [{"virtualRecordId": "v-out"}]}]
        kept, kept_history = await keep_accessible_attachments(
            graph, org_id="org-1", user_id="user-1", is_service_account=False, attachments=attachments,
            previous_conversations=history, logger=MagicMock(), connection_sources=LIMIT,
        )
        assert [a["recordId"] for a in kept] == ["rec-in", "upload"]
        assert kept_history == [{"attachments": []}]

    @pytest.mark.asyncio
    async def test_a_universal_agent_turn_with_no_pick_is_prepared_inside_the_limit(self) -> None:
        prepare = AsyncMock(return_value={"prepared": True})
        with patch("app.api.routes.agent.prepare_turn_scope", prepare):
            filters = await _resolve_turn_filters(
                agent_id="agentIdPlaceholder", agent_knowledge=[], requested_filters={},
                graph_provider=_graph(), caller_user_id="user-1", org_id="org-1", logger=MagicMock(),
                connection_sources={"apps": [], "recordGroups": [], "records": ["rec-in"]},
            )
        assert filters == {"prepared": True}
        assert prepare.call_args.kwargs["connection_sources"]["records"] == ["rec-in"]


#: A file in a folder of app-in; a limit naming only the file leaves the folder out.
FILE_LIMIT = {"apps": [], "recordGroups": [], "records": ["rec-in"]}
FOLDER_LIMIT = {"apps": [], "recordGroups": [], "records": ["folder-in"]}


def _graph_under(limit: dict) -> AsyncMock:
    graph = _graph()
    ROWS.setdefault("folder-in", {"id": "folder-in", "connectorId": "app-in", "groupIds": []})
    below = {"rec-in": ["rec-in"], "folder-in": ["folder-in", "rec-in"]}
    graph.get_selection_nodes = AsyncMock(return_value={"groups": [], "records": [
        {"id": i, "vrid": "", "connectorId": "app-in"} for named in limit["records"] for i in below.get(named, [])
    ]})
    return graph


def _file_doc() -> dict:
    return {
        "_key": "rec-in", "recordName": "budget.xlsx", "connectorId": "app-in",
        "recordGroupId": "app-in", "externalGroupId": "ext-collection",
        "parentNodeId": "folder-in", "externalParentId": "ext-folder", "rootRecordGroupId": None,
    }


class TestParentsOutsideTheLimit:
    """A file the limit names can sit in a folder the limit leaves out: the
    file is returned and that folder is not named."""

    @staticmethod
    async def _search(limit: dict | None) -> tuple[dict, AsyncMock]:
        graph = _graph_under(limit or FILE_LIMIT)
        doc = _file_doc()
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"metadata": {"recordId": "rec-in"}}], "records": [doc],
            "virtual_to_record_map": {"v-in": doc}, "status_code": 200,
        })
        request = _request(limit)
        request.app.container.logger.return_value = MagicMock()
        chain = MagicMock(ainvoke=AsyncMock(return_value=""))
        with patch("app.api.routes.search.setup_query_transformation", return_value=(chain, chain)), \
             patch("app.api.routes.search.resolve_llm_for_search", AsyncMock(return_value=MagicMock())):
            response = await search(request=request, body=SearchQuery(query="q"),
                                    retrieval_service=retrieval, graph_provider=graph)
        return json.loads(response.body), graph

    @pytest.mark.asyncio
    async def test_a_search_hit_named_alone_does_not_name_its_folder(self) -> None:
        body, _ = await self._search(FILE_LIMIT)

        (record,) = body["records"]
        assert record["parentNodeId"] is None and record["externalParentId"] is None
        assert record["recordGroupId"] is None and record["externalGroupId"] is None
        assert body["virtual_to_record_map"]["v-in"]["parentNodeId"] is None
        assert "folder-in" not in json.dumps(body) and "ext-folder" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_a_search_hit_in_a_named_folder_keeps_its_folder(self) -> None:
        body, _ = await self._search(FOLDER_LIMIT)

        (record,) = body["records"]
        assert record["parentNodeId"] == "folder-in" and record["externalParentId"] == "ext-folder"
        assert record["recordGroupId"] is None  # the collection is only partly shared

    @staticmethod
    async def _search_with_paths(limit: dict | None) -> dict:
        graph = _graph_under(limit if limit and limit.get("records") else FILE_LIMIT)
        doc = {**_file_doc(), "filePath": "src/billing/secret/a.py",
               "fileRecord": {"path": "/Finance/Board/budget.xlsx", "webUrl": "https://files.example/b"}}
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"metadata": {"recordId": "rec-in"}}], "records": [doc],
            "virtual_to_record_map": {"v-in": doc}, "status_code": 200,
        })
        request = _request(limit)
        request.app.container.logger.return_value = MagicMock()
        chain = MagicMock(ainvoke=AsyncMock(return_value=""))
        with patch("app.api.routes.search.setup_query_transformation", return_value=(chain, chain)), \
             patch("app.api.routes.search.resolve_llm_for_search", AsyncMock(return_value=MagicMock())):
            response = await search(request=request, body=SearchQuery(query="q"),
                                    retrieval_service=retrieval, graph_provider=graph)
        return json.loads(response.body)

    @pytest.mark.asyncio
    async def test_a_narrow_limits_search_names_no_source_path(self) -> None:
        body = await self._search_with_paths(FILE_LIMIT)

        (record,) = body["records"]
        assert record["filePath"] is None and record["fileRecord"]["path"] is None
        assert record["fileRecord"]["webUrl"] == "https://files.example/b"
        assert "secret" not in json.dumps(body) and "Board" not in json.dumps(body)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, LIMIT])
    async def test_a_search_keeps_source_paths_without_a_limit_or_under_a_whole_app_one(self, limit) -> None:
        body = await self._search_with_paths(limit)

        (record,) = body["records"]
        assert record["filePath"] == "src/billing/secret/a.py"
        assert record["fileRecord"]["path"] == "/Finance/Board/budget.xlsx"

    @pytest.mark.asyncio
    async def test_an_unlimited_search_is_unchanged(self) -> None:
        body, graph = await self._search(None)

        assert body["records"] == [_file_doc()]
        graph.check_access.assert_not_awaited()

    @staticmethod
    async def _details(limit: dict | None) -> dict:
        graph = _graph_under(limit or FILE_LIMIT)
        graph.check_record_access_with_details = AsyncMock(return_value={
            "record": _file_doc(),
            "knowledgeBase": {"id": "app-in", "name": "Finance", "orgId": "org-1"},
            "folder": {"id": "folder-in", "name": "Q3"},
        })
        graph.get_document = AsyncMock(return_value={"connectorId": "app-in", "_key": "rec-in"})
        request = _request(limit)
        request.app.container.config_service.return_value = AsyncMock()
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._stage_states_of", AsyncMock(return_value=None)):
            return await get_record_by_id("rec-in", request, graph_provider=graph)

    @pytest.mark.asyncio
    async def test_record_details_of_a_file_named_alone_name_no_folder(self) -> None:
        details = await self._details(FILE_LIMIT)

        assert details["folder"] is None and details["knowledgeBase"] is None
        assert details["record"]["parentNodeId"] is None and details["record"]["recordName"] == "budget.xlsx"

    @pytest.mark.asyncio
    async def test_record_details_in_a_named_folder_keep_the_folder(self) -> None:
        details = await self._details(FOLDER_LIMIT)

        assert details["folder"] == {"id": "folder-in", "name": "Q3"}
        assert details["record"]["parentNodeId"] == "folder-in"

    @pytest.mark.asyncio
    async def test_unlimited_record_details_are_unchanged(self) -> None:
        details = await self._details(None)

        assert details["folder"]["id"] == "folder-in" and details["knowledgeBase"]["id"] == "app-in"
        assert details["record"] == _file_doc()

    @pytest.mark.asyncio
    async def test_a_restore_refusal_does_not_name_a_trashed_folder_outside_the_limit(self) -> None:
        members = [{"record": {"_key": "rec-in"}, "parentId": "trashed-dir", "parentIsDeleted": True,
                    "parentName": "Secret plans"}]

        async def refusal(limit: dict | None) -> dict:
            request = _request(limit, graph=_graph_under(limit or FILE_LIMIT))
            outside = None if limit is None else (
                lambda kb_id, live, trashed: ids_outside_connection_on_restore(
                    request, request.app.state.graph_provider, "org-1", "user-1", kb_id, live, trashed,
                )
            )
            return await KnowledgeBaseService._trashed_parent_refusal(
                "budget.xlsx", members, {"rec-in"}, "app-in", outside,
            )

        hidden = await refusal(FILE_LIMIT)
        assert "Secret plans" not in hidden["reason"] and "parentId" not in hidden
        assert hidden["code"] == 409
        shown = await refusal(None)
        assert "Secret plans" in shown["reason"] and shown["parentId"] == "trashed-dir"
        whole = await refusal(LIMIT)
        assert "Secret plans" in whole["reason"]


SOURCE_PATH = "/Finance/Q3/budget.xlsx"


class TestSourcePathsUnderALimit:
    """A file's path in its source names every folder above it. A limit that
    covers the file's whole app keeps it; any narrower limit does not."""

    @staticmethod
    async def _details(limit: dict | None, record: dict | None = None) -> dict:
        graph = _graph_under(limit if limit and limit.get("records") else FILE_LIMIT)
        graph.check_record_access_with_details = AsyncMock(return_value={
            "record": record or {**_file_doc(), "fileRecord": {
                "path": SOURCE_PATH, "webUrl": "https://files.example/budget.xlsx", "extension": "xlsx",
            }},
            "knowledgeBase": None, "folder": None,
        })
        graph.get_document = AsyncMock(return_value={"connectorId": "app-in", "_key": "rec-in"})
        request = _request(limit)
        request.app.container.config_service.return_value = AsyncMock()
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._stage_states_of", AsyncMock(return_value=None)):
            return await get_record_by_id("rec-in", request, graph_provider=graph)

    @pytest.mark.asyncio
    async def test_record_details_under_a_file_limit_hide_the_path_and_keep_the_web_url(self) -> None:
        details = await self._details(FILE_LIMIT)

        file_record = details["record"]["fileRecord"]
        assert file_record["path"] is None
        assert file_record["webUrl"] == "https://files.example/budget.xlsx" and file_record["extension"] == "xlsx"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, LIMIT])
    async def test_record_details_keep_the_path_without_a_limit_or_under_a_whole_app_limit(self, limit) -> None:
        details = await self._details(limit)

        assert details["record"]["fileRecord"]["path"] == SOURCE_PATH

    @pytest.mark.asyncio
    async def test_a_source_parent_with_no_parent_node_is_hidden_unless_the_whole_app_is_shared(self) -> None:
        record = {"_key": "rec-in", "connectorId": "app-in", "externalParentId": "ext-folder", "parentNodeId": None}

        named_alone = await self._details(FILE_LIMIT, dict(record))
        whole_app = await self._details(LIMIT, dict(record))
        unlimited = await self._details(None, dict(record))

        assert named_alone["record"]["externalParentId"] is None
        assert whole_app["record"]["externalParentId"] == unlimited["record"]["externalParentId"] == "ext-folder"

    @staticmethod
    async def _folder_children(limit: dict | None) -> dict:
        from app.connectors.sources.localKB.api.kb_router import get_folder_children

        service = MagicMock()
        service.get_folder_children = AsyncMock(return_value={
            "success": True,
            "container": {"id": "folder-in", "name": "Q3", "path": "/Finance/Q3", "type": "folder"},
            "folders": [{"id": "sub", "name": "Drafts", "path": "/Finance/Q3/Drafts"}],
            "records": [{"id": "rec-in", "fileRecord": {"path": SOURCE_PATH, "webUrl": "https://f/b"}}],
        })
        return await get_folder_children("app-in", "folder-in", _request(limit), kb_service=service)

    @pytest.mark.asyncio
    async def test_a_folder_listing_under_a_folder_limit_names_no_path(self) -> None:
        listed = await self._folder_children(FOLDER_LIMIT)

        assert listed["container"]["path"] is None and listed["folders"][0]["path"] is None
        assert listed["records"][0]["fileRecord"] == {"path": None, "webUrl": "https://f/b"}
        assert listed["container"]["name"] == "Q3"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, LIMIT])
    async def test_a_folder_listing_keeps_paths_without_a_limit_or_under_a_whole_app_limit(self, limit) -> None:
        listed = await self._folder_children(limit)

        assert listed["container"]["path"] == "/Finance/Q3"
        assert listed["records"][0]["fileRecord"]["path"] == SOURCE_PATH


def _collection_file(store: str) -> dict:
    """A collection file in a folder, as each store returns it. Its folder is
    named by the source parent, the folder's own id, with no parent node set:
    Arango returns the empty parent keys as null, and Neo4j, which stores no
    null property, leaves them out."""
    doc = {"_key": "rec-in", "recordName": "budget.xlsx", "connectorId": "app-in",
           "externalGroupId": "app-in", "externalParentId": "folder-in"}
    if store == "arango":
        doc.update(recordGroupId=None, rootRecordGroupId=None, parentNodeId=None)
    return doc


STORES = ["neo4j", "arango"]


class TestCollectionFilesWithNoParentNode:
    @staticmethod
    async def _search(limit: dict | None, store: str) -> dict:
        graph = _graph_under(limit if limit and limit.get("records") else FILE_LIMIT)
        doc = _collection_file(store)
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value={
            "searchResults": [{"metadata": {"recordId": "rec-in"}}], "records": [doc],
            "virtual_to_record_map": {"v-in": doc}, "status_code": 200,
        })
        request = _request(limit)
        request.app.container.logger.return_value = MagicMock()
        chain = MagicMock(ainvoke=AsyncMock(return_value=""))
        with patch("app.api.routes.search.setup_query_transformation", return_value=(chain, chain)), \
             patch("app.api.routes.search.resolve_llm_for_search", AsyncMock(return_value=MagicMock())):
            response = await search(request=request, body=SearchQuery(query="q"),
                                    retrieval_service=retrieval, graph_provider=graph)
        return json.loads(response.body)

    @staticmethod
    async def _details(limit: dict | None, store: str) -> dict:
        graph = _graph_under(limit if limit and limit.get("records") else FILE_LIMIT)
        graph.check_record_access_with_details = AsyncMock(return_value={
            "record": _collection_file(store), "knowledgeBase": None, "folder": None,
        })
        graph.get_document = AsyncMock(return_value={"connectorId": "app-in", "_key": "rec-in"})
        request = _request(limit)
        request.app.container.config_service.return_value = AsyncMock()
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._stage_states_of", AsyncMock(return_value=None)):
            return await get_record_by_id("rec-in", request, graph_provider=graph)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store", STORES)
    async def test_a_search_hit_named_alone_does_not_name_its_folder(self, store) -> None:
        body = await self._search(FILE_LIMIT, store)

        assert body["records"][0]["externalParentId"] is None
        assert body["virtual_to_record_map"]["v-in"]["externalParentId"] is None
        assert "folder-in" not in json.dumps(body)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store", STORES)
    async def test_record_details_of_a_file_named_alone_name_no_folder(self, store) -> None:
        details = await self._details(FILE_LIMIT, store)

        assert details["record"]["externalParentId"] is None
        assert "folder-in" not in json.dumps(details)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store", STORES)
    @pytest.mark.parametrize("limit", [FOLDER_LIMIT, LIMIT, None])
    async def test_a_folder_the_limit_shares_stays_named(self, limit, store) -> None:
        body = await self._search(limit, store)
        details = await self._details(limit, store)

        assert body["records"][0]["externalParentId"] == "folder-in"
        assert details["record"]["externalParentId"] == "folder-in"


class TestWhatAReadNamesBesideTheRecord:
    """Every read by id hands the connection's limit to what it names beside
    the record (links, related tables, source parent, live ticket fields)."""

    @staticmethod
    def _graph_with_access() -> AsyncMock:
        graph = _graph()
        graph.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})
        graph.get_document = AsyncMock(return_value={"connectorId": "app-in", "_key": "rec-in"})
        return graph

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, FILE_LIMIT])
    async def test_the_record_content_route(self, limit) -> None:
        request = _request(limit)
        request.app.container.config_service.return_value = AsyncMock()
        fetch = AsyncMock(return_value={"ok": True, "records": [{"id": "rec-in"}]})
        with patch(f"{ROUTER}.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch(f"{ROUTER}._fetch_multiple_records_impl", fetch), \
             patch(f"{ROUTER}.record_to_text", return_value="text"):
            await get_record_content("rec-in", request, graph_provider=_graph_under(FILE_LIMIT) if limit else _graph())
        assert fetch.await_args.kwargs["connection_sources"] == limit

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [None, FILE_LIMIT])
    async def test_the_budgeted_read_route(self, limit) -> None:
        graph = _graph_under(FILE_LIMIT)
        graph.check_record_access_with_details = AsyncMock(return_value={"hasAccess": True})
        graph.get_document = AsyncMock(return_value={"connectorId": "app-in", "_key": "rec-in"})
        request = _request(limit, graph=graph)
        request.app.container.graph_provider = AsyncMock(return_value=graph)
        request.app.container.blob_store = AsyncMock(return_value=MagicMock())
        fetch = AsyncMock(return_value={"ok": True, "records": [{"id": "rec-in"}]})
        with patch("app.api.routes.records.is_hidden_demo_record", AsyncMock(return_value=False)), \
             patch("app.api.routes.records._fetch_multiple_records_impl", fetch), \
             patch("app.api.routes.records.read_record", AsyncMock(return_value={"content": ""})):
            await read_record_content("rec-in", request, mode="record", query=None,
                                      start_block=None, start_record=0, max_chars=40_000)
        assert fetch.await_args.kwargs["connection_sources"] == limit

    @staticmethod
    def _block() -> str:
        return "Record ID: rec-in\nName: budget.xlsx\nExternal Parent ID: ext-folder\n* Linked Record ID: rec-out"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("limit", "shown"), [(FILE_LIMIT, False), (FOLDER_LIMIT, True), (None, True)])
    async def test_navigate_names_a_source_parent_only_inside_the_limit(self, limit, shown) -> None:
        graph = _graph_under(limit or FILE_LIMIT)
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "rec-in", "parentNodeId": "folder-in"}])
        view = NavigationView(
            current=NodeRef(id="rec-in", name="budget.xlsx", node_type="record", sub_type=None, is_record=True),
            breadcrumbs=[], rows=[], related=[], pagination=None, web_url=None, indexing_status=None,
            connector=None, context_block=self._block(),
        )
        result = await TestBrowsing._navigate(_request(limit), graph, node_id="rec-in", view=view)

        assert ("ext-folder" in result["context_block"]) is shown
        assert ("ext-folder" in result["text"]) is shown
        assert ("rec-out" in result["context_block"]) is (limit is None)
        if limit is None:
            graph.get_records_by_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lookup_names_no_source_parent_outside_the_limit(self) -> None:
        graph = _graph_under(FILE_LIMIT)
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "rec-in", "parentNodeId": "folder-in"}])
        catalog = MagicMock(connector_ids=MagicMock(return_value=["app-in"]),
                            is_empty=MagicMock(return_value=False), connectors=[])
        match = LookupMatch(id="rec-in", name="budget.xlsx", record_type="FILE", connector_name="KB", web_url=None,
                            indexing_status="COMPLETED", identifier_used="ref", context_block=self._block())
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=catalog)), \
             patch(f"{ROUTER}.RecordResolver") as resolver:
            resolver.return_value.resolve_many = AsyncMock(return_value=LookupResult(
                matches=[match], ambiguous=False, not_found_identifiers=[],
            ))
            result = await knowledge_graph_lookup(
                request=_request(FILE_LIMIT), graph_provider=graph, identifiers=["ref"], connector_name=None,
            )
        assert "ext-folder" not in result["matches"][0]["context_block"] and "ext-folder" not in result["text"]
        assert "rec-out" not in result["text"]


class TestACollectionUpdateResponse:
    """The answer to an update names nothing of the collection the limit leaves out."""

    @staticmethod
    async def _response(limit: dict | None) -> dict:
        from app.connectors.sources.localKB.api.kb_router import (
            _keep_update_inside_connection,
        )

        response = {
            "updatedRecord": {**_file_doc(), "fileRecord": {"path": SOURCE_PATH}},
            "kb": {"id": "app-in", "name": "Finance", "folders": [
                {"id": "folder-in", "name": "Q3"}, {"id": "rec-out", "name": "Board"},
            ]},
        }
        graph = _graph_under(limit if limit and limit.get("records") else FILE_LIMIT)
        await _keep_update_inside_connection(_request(limit, graph=graph), response, "app-in")
        return response

    @pytest.mark.asyncio
    async def test_a_file_limit_hides_the_collection_its_folder_and_path(self) -> None:
        response = await self._response(FILE_LIMIT)

        assert response["kb"] == {}
        assert response["updatedRecord"]["parentNodeId"] is None
        assert response["updatedRecord"]["fileRecord"]["path"] is None

    @pytest.mark.asyncio
    async def test_a_folder_limit_keeps_only_its_folder(self) -> None:
        response = await self._response(FOLDER_LIMIT)

        assert response["kb"] == {}  # the collection itself is only partly shared

    @pytest.mark.asyncio
    async def test_a_whole_collection_limit_keeps_folders_inside_it(self) -> None:
        response = await self._response(LIMIT)

        assert [f["id"] for f in response["kb"]["folders"]] == ["folder-in"]
        assert response["updatedRecord"]["fileRecord"]["path"] == SOURCE_PATH

    @pytest.mark.asyncio
    async def test_an_unlimited_update_is_unchanged(self) -> None:
        response = await self._response(None)

        assert len(response["kb"]["folders"]) == 2 and response["updatedRecord"] == {
            **_file_doc(), "fileRecord": {"path": SOURCE_PATH},
        }


class TestReviewFindingsOnTheRelatedStack:
    """Rows under an opened node, a whole-app limit's blocks, and an update
    whose limit check fails."""

    @staticmethod
    def _row(summary: str) -> NodeRow:
        return NodeRow(id="rec-in", name="link", node_type="record", sub_type="LINK", is_record=True,
                       has_children=False, detail=None, context_summary=summary)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("limit", "shown"), [(FOLDER_LIMIT, False), (None, True)])
    async def test_a_row_summary_names_no_linked_record_outside_the_limit(self, limit, shown) -> None:
        graph = _graph_under(FOLDER_LIMIT)
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "rec-in", "connectorId": "app-in"}])
        view = NavigationView(
            current=NodeRef(id="folder-in", name="Q3", node_type="folder", sub_type=None, is_record=True),
            breadcrumbs=[], related=[], pagination=None, web_url=None, indexing_status=None, connector=None,
            rows=[self._row("URL: https://x, Linked Record ID: rec-out")],
        )
        result = await TestBrowsing._navigate(_request(limit), graph, node_id="folder-in", view=view)

        (row,) = result["rows"]
        assert ("rec-out" in row["context_summary"]) is shown and ("rec-out" in result["text"]) is shown
        assert "URL: https://x" in row["context_summary"]

    @pytest.mark.asyncio
    async def test_a_whole_app_limit_keeps_a_blocks_path_and_source_parent(self) -> None:
        graph = _graph()
        graph.get_records_by_record_ids = AsyncMock(return_value=[{"id": "rec-in", "connectorId": "app-in"}])
        block = "Record ID: rec-in\nName: a.py\nExternal Parent ID: ext-dir\nPath: src/billing/a.py"
        view = NavigationView(
            current=NodeRef(id="rec-in", name="a.py", node_type="record", sub_type=None, is_record=True),
            breadcrumbs=[], rows=[], related=[], pagination=None, web_url=None, indexing_status=None,
            connector=None, context_block=block,
        )
        result = await TestBrowsing._navigate(_request(LIMIT), graph, node_id="rec-in", view=view)

        assert result["context_block"] == block

    @staticmethod
    async def _update(limit: dict | None, graph: AsyncMock) -> dict:
        from app.connectors.sources.localKB.api.kb_router import update_record

        request = _request(limit, graph=graph)
        request.json = AsyncMock(return_value={"updates": {"recordName": "budget.xlsx"}})
        request.app.container.logger.return_value = MagicMock()
        service = MagicMock()
        service.update_record = AsyncMock(return_value={"success": True, "updatedRecord": _file_doc()})
        return await update_record("rec-in", request, kb_service=service, kafka_service=MagicMock())

    @pytest.mark.asyncio
    async def test_an_update_whose_limit_check_fails_names_nothing_above_the_record(self) -> None:
        from app.exceptions.graph_db_exceptions import (
            PermissionVerificationUnavailableError,
        )

        graph = _graph_under(FILE_LIMIT)
        graph._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "app-in", "kb_name": "Finance"})
        graph.get_user_kb_permission = AsyncMock(return_value="OWNER")
        graph.get_knowledge_base = AsyncMock(return_value={"id": "app-in", "name": "Finance", "folders": []})
        graph.get_knowledge_hub_parent_node = AsyncMock(return_value=None)
        graph.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("graph down"))

        response = await self._update(FILE_LIMIT, graph)

        record = response["updatedRecord"]
        assert response["kb"] == {} and record["recordName"] == "budget.xlsx"
        for field in ("parentNodeId", "externalParentId", "recordGroupId", "externalGroupId"):
            assert record[field] is None, field

    @pytest.mark.asyncio
    async def test_an_unlimited_update_whose_enrichment_fails_is_unchanged(self) -> None:
        graph = _graph()
        graph._get_kb_context_for_record = AsyncMock(side_effect=RuntimeError("graph down"))

        response = await self._update(None, graph)

        assert response["updatedRecord"] == _file_doc()
