"""A connected app limited to part of an app pages and counts only what its
limit admits, and finds what its limit names at the browse root.

The Knowledge Hub route runs the real ``KnowledgeHubService`` and the real
limit check; only the graph database is stubbed, with an autospec of the
production provider whose listing pages and access check behave as both
providers do (see ``kh_listing_stub``).
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import MimeTypes
from app.connectors.api.router import knowledge_graph_navigate
from app.connectors.sources.localKB.api import knowledge_hub_router
from app.connectors.sources.localKB.api.knowledge_hub_router import _handle_get_nodes
from app.connectors.sources.localKB.handlers.kh_bounded import (
    BoundedCursor,
    Position,
    bound_key,
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
        *({**node(f"zz-{c}", f"zz budget {c}", parent_id=FOLDER, origin="COLLECTION"),
           "parentType": "folder", "parentName": "shared folder"} for c in "abc"),
    ],
    "kb-other": [node("other-1", "budget other", origin="COLLECTION")],
}


# Upload order is the reverse of the files' own modification order.
APP_DATES = {"createdAtTimestamp": 1_700_000_000_000, "updatedAtTimestamp": 1_700_000_000_000}
DATES = {
    "zz-a": {"createdAtTimestamp": 2_000, "updatedAtTimestamp": 2_000,
             "sourceCreatedAtTimestamp": 100, "sourceLastModifiedTimestamp": 100},
    "big-07": {"createdAtTimestamp": 1_000, "updatedAtTimestamp": 1_000,
               "sourceCreatedAtTimestamp": 900, "sourceLastModifiedTimestamp": 900},
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

    def by_ids(
        collection: str, _field: str, ids: list[str], return_fields: list[str] | None = None, **_: object,
    ) -> list[dict[str, Any]]:
        if collection == "apps":
            # Only the fields asked for, as both stores project them.
            docs = [{"id": i, "name": i, "type": "KB", **APP_DATES} for i in ids if i in CORPUS]
            return [{k: v for k, v in d.items() if return_fields is None or k in return_fields} for d in docs]
        # As a collection stores them: origin UPLOAD, and the file's own dates
        # beside when it was added.
        return [
            {"id": i, "recordName": rows[i]["name"], "connectorId": owner[i], "connectorName": "KB",
             "origin": "UPLOAD", **DATES.get(i, {}),
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

    async def test_the_cursor_alone_resumes_the_same_search(self) -> None:
        graph = _graph()
        first = await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2)

        second = await _nodes(graph, FOLDER_LIMIT, limit=2, cursor=first.pagination.nextCursor)

        assert [item.id for item in second.items] == ["zz-c"]
        assert second.pagination.totalItems == 3

    async def test_the_cursor_decides_the_search_over_what_the_request_asks(self) -> None:
        graph = _graph()
        first = await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2)

        second = await _nodes(graph, FOLDER_LIMIT, q="shared", limit=2, cursor=first.pagination.nextCursor)

        assert [item.id for item in second.items] == ["zz-c"]

    async def test_a_page_number_reaches_the_same_page_as_the_cursor(self) -> None:
        second = await _nodes(_graph(), FOLDER_LIMIT, q="budget", limit=2, page=2)

        assert [item.id for item in second.items] == ["zz-c"]
        assert second.pagination.totalItems == 3

    async def test_a_deep_page_number_is_refused_without_reading_any_page(self) -> None:
        """Each page before it would be read first: one request could make dozens of listing reads."""
        graph = _graph()

        with pytest.raises(HTTPException) as exc:
            await _nodes(graph, FOLDER_LIMIT, q="budget", limit=2, page=6)

        assert exc.value.status_code == 400 and "cursor" in exc.value.detail
        assert not connector_pages(graph)

    async def test_a_page_number_within_the_cap_is_still_walked(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, q="budget", limit=1, page=3)

        assert [item.id for item in result.items] == ["zz-c"]

    async def test_counts_cover_only_what_the_limit_admits(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, q="budget", include="counts")

        assert result.pagination.totalItems == 3 and result.pagination.hasNext is False
        assert result.counts.total == 3

    async def test_a_mixed_limit_searches_its_apps_and_its_folders(self) -> None:
        result = await _nodes(_graph(), MIXED_LIMIT, q="budget")

        assert [item.id for item in result.items] == ["other-1", "zz-a", "zz-b", "zz-c"]
        assert result.pagination.totalItems == 4 and result.pagination.hasNext is False

    @pytest.mark.parametrize(("q", "issued_under"), [("budget", "folder"), (None, "files")])
    async def test_a_cursor_read_under_another_limit_is_refused(self, q, issued_under) -> None:
        """Its offset counts rows of the first limit's listing, so resuming it
        under the second would skip or repeat rows and miscount the total."""
        graph = _graph()
        limit = {"folder": FOLDER_LIMIT, "files": FILES_LIMIT}[issued_under]
        first = await _nodes(graph, limit, q=q, limit=1)
        assert first.pagination.nextCursor

        with pytest.raises(HTTPException) as exc:
            await _nodes(graph, MIXED_LIMIT, q=q, limit=1, cursor=first.pagination.nextCursor)
        assert exc.value.status_code == 400
        resumed = await _nodes(graph, limit, q=q, limit=1, cursor=first.pagination.nextCursor)
        assert resumed.success

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

    async def test_the_cursor_alone_keeps_the_sort(self) -> None:
        graph = _graph()
        first = await _nodes(graph, FILES_LIMIT, limit=1, sort_order="desc")

        second = await _nodes(graph, FILES_LIMIT, limit=1, cursor=first.pagination.nextCursor)

        assert [item.id for item in first.items] == ["zz-a"]
        assert [item.id for item in second.items] == ["big-07"]

    async def test_a_collection_folder_is_listed_as_collection_content(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, origins="COLLECTION", flattened=False)

        assert [item.id for item in result.items] == [FOLDER]
        assert result.items[0].origin == "COLLECTION"
        # The collections page gates edit and delete on the caller's role in the collection.
        assert result.items[0].permission is not None and result.items[0].permission.role == "READER"

    async def test_a_collection_file_is_dated_by_its_upload(self) -> None:
        graph = _graph()

        result = await _nodes(graph, FILES_LIMIT, sort_by="updatedAt", sort_order="asc")
        since = await _nodes(graph, FILES_LIMIT, updated_at="gte:1500", flattened=False)

        assert [item.id for item in result.items] == ["big-07", "zz-a"]
        assert [item.updatedAt for item in result.items] == [1_000, 2_000]
        assert [item.id for item in since.items] == ["zz-a"]

    async def test_a_named_app_past_the_first_page_of_apps_is_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(knowledge_hub_router, "_APP_BATCH", 1)

        result = await _nodes(_graph(), MIXED_LIMIT)

        assert [item.id for item in result.items] == ["kb-other", FOLDER]

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

    async def test_a_smaller_whole_app_limits_cursor_is_refused_by_a_larger_one(self) -> None:
        """Its Apps are a subset of the larger limit's, but its total counted only them."""
        graph = _graph()
        smaller = await _nodes(graph, APP_LIMIT, q="budget", limit=2)
        larger = {"apps": ["kb-big", "kb-other"], "recordGroups": [], "records": []}

        with pytest.raises(HTTPException) as exc:
            await _nodes(graph, larger, q="budget", limit=2, cursor=smaller.pagination.nextCursor)
        assert exc.value.status_code == 400

    async def test_a_one_shot_iterable_of_apps_still_ties_the_cursor_to_its_limit(self) -> None:
        graph = _graph()
        service = _service(graph)
        query = {"user_id": "user-1", "org_id": "org-1", "q": "budget", "limit": 2,
                 "sort_by": "name", "sort_order": "asc"}
        smaller = await service.get_nodes(**query, within_app_ids=iter(["kb-big"]))

        larger = await service.get_nodes(
            **{**query, "cursor": smaller.pagination.nextCursor}, within_app_ids=iter(["kb-big", "kb-other"]),
        )

        assert smaller.success and not larger.success and larger.errorCode == 400

    async def test_a_cursor_filtered_to_one_app_resumes_under_its_own_limit(self) -> None:
        """The same limit's cursor still resumes when a connector filter narrowed it."""
        graph = _graph()
        larger = {"apps": ["kb-big", "kb-other"], "recordGroups": [], "records": []}
        first = await _nodes(graph, larger, q="budget", limit=2, connector_ids="kb-big")

        second = await _nodes(graph, larger, limit=2, cursor=first.pagination.nextCursor)

        assert second.success and second.pagination.totalItems == first.pagination.totalItems

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

    async def test_a_date_filter_keeps_a_named_app_dated_inside_it(self) -> None:
        catalog = MagicMock(connector_ids=MagicMock(return_value=list(CORPUS)), connectors=[])
        with patch("app.connectors.api.router.ConnectorCatalog.build", AsyncMock(return_value=catalog)):
            kept = await knowledge_graph_navigate(
                request=_request(_graph(), MIXED_LIMIT), graph_provider=_graph(), node_id=None, page=1, limit=50,
                depth=1, node_types=None, created_after="2020-01-01T00:00:00Z", created_before=None,
                modified_after=None, modified_before=None,
            )
            dropped = await knowledge_graph_navigate(
                request=_request(_graph(), MIXED_LIMIT), graph_provider=_graph(), node_id=None, page=1, limit=50,
                depth=1, node_types=None, created_after="2024-01-01T00:00:00Z", created_before=None,
                modified_after=None, modified_before=None,
            )

        assert "kb-other" in [row["id"] for row in kept["rows"]]
        assert "kb-other" not in [row["id"] for row in dropped["rows"]]

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
        assert [row["source_modified_at"] for row in result["rows"]] == [2_000, 1_000]


def _raw_cursor(payload: dict[str, Any]) -> str:
    return "lim1." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


class TestTheParentOfARow:
    """A file the limit names can sit in a folder the limit leaves out; the row
    is kept and that folder is not named."""

    async def test_a_named_file_in_a_folder_outside_the_limit_has_no_parent(self) -> None:
        result = await _nodes(_graph(), FILES_LIMIT, q="zz budget")

        (row,) = result.items
        assert row.id == "zz-a"
        assert row.parent is None and row.parentId is None
        assert "shared folder" not in result.model_dump_json() and FOLDER not in result.model_dump_json()

    async def test_a_named_file_under_its_app_does_not_name_the_app(self) -> None:
        result = await _nodes(_graph(), FILES_LIMIT, q="budget 07")

        assert [(item.id, item.parentId) for item in result.items] == [("big-07", None)]

    async def test_a_file_in_a_folder_inside_the_limit_keeps_its_parent(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, q="zz budget")

        assert {item.id: item.parent.id for item in result.items} == {f"zz-{c}": FOLDER for c in "abc"}
        assert all(item.parentId == FOLDER and item.parent.name == "shared folder" for item in result.items)

    async def test_a_browse_inside_a_named_folder_keeps_the_folder_as_parent(self) -> None:
        result = await _nodes(_graph(), FOLDER_LIMIT, parent_id=FOLDER, parent_type="folder")

        assert [item.id for item in result.items] == ["zz-a", "zz-b", "zz-c"]
        assert all(item.parentId == FOLDER for item in result.items)

    @staticmethod
    def _with_the_path_above(graph: MagicMock) -> MagicMock:
        """The browse scope names the folder's own parent and trail, as the providers do."""
        page = graph.get_knowledge_hub_connector_page_v3.side_effect

        def with_path(**kwargs: object) -> dict[str, Any]:
            result = page(**kwargs)
            if (result.get("scope") or {}).get("nodeId") == FOLDER:
                app = {"id": "kb-big", "name": "kb-big", "nodeType": "app"}
                result["scope"] = {**result["scope"], "parentNode": app,
                                   "breadcrumbs": [app, result["scope"]["currentNode"]]}
            return result

        graph.get_knowledge_hub_connector_page_v3.side_effect = with_path
        return graph

    async def test_a_browse_inside_a_named_folder_does_not_name_what_is_above_it(self) -> None:
        graph = self._with_the_path_above(_graph())

        result = await _nodes(graph, FOLDER_LIMIT, parent_id=FOLDER, parent_type="folder", include="breadcrumbs")

        assert result.parentNode is None
        assert [crumb.id for crumb in result.breadcrumbs] == [FOLDER]

    async def test_an_unlimited_browse_keeps_the_path_above(self) -> None:
        graph = self._with_the_path_above(_graph())

        result = await _nodes(graph, None, parent_id=FOLDER, parent_type="folder", include="breadcrumbs")

        assert result.parentNode.id == "kb-big"
        assert [crumb.id for crumb in result.breadcrumbs] == ["kb-big", FOLDER]

    async def test_an_unlimited_token_keeps_every_parent(self) -> None:
        graph = _graph()

        result = await _nodes(graph, None, q="zz budget")

        assert {item.id: (item.parentId, item.parent.id) for item in result.items} == {
            f"zz-{c}": (FOLDER, FOLDER) for c in "abc"
        }
        graph.check_access.assert_not_called()

    async def test_a_whole_app_limit_keeps_every_parent(self) -> None:
        result = await _nodes(_graph(), APP_LIMIT, q="budget", limit=100)

        assert all(item.parentId for item in result.items)
        assert {item.parent.id for item in result.items if item.parent} == {FOLDER}


class TestTheFilterMenu:
    """``filters.available.connectors`` names an app or collection by its own
    name, so a limited connection is offered only those its limit covers whole."""

    @staticmethod
    async def _offered(limit: dict | None, q: str | None) -> list[str]:
        result = await _nodes(_graph(), limit, q=q, include="availableFilters")
        return [option.id for option in result.filters.available.connectors]

    @pytest.mark.parametrize("q", [None, "budget"])
    async def test_a_folder_limit_is_offered_no_partly_shared_collection(self, q) -> None:
        assert await self._offered(FOLDER_LIMIT, q) == []

    @pytest.mark.parametrize("q", [None, "budget"])
    async def test_a_mixed_limit_is_offered_only_its_whole_collection(self, q) -> None:
        assert await self._offered(MIXED_LIMIT, q) == ["kb-other"]

    @pytest.mark.parametrize("q", [None, "budget"])
    async def test_a_whole_app_limit_is_offered_its_apps(self, q) -> None:
        assert await self._offered(APP_LIMIT, q) == ["kb-big"]

    @pytest.mark.parametrize("q", [None, "budget"])
    async def test_an_unlimited_token_is_offered_every_source(self, q) -> None:
        assert await self._offered(None, q) == ["kb-big", "kb-other"]

    @staticmethod
    async def _offered_below(limit: dict | None, parent_id: str, parent_type: str) -> list[str]:
        result = await _nodes(_graph(), limit, parent_id=parent_id, parent_type=parent_type, include="availableFilters")
        return [option.id for option in result.filters.available.connectors]

    async def test_a_folder_opened_under_a_folder_limit_offers_no_collection(self) -> None:
        assert await self._offered_below(FOLDER_LIMIT, FOLDER, "folder") == []

    async def test_a_folder_opened_under_a_whole_app_limit_offers_only_that_app(self) -> None:
        assert await self._offered_below(APP_LIMIT, FOLDER, "folder") == ["kb-big"]

    async def test_a_folder_opened_without_a_limit_offers_every_source(self) -> None:
        assert await self._offered_below(None, FOLDER, "folder") == ["kb-big", "kb-other"]


class TestTheBoundedCursor:
    def test_it_round_trips(self) -> None:
        params = {"q": "budget", "sort_by": "name", "only_containers": False, "flattened": None}
        token = encode_bounded_cursor("search", 4, Position(cursor="abc", page=2, skip=3), params)
        assert decode_bounded_cursor(token) == BoundedCursor(
            "search", 4, Position(cursor="abc", page=2, skip=3), {"q": "budget", "sort_by": "name", "only_containers": False},
        )

    def test_it_carries_the_limit_it_was_read_under(self) -> None:
        folder = bound_key({"apps": [], "recordGroups": [], "records": ["f", "g"]})

        assert folder == bound_key({"records": ["g", "f"], "recordGroups": [], "apps": []})
        assert folder != bound_key({"apps": [], "recordGroups": [], "records": ["f"]})
        assert decode_bounded_cursor(encode_bounded_cursor("browse", 1, bound=folder)).bound == folder

    @pytest.mark.parametrize("token", [
        "abc", "lim1.!!!", "lim1.e30", encode_bounded_cursor("other", 1),
        _raw_cursor({"m": "search", "n": 0, "f": {"q": 5}}),
        _raw_cursor({"m": "search", "n": 0, "f": {"limit": "9"}}),
        _raw_cursor({"m": "search", "n": 0, "f": {"flattened": "yes"}}),
    ])
    def test_anything_else_is_refused(self, token: str) -> None:
        with pytest.raises(ValueError):
            decode_bounded_cursor(token)

    async def test_what_it_carries_is_validated_as_a_request_is(self) -> None:
        token = _raw_cursor({"m": "browse", "n": 0, "f": {"sort_by": "secret"}})

        with pytest.raises(HTTPException) as exc:
            await _nodes(_graph(), FOLDER_LIMIT, cursor=token)
        assert exc.value.status_code == 400
