"""A connected app limited to part of an app pages and counts only what its
limit admits, and finds what its limit names at the browse root.

The Knowledge Hub route runs the real ``KnowledgeHubService`` and the real
limit check; only the graph database is stubbed, with an autospec of the
production provider whose listing pages and access check behave as both
providers do (see ``kh_listing_stub``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes
from app.connectors.api.router import knowledge_graph_navigate
from app.connectors.sources.localKB.api.knowledge_hub_router import _handle_get_nodes
from app.connectors.sources.localKB.handlers.kh_bounded import (
    Position,
    decode_bounded_cursor,
    encode_bounded_cursor,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.modules.retrieval import selection_scope
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from tests.unit.agents.actions.kh_listing_stub import (
    connector_pages,
    node,
    stub_listing,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.connectors.sources.localKB.api.knowledge_hub_models import (
        KnowledgeHubNodesResponse,
    )

USER_KEY = "user-key-1"
FOLDER = "11111111-1111-4111-8111-111111111111"

# Sixty files the search matches sort before the three under the folder a limit names.
CORPUS = {
    "kb-big": [
        *(node(f"big-{i:02d}", f"budget {i:02d}", origin="COLLECTION") for i in range(60)),
        node(FOLDER, "shared folder", "folder", origin="COLLECTION"),
        *(node(f"zz-{c}", f"zz budget {c}", parent_id=FOLDER, origin="COLLECTION") for c in "abc"),
    ],
    "kb-other": [node("other-1", "budget other", origin="COLLECTION")],
}


@pytest.fixture(autouse=True)
def _fresh_selection_memo() -> Iterator[None]:
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()


def _graph() -> MagicMock:
    graph = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(graph, USER_KEY, CORPUS)
    graph.get_hidden_app_ids.return_value = []
    rows = {r["id"]: {**r, "connectorId": app} for app, items in CORPUS.items() for r in items}
    owner = {i: r["connectorId"] for i, r in rows.items()} | {app: app for app in CORPUS}
    children: dict[str, list[str]] = {}
    for r in rows.values():
        if r.get("parentId"):
            children.setdefault(r["parentId"], []).append(r["id"])

    def selection_nodes(_org_id: str, *, record_ids=(), exact_record_ids=(), **_: object) -> dict[str, Any]:
        found, todo = [], list(record_ids)
        while todo:
            node_id = todo.pop()
            found.append(node_id)
            todo.extend(children.get(node_id, []))
        found += list(exact_record_ids)
        return {"groups": [], "records": [
            {"id": i, "vrid": f"v-{i}", "connectorId": owner[i]} for i in sorted(set(found)) if i in owner
        ]}

    def check(_user_key: str, _org_id: str, node_ids=(), scopes=(), **_: object) -> AccessCheck:
        seen = frozenset(i for i in node_ids if i in owner)
        row = {i: {"id": i, "connectorId": owner[i], "groupIds": []} for i in seen}
        return AccessCheck(
            node_ids=seen,
            node_ids_in_scope=frozenset(i for i in seen if all(s.admits(row[i]) for s in scopes or ())),
        )

    def by_ids(collection: str, _field: str, ids: list[str], **_: object) -> list[dict[str, Any]]:
        if collection == "apps":
            return [{"id": i, "name": i} for i in ids if i in CORPUS]
        return [
            {"id": i, "recordName": rows[i]["name"], "connectorId": owner[i], "origin": "COLLECTION",
             "mimeType": MimeTypes.FOLDER.value if rows[i]["nodeType"] == "folder" else "text/plain"}
            for i in ids if i in rows
        ]

    graph.get_selection_nodes.side_effect = selection_nodes
    graph.check_access.side_effect = check
    graph.get_nodes_by_field_in.side_effect = by_ids
    graph.get_user_by_user_id.return_value = {"_key": USER_KEY, "email": "u@example.com"}
    return graph


def _service(graph: MagicMock) -> KnowledgeHubService:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"scopedJwtSecret": "s" * 32})
    return KnowledgeHubService(
        logger=MagicMock(), graph_provider=graph, config_service=config, excluded_app_ids=frozenset(),
    )


def _request(graph: MagicMock, limit: dict | None) -> MagicMock:
    user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    if limit is not None:
        user.update(isOAuth=True, knowledgeLimit=limit)
    request = MagicMock()
    request.state.user = user
    request.app.state.graph_provider = graph
    return request


async def _nodes(graph: MagicMock, knowledge_limit: dict | None, **overrides: object) -> KnowledgeHubNodesResponse:
    kwargs: dict[str, Any] = {
        "parent_id": None, "parent_type": None, "only_containers": False, "page": 1, "limit": 50,
        "sort_by": "name", "sort_order": "asc", "q": None, "node_types": None, "record_types": None,
        "origins": None, "connector_ids": None, "indexing_status": None, "created_at": None,
        "updated_at": None, "size": None, "flattened": None, "include": None, "cursor": None,
    }
    kwargs.update(overrides)
    return await _handle_get_nodes(
        request=_request(graph, knowledge_limit), knowledge_hub_service=_service(graph), **kwargs,
    )


FOLDER_LIMIT = {"apps": [], "recordGroups": [], "records": [FOLDER]}
FILES_LIMIT = {"apps": [], "recordGroups": [], "records": ["zz-a", "big-07"]}
APP_LIMIT = {"apps": ["kb-big"], "recordGroups": [], "records": []}
MIXED_LIMIT = {"apps": ["kb-other"], "recordGroups": [], "records": [FOLDER]}


class TestASearchInsideAFolderLimit:
    async def test_a_match_past_the_first_service_page_fills_the_page(self) -> None:
        graph = _graph()

        result = await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2)

        assert [item.id for item in result.items] == ["zz-a", "zz-b"]
        assert result.pagination.hasNext is True and result.pagination.nextCursor
        assert result.pagination.startIndex == 1 and result.pagination.endIndex == 2
        assert len(connector_pages(graph)) >= 2

    async def test_the_next_page_holds_the_rest_and_the_total_is_then_final(self) -> None:
        graph = _graph()
        first = await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2)

        second = await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2, cursor=first.pagination.nextCursor)

        assert [item.id for item in second.items] == ["zz-c"]
        assert second.pagination.hasNext is False and second.pagination.nextCursor is None
        assert second.pagination.totalItems == 3
        assert second.pagination.startIndex == 3 and second.pagination.hasPrev is True

    async def test_a_page_number_reaches_the_same_page_as_the_cursor(self) -> None:
        second = await _nodes(_graph(), FOLDER_LIMIT, q="budget", limit=2, page=2)

        assert [item.id for item in second.items] == ["zz-c"]
        assert second.pagination.totalItems == 3

    async def test_counts_cover_only_what_the_limit_admits(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, q="budget", include="counts")

        assert result.pagination.totalItems == 3 and result.pagination.hasNext is False
        assert result.counts.total == 3

    async def test_a_mixed_limit_searches_its_apps_and_its_folders(self) -> None:
        result = await _nodes(_graph(), MIXED_LIMIT, q="budget")

        assert [item.id for item in result.items] == ["other-1", "zz-a", "zz-b", "zz-c"]
        assert result.pagination.totalItems == 4 and result.pagination.hasNext is False

    async def test_a_cursor_the_service_issued_elsewhere_is_refused(self) -> None:
        graph = _graph()
        unlimited = await _nodes(graph, None, q="budget", limit=2)

        with pytest.raises(HTTPException) as exc:
            await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2, cursor=unlimited.pagination.nextCursor)
        assert exc.value.status_code == 400


class TestTheBrowseRootOfALimitBelowAppLevel:
    async def test_a_folder_limit_lists_its_folder(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT)

        assert [item.id for item in result.items] == [FOLDER]
        assert result.items[0].nodeType == "folder" and result.items[0].hasChildren is True
        assert result.pagination.totalItems == 1 and result.pagination.hasNext is False

    async def test_a_files_limit_lists_its_files_and_pages_them(self) -> None:
        graph = _graph()
        first = await _nodes(graph, FILES_LIMIT, limit=1)
        second = await _nodes(graph, FILES_LIMIT, limit=1, cursor=first.pagination.nextCursor)

        assert [item.id for item in first.items] == ["big-07"]
        assert [item.id for item in second.items] == ["zz-a"]
        assert first.pagination.totalItems == second.pagination.totalItems == 2
        assert second.pagination.hasNext is False

    async def test_a_mixed_limit_lists_its_apps_beside_its_folders(self) -> None:
        result = await _nodes(_graph(), MIXED_LIMIT)

        assert [item.id for item in result.items] == ["kb-other", FOLDER]
        assert result.items[0].nodeType == "app" and result.pagination.totalItems == 2

    async def test_a_named_item_the_user_cannot_read_is_not_listed(self) -> None:
        limit = {"apps": [], "recordGroups": [], "records": [FOLDER, "gone"]}

        result = await _nodes(_graph(), limit)

        assert [item.id for item in result.items] == [FOLDER]


class TestWholeAppsAndNoLimit:
    async def test_an_unlimited_token_lists_every_source(self) -> None:
        graph = _graph()

        result = await _nodes(graph, None)
        direct = await _service(graph).get_nodes(user_id="user-1", org_id="org-1", sort_by="name", sort_order="asc")

        assert [item.id for item in result.items] == [item.id for item in direct.items] == ["kb-big", "kb-other"]
        assert result.pagination.totalItems == 2

    async def test_a_whole_app_limit_lists_that_app_and_counts_only_it(self) -> None:
        result = await _nodes(_graph(), APP_LIMIT)

        assert [item.id for item in result.items] == ["kb-big"]
        assert result.pagination.totalItems == 1

    async def test_a_whole_app_limit_searches_inside_the_query(self) -> None:
        graph = _graph()

        result = await _nodes(graph, APP_LIMIT, q="budget", limit=100)

        assert "other-1" not in {item.id for item in result.items}
        assert result.pagination.totalItems == 63
        assert {page["app_id"] for page in connector_pages(graph)} == {"kb-big"}

    async def test_a_whole_app_limit_refuses_a_cursor_issued_without_it(self) -> None:
        graph = _graph()
        unlimited = await _nodes(graph, None, q="budget", limit=2)

        with pytest.raises(HTTPException) as exc:
            await _nodes(graph, APP_LIMIT, q="budget", limit=2, cursor=unlimited.pagination.nextCursor)
        assert exc.value.status_code == 400

    async def test_a_whole_app_limit_pages_on_by_its_own_cursor(self) -> None:
        graph = _graph()
        first = await _nodes(graph, APP_LIMIT, q="budget", limit=50)

        second = await _nodes(graph, APP_LIMIT, q="budget", limit=50, cursor=first.pagination.nextCursor)

        assert len(second.items) == 13 and second.pagination.totalItems == 63


class TestTheNavigateRoot:
    @staticmethod
    async def _navigate(graph: MagicMock, limit: dict | None) -> dict:
        catalog = MagicMock(connector_ids=MagicMock(return_value=list(CORPUS)), connectors=[])
        with patch("app.connectors.api.router.ConnectorCatalog.build", AsyncMock(return_value=catalog)), \
             patch("app.connectors.api.router.GraphNavigator") as navigator:
            navigator.return_value.navigate = AsyncMock(side_effect=AssertionError("the navigator lists apps"))
            return await knowledge_graph_navigate(
                request=_request(graph, limit), graph_provider=graph, node_id=None, page=1, limit=50,
                depth=1, node_types=None, created_after=None, created_before=None,
                modified_after=None, modified_before=None,
            )

    async def test_a_folder_limit_lists_its_folder(self) -> None:
        result = await self._navigate(_graph(), FOLDER_LIMIT)

        assert [row["id"] for row in result["rows"]] == [FOLDER]
        assert result["pagination"]["total"] == 1
        assert "shared folder" in result["text"] and "big-00" not in result["text"]

    async def test_a_mixed_limit_lists_its_apps_beside_its_folders(self) -> None:
        result = await self._navigate(_graph(), MIXED_LIMIT)

        assert [row["id"] for row in result["rows"]] == ["kb-other", FOLDER]

    async def test_a_files_limit_lists_its_files(self) -> None:
        result = await self._navigate(_graph(), FILES_LIMIT)

        assert [row["id"] for row in result["rows"]] == ["zz-a", "big-07"]
        assert all(row["is_record"] for row in result["rows"])


class TestTheBoundedCursor:
    def test_it_round_trips(self) -> None:
        token = encode_bounded_cursor("search", 4, Position(cursor="abc", page=2, skip=3))
        assert decode_bounded_cursor(token) == ("search", 4, Position(cursor="abc", page=2, skip=3))

    @pytest.mark.parametrize("token", ["abc", "lim1.!!!", "lim1.e30", encode_bounded_cursor("other", 1)])
    def test_anything_else_is_refused(self, token: str) -> None:
        with pytest.raises(ValueError):
            decode_bounded_cursor(token)
