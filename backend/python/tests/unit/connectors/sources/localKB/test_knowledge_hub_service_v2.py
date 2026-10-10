"""`KnowledgeHubService.get_nodes` on the v2 read path.

The v2 queries decide permission, placement and breadcrumbs themselves, so the
service's job is the three things these tests pin: pick the right query for
the request, turn a cursor into the next page (and refuse one that is not the
caller's), and build the response — including the 404 that must not say what it
is hiding.

The provider is a mock here on purpose: the queries themselves are proven
against real Neo4j and Arango in `tests/integration/graph_permissions`, and
repeating that here would test the fixture, not the wiring.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.connectors.sources.localKB.handlers.kh_search import SearchPage
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    _MAX_FILTER_SOURCES,
    _MAX_PAGE_WALK,
    KnowledgeHubService,
)
from app.utils.kh_cursor import (
    Boundary,
    KnowledgeHubCursor,
    derive_cursor_secret,
    encode,
)


@pytest.fixture
def logger():
    log = logging.getLogger("test_kh_service_v2")
    log.setLevel(logging.CRITICAL)
    return log


@pytest.fixture
def config_service():
    config = AsyncMock()
    config.get_config.return_value = {"scopedJwtSecret": "test-secret"}
    return config


@pytest.fixture
def provider():
    graph = AsyncMock()
    graph.get_user_by_user_id.return_value = {"_key": "uk1"}
    graph.get_knowledge_hub_access_context_v2.return_value = {
        "grantee_ids": ["uk1", "group-g"],
        "gated_app_ids": ["app1", "kb-1"],
    }
    graph.get_knowledge_hub_access_v3.return_value = {
        "grantee_ids": ["uk1", "group-g"],
        "gated_app_ids": ["app1", "kb-1"],
        "by_connector": {"app1": [], "kb-1": []},
    }
    return graph


@pytest.fixture
def service(logger, provider, config_service):
    return KnowledgeHubService(
        logger=logger, graph_provider=provider, config_service=config_service
    )


def row(row_id: str, name: str, *, node_type: str = "record", parent: str | None = None,
        parent_type: str | None = None) -> dict:
    return {
        "id": row_id, "name": name, "nodeType": node_type,
        "sortKey": name.lower(), "nullRank": 0,
        "parentId": parent, "parentType": parent_type, "parentName": "Parent",
        "origin": "COLLECTION", "createdAt": 1, "updatedAt": 2, "hasChildren": False,
    }


def envelope(rows: list[dict], *, total: int | None = None, has_more: bool = False,
             ids: list[dict] | None = None, scope: dict | None = None) -> dict:
    return {
        "partitions": [{
            "partitionId": "p1", "partitionKind": "BROWSE", "appId": None,
            "rows": rows, "hasMore": has_more, "exhausted": not has_more,
            "total": len(rows) if total is None else total,
            "ids": ids or [], "countsByType": None,
        }],
        "scope": scope,
    }


def page_of(rows: list[dict], *, total: int | None = None, has_more: bool = False,
            scope: dict | None = None, counts: dict | None = None) -> dict:
    return {
        "rows": rows,
        "hasMore": has_more,
        "total": len(rows) if total is None else total,
        "counts": counts,
        "scope": scope,
    }


def admitted_scope(node_id: str = "p1", node_type: str = "recordGroup") -> dict:
    return {
        "admitted": True,
        "nodeId": node_id,
        "currentNode": {"id": node_id, "name": "Current", "nodeType": node_type},
        "parentNode": {"id": "app1", "name": "App One", "nodeType": "app"},
        "breadcrumbs": [
            {"id": "app1", "name": "App One", "nodeType": "app"},
            {"id": node_id, "name": "Current", "nodeType": node_type},
        ],
    }


# --------------------------------------------------------------- routing

@pytest.mark.asyncio
async def test_a_root_listing_uses_the_root_query(service, provider) -> None:
    """The request mode picks the v2 query, with no runtime switch."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")]
    )
    result = await service.get_nodes(user_id="u1", org_id="o1")

    assert result.success is True
    assert [item.id for item in result.items] == ["app1"]
    # The gate is the access context's.
    assert provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs["user_app_ids"] == [
        "app1", "kb-1",
    ]


@pytest.mark.asyncio
async def test_browsing_a_node_uses_the_children_query_unflattened(service, provider) -> None:
    """Browse routes to the children query, and asks it not to flatten."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], scope=admitted_scope()
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup"
    )

    assert result.success is True
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs["start_id"] == "p1" and kwargs["flatten"] is False
    assert kwargs["include_scope"] is True
    assert kwargs["grantee_ids"] == ["uk1", "group-g"]


@pytest.mark.asyncio
async def test_a_filtered_scoped_request_flattens(service, provider) -> None:
    """A filter means "search below here", which is the same query with depth."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope=admitted_scope()
    )
    await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup", q="report"
    )
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs["flatten"] is True
    assert kwargs["filters"]["search_query"] == "report"


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="depth is accepted by get_nodes and never read; "
           "the connector page query has no depth parameter to receive it",
)
async def test_depth_bounds_a_flattened_request(service, provider) -> None:
    """The agent navigator asks for a bounded flatten.

    `navigator.py` clamps depth to its own maximum, passes it, and then computes
    each row's nesting level from it — so it believes the bound was applied. v2
    drops it and flattens the whole subtree up to `_KH_V2_MAX_DEPTH` (50), so an
    agent asking for two levels can receive fifty. No error is raised anywhere,
    which is why nothing caught it.

    Marked xfail rather than fixed: the remedy is a product decision — bound the
    traversal in the provider, clamp by level in the service after fetching, or
    retire the parameter and update the navigator.
    """
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], scope=admitted_scope()
    )
    await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup",
        flattened=True, depth=2,
    )
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs.get("depth") == 2, sorted(kwargs)


@pytest.mark.asyncio
async def test_the_user_override_point_still_decides_the_user(
    logger, provider, config_service
) -> None:
    """`edition_config.knowledge_hub_service_factory` swaps the whole
    service (`connectors_main.py:887`), and an enterprise build overrides
    `_resolve_user` for an org-scoped lookup. If the base stopped calling it,
    that deployment would silently resolve users the community way.

    The assertion reads the call rather than its keyword names, so it pins that
    the override was consulted without pinning an argument spelling.
    """
    class _EEService(KnowledgeHubService):
        async def _resolve_user(self, user_id: str, org_id: str):
            return {"_key": "ee-user-key"}

    service = _EEService(
        logger=logger, graph_provider=provider, config_service=config_service
    )
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1")

    call = provider.get_knowledge_hub_access_context_v2.call_args
    assert "ee-user-key" in list(call.args) + list(call.kwargs.values()), call


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="_get_user_app_ids is defined as an override point but has no "
           "caller in the service package; v2 gates on the access context instead",
)
async def test_the_app_gate_override_point_is_still_consulted(
    logger, provider, config_service
) -> None:
    """An enterprise build overriding only `_get_user_app_ids`
    should still change which apps the request may reach.

    It does not. The method survives the v2 rewrite, so the override compiles and
    looks effective, but nothing calls it — the gate comes from
    `get_knowledge_hub_access_context_v2`. An EE deployment relying on it would
    widen or narrow nothing, with no error to notice.
    """
    consulted: list[tuple[str, str]] = []

    class _EEService(KnowledgeHubService):
        async def _get_user_app_ids(self, user_key: str, org_id: str) -> list[str]:
            consulted.append((user_key, org_id))
            return ["ee-app"]

    service = _EEService(
        logger=logger, graph_provider=provider, config_service=config_service
    )
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1")

    assert consulted, "the override point was never consulted"


@pytest.mark.asyncio
async def test_a_global_search_is_partitioned(service, provider) -> None:
    """The one mode that fans out across partitions and merges."""
    page = SearchPage(rows=[row("r1", "Report")], total=1, counts_by_type={"record": 1},
                      start_index=1, end_index=1, next_cursor=None, prev_cursor=None)
    with patch(
        "app.connectors.sources.localKB.handlers.knowledge_hub_service.search_page",
        AsyncMock(return_value=page),
    ) as searched:
        result = await service.get_nodes(user_id="u1", org_id="o1", q="report")

    assert [item.id for item in result.items] == ["r1"]
    assert searched.call_args.kwargs["filters"]["search_query"] == "report"
    provider.get_knowledge_hub_root_nodes_v2.assert_not_called()


@pytest.mark.asyncio
async def test_a_global_search_reads_filter_options_only_when_asked(service, provider) -> None:
    """The options are a query of their own; a search that did not ask for
    them must not run it."""
    page = SearchPage(rows=[row("r1", "Report")], total=1, counts_by_type={"record": 1},
                      start_index=1, end_index=1, next_cursor=None, prev_cursor=None)
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")]
    )
    with patch(
        "app.connectors.sources.localKB.handlers.knowledge_hub_service.search_page",
        AsyncMock(return_value=page),
    ):
        plain = await service.get_nodes(user_id="u1", org_id="o1", q="report")
        provider.get_knowledge_hub_root_nodes_v2.assert_not_called()
        asked = await service.get_nodes(
            user_id="u1", org_id="o1", q="report", include=["availableFilters"]
        )

    assert plain.filters.available is None
    assert [option.id for option in asked.filters.available.connectors] == ["app1"]
    # The root listing ran for the options, never as the source of the page.
    assert provider.get_knowledge_hub_root_nodes_v2.await_count == 1
    assert provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs["limit"] == _MAX_FILTER_SOURCES


@pytest.mark.asyncio
async def test_sorting_by_size_reaches_the_root_listing(service, provider) -> None:
    """A root listing sorted by size must not fall back to name without telling
    anyone."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1", sort_by="size", sort_order="asc")
    kwargs = provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs
    assert (kwargs["sort_field"], kwargs["sort_dir"]) == ("sizeInBytes", "ASC")


@pytest.mark.asyncio
async def test_the_access_context_is_resolved_once_per_request(service, provider) -> None:
    """The listing, the filter options and a global search gate on one answer.

    Asking separately would let them disagree — filters offering a source the
    listing refuses to open.
    """
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1", include=["availableFilters"])
    assert provider.get_knowledge_hub_access_context_v2.await_count == 1


@pytest.mark.asyncio
async def test_available_filters_come_from_the_gate(service, provider) -> None:
    """An App the user cannot open cannot appear among the filters."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")]
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", include=["availableFilters"]
    )
    assert result.filters.available is not None
    # Both listing and filters ask for exactly the gated set.
    for call in provider.get_knowledge_hub_root_nodes_v2.call_args_list:
        assert call.kwargs["user_app_ids"] == ["app1", "kb-1"]
    provider.get_knowledge_hub_filter_options.assert_not_called()


# --------------------------------------------------------------- scope

@pytest.mark.asyncio
async def test_current_and_parent_nodes_come_from_the_listing_query(
    service, provider
) -> None:
    """The listing query returns them, so browsing costs no extra round trips."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope=admitted_scope()
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup"
    )

    assert result.currentNode.id == "p1"
    assert result.parentNode.id == "app1"
    provider.get_knowledge_hub_parent_node.assert_not_called()


@pytest.mark.asyncio
async def test_breadcrumbs_come_from_the_same_query(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope=admitted_scope()
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup",
        include=["breadcrumbs"],
    )
    assert [crumb.id for crumb in result.breadcrumbs] == ["app1", "p1"]
    provider.get_knowledge_hub_breadcrumbs.assert_not_called()


@pytest.mark.asyncio
async def test_an_inadmissible_node_is_a_404_that_names_nothing(service, provider) -> None:
    """The body must not confirm the node exists, or what it is."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope={"admitted": False, "nodeId": "secret-1"}
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="secret-1", parent_type="recordGroup"
    )

    assert result.success is False
    assert result.error == "Node not found"
    assert "secret-1" not in result.error
    assert result.currentNode is None and result.items == []


@pytest.mark.asyncio
async def test_the_wrong_type_in_the_url_is_still_a_400(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope=admitted_scope(node_type="app")
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup"
    )
    assert result.success is False
    assert "type mismatch" in result.error.lower()


@pytest.mark.asyncio
async def test_a_folder_browse_is_not_a_type_mismatch(service, provider) -> None:
    """A folder is a record in the graph; comparing the raw strings would
    reject every folder browse."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [], scope=admitted_scope(node_type="record")
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="folder"
    )
    assert result.success is True


# --------------------------------------------------------------- paging

@pytest.mark.asyncio
async def test_a_page_carries_cursors_and_indices(service, provider) -> None:
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=5, has_more=True
    )
    result = await service.get_nodes(user_id="u1", org_id="o1", limit=1)

    pagination = result.pagination
    assert pagination.nextCursor and pagination.prevCursor is None
    assert (pagination.startIndex, pagination.endIndex) == (1, 1)
    assert pagination.currentPageItems == 1
    assert pagination.hasNext is True and pagination.hasPrev is False
    assert pagination.totalItems == 5
    # Legacy fields stay until the frontend moves off them.
    assert pagination.page == 1


@pytest.mark.asyncio
async def test_the_next_cursor_resumes_after_the_last_row(service, provider) -> None:
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=5, has_more=True
    )
    first = await service.get_nodes(user_id="u1", org_id="o1", limit=1)

    await service.get_nodes(
        user_id="u1", org_id="o1", limit=1, cursor=first.pagination.nextCursor
    )
    kwargs = provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs
    assert kwargs["after"] == {"nullRank": 0, "sortKey": "app one", "id": "app1"}
    assert kwargs["direction"] == "next"


@pytest.mark.asyncio
async def test_the_cursor_decides_sort_and_filters(service, provider) -> None:
    """The cursor wins over conflicting parameters, `include` does not.

    Scoped, because a `q` with no parent is a *global* search and would fan out
    across partitions instead of reaching a single listing query.
    """
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], total=5, has_more=True, scope=admitted_scope()
    )
    first = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup",
        limit=1, sort_by="createdAt", sort_order="desc", q="alpha",
    )

    await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup",
        limit=1, cursor=first.pagination.nextCursor,
        sort_by="name", sort_order="asc", q="beta",
    )
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert (kwargs["sort_field"], kwargs["sort_dir"]) == ("createdAt", "DESC")
    assert kwargs["filters"]["search_query"] == "alpha"


@pytest.mark.asyncio
async def test_a_cursor_from_another_user_is_refused(service, provider, logger,
                                                     config_service) -> None:
    """It is a 400, never a quietly-served first page."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=5, has_more=True
    )
    first = await service.get_nodes(user_id="u1", org_id="o1", limit=1)

    other = KnowledgeHubService(
        logger=logger, graph_provider=provider, config_service=config_service
    )
    result = await other.get_nodes(
        user_id="u2", org_id="o1", limit=1, cursor=first.pagination.nextCursor
    )
    assert result.success is False
    assert "invalid cursor" in result.error.lower()


@pytest.mark.asyncio
async def test_deep_page_numbers_are_refused(service, provider) -> None:
    """A page number re-traverses, so an unbounded one is free server load."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    result = await service.get_nodes(user_id="u1", org_id="o1", page=500, limit=200)
    assert result.success is False
    assert "invalid page" in result.error.lower()


@pytest.mark.asyncio
async def test_the_page_walk_is_bounded_by_queries_not_items(service, provider) -> None:
    """Each page walked is a listing query, whatever the page size, so the
    bound counts pages, not rows."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=100_000, has_more=True
    )
    refused = await service.get_nodes(user_id="u1", org_id="o1", page=_MAX_PAGE_WALK + 1, limit=1)
    assert (refused.success, refused.errorCode) == (False, 400)
    assert "invalid page" in refused.error.lower()
    provider.get_knowledge_hub_root_nodes_v2.assert_not_called()

    allowed = await service.get_nodes(user_id="u1", org_id="o1", page=_MAX_PAGE_WALK, limit=1)
    assert allowed.success is True
    assert provider.get_knowledge_hub_root_nodes_v2.await_count == _MAX_PAGE_WALK


@pytest.mark.asyncio
async def test_a_page_past_the_end_is_empty(service, provider) -> None:
    """Not the last page it reached, served under the number asked for."""
    provider.get_knowledge_hub_root_nodes_v2.side_effect = [
        envelope([row("app1", "App One", node_type="app")], total=2, has_more=True),
        envelope([row("app2", "App Two", node_type="app")], total=2, has_more=False),
    ]
    result = await service.get_nodes(user_id="u1", org_id="o1", page=5, limit=1)

    assert result.success is True
    assert result.items == []
    pagination = result.pagination
    assert (pagination.page, pagination.totalItems, pagination.totalPages) == (5, 2, 2)
    assert pagination.hasNext is False
    assert pagination.currentPageItems == 0
    assert (pagination.startIndex, pagination.endIndex) == (0, 0)
    assert pagination.nextCursor is None and pagination.prevCursor is None
    assert provider.get_knowledge_hub_root_nodes_v2.await_count == 2


@pytest.mark.asyncio
async def test_a_cursor_issued_for_one_folder_is_refused_on_another(service, provider) -> None:
    """The boundary and the carried total belong to the folder that issued them."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], total=5, has_more=True, scope=admitted_scope("folder-a")
    )
    first = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="folder-a", parent_type="recordGroup", limit=1
    )

    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="folder-b", parent_type="recordGroup",
        limit=1, cursor=first.pagination.nextCursor,
    )
    assert (result.success, result.errorCode) == (False, 400)
    assert result.error == "Invalid cursor: issued for a different location"
    assert provider.get_knowledge_hub_connector_page_v3.await_count == 1


@pytest.mark.asyncio
async def test_a_browse_cursor_is_refused_on_a_flatten_of_the_same_folder(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], total=5, has_more=True, scope=admitted_scope()
    )
    browse = {"user_id": "u1", "org_id": "o1", "parent_id": "p1", "parent_type": "recordGroup", "limit": 1}
    first = await service.get_nodes(**browse)

    result = await service.get_nodes(**browse, flattened=True, cursor=first.pagination.nextCursor)
    assert (result.success, result.errorCode) == (False, 400)
    assert result.error == "Invalid cursor: issued for a different location"


@pytest.mark.asyncio
async def test_a_search_cursor_is_refused_on_a_listing(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Report")], total=5, has_more=True, scope=admitted_scope()
    )
    searched = await service.get_nodes(user_id="u1", org_id="o1", q="report", limit=1)
    assert searched.pagination.nextCursor
    asked = provider.get_knowledge_hub_connector_page_v3.await_count

    for listing in ({}, {"parent_id": "p1", "parent_type": "recordGroup", "flattened": True}):
        result = await service.get_nodes(
            user_id="u1", org_id="o1", limit=1, cursor=searched.pagination.nextCursor, **listing
        )
        assert (result.success, result.errorCode) == (False, 400), listing
        assert result.error == "Invalid cursor: issued for a different location"
    provider.get_knowledge_hub_root_nodes_v2.assert_not_called()
    assert provider.get_knowledge_hub_connector_page_v3.await_count == asked


@pytest.mark.asyncio
async def test_a_listing_cursor_is_refused_on_a_search(service, provider) -> None:
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=5, has_more=True
    )
    first = await service.get_nodes(user_id="u1", org_id="o1", limit=1)

    result = await service.get_nodes(
        user_id="u1", org_id="o1", q="report", limit=1, cursor=first.pagination.nextCursor
    )
    assert (result.success, result.errorCode) == (False, 400)
    assert result.error == "Invalid cursor: issued for a different location"
    provider.get_knowledge_hub_connector_page_v3.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_kwargs", [{}, {"q": "report"}])
async def test_a_cursor_that_names_no_mode_is_refused(service, provider, request_kwargs) -> None:
    """A cursor issued before the mode was carried cannot say where it is from."""
    token = encode(
        KnowledgeHubCursor(
            boundary=Boundary(null_rank=0, sort_key="app one", last_id="app1"),
            items_seen=1, total=5, user_id="u1", org_id="o1",
        ),
        derive_cursor_secret("test-secret"),
    )
    result = await service.get_nodes(user_id="u1", org_id="o1", limit=1, cursor=token, **request_kwargs)
    assert (result.success, result.errorCode) == (False, 400)
    assert result.error == "Invalid cursor: issued for a different location"
    provider.get_knowledge_hub_root_nodes_v2.assert_not_called()
    provider.get_knowledge_hub_connector_page_v3.assert_not_called()


@pytest.mark.asyncio
async def test_a_search_pages_forward_and_back_on_its_own_cursors(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Report")], total=5, has_more=True
    )
    search = {"user_id": "u1", "org_id": "o1", "q": "report", "limit": 1}
    first = await service.get_nodes(**search)
    second = await service.get_nodes(**search, cursor=first.pagination.nextCursor)

    assert second.success is True
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs["after"] == {"nullRank": 0, "sortKey": "report", "id": "r1"}
    assert kwargs["direction"] == "next"

    back = await service.get_nodes(**search, cursor=second.pagination.prevCursor)
    assert back.success is True
    assert provider.get_knowledge_hub_connector_page_v3.call_args.kwargs["direction"] == "prev"


@pytest.mark.asyncio
async def test_a_browse_pages_forward_and_back_on_its_own_cursors(service, provider) -> None:
    """The control for the location check, under both names a folder is browsed
    by: it is a record in the graph, and the listing starts from the same node."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record")], total=5, has_more=True, scope=admitted_scope("f1", "record")
    )
    browse = {"user_id": "u1", "org_id": "o1", "parent_id": "f1", "limit": 1}
    first = await service.get_nodes(**browse, parent_type="folder")
    second = await service.get_nodes(**browse, parent_type="record", cursor=first.pagination.nextCursor)

    assert second.success is True
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs["after"] == {"nullRank": 0, "sortKey": "record", "id": "r1"}
    assert kwargs["direction"] == "next"

    back = await service.get_nodes(**browse, parent_type="record", cursor=second.pagination.prevCursor)
    assert back.success is True
    assert provider.get_knowledge_hub_connector_page_v3.call_args.kwargs["direction"] == "prev"


@pytest.mark.asyncio
async def test_a_page_number_walks_forward(service, provider) -> None:
    """`page` survives transitionally by paging for the caller."""
    provider.get_knowledge_hub_root_nodes_v2.side_effect = [
        envelope([row("app1", "App One", node_type="app")], total=3, has_more=True),
        envelope([row("app2", "App Two", node_type="app")], total=3, has_more=True),
    ]
    result = await service.get_nodes(user_id="u1", org_id="o1", page=2, limit=1)

    assert [item.id for item in result.items] == ["app2"]
    assert provider.get_knowledge_hub_root_nodes_v2.await_count == 2


# --------------------------------------------------------------- counts

@pytest.mark.asyncio
async def test_counts_describe_the_whole_result(service, provider) -> None:
    """A breakdown of the current page beside a total of everything would
    disagree on every page but the last."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")],
        total=3, has_more=True,
        ids=[{"id": "app1", "nodeType": "app"},
             {"id": "r1", "nodeType": "record"},
             {"id": "f1", "nodeType": "folder"}],
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", limit=1, include=["counts"]
    )

    assert provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs["include_ids"] is True
    assert result.counts.total == 3
    assert {item.label: item.count for item in result.counts.items} == {
        "apps": 1, "records": 1, "folders": 1,
    }


@pytest.mark.asyncio
async def test_ids_are_not_fetched_when_counts_were_not_asked_for(
    service, provider
) -> None:
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1")
    assert provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs["include_ids"] is False


# --------------------------------------------------------------- rows

@pytest.mark.asyncio
async def test_rows_carry_the_parent_triple(service, provider) -> None:
    """A search hit renders "in <folder>" without a second lookup."""
    provider.get_knowledge_hub_connector_page_v3.return_value = page_of(
        [row("r1", "Record", parent="f1", parent_type="folder")],
        scope=admitted_scope(),
    )
    result = await service.get_nodes(
        user_id="u1", org_id="o1", parent_id="p1", parent_type="recordGroup"
    )
    item = result.items[0]
    assert item.parentId == "f1"
    assert (item.parent.id, item.parent.nodeType, item.parent.name) == (
        "f1", "folder", "Parent",
    )


@pytest.mark.asyncio
async def test_an_unknown_user_is_reported_without_querying(service, provider) -> None:
    provider.get_user_by_user_id.return_value = None
    result = await service.get_nodes(user_id="nobody", org_id="o1")
    assert result.success is False and result.error == "User not found"
    provider.get_knowledge_hub_root_nodes_v2.assert_not_called()


@pytest.mark.asyncio
async def test_without_a_signer_pages_work_but_hand_out_no_cursor(
    logger, provider
) -> None:
    """An unsigned cursor is an editable one, so none is handed out at all.

    The agent tools build this service without a config service and page by
    number; the HTTP router always passes one, so the API keeps its cursors.
    """
    unconfigured = KnowledgeHubService(logger=logger, graph_provider=provider)
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope(
        [row("app1", "App One", node_type="app")], total=5, has_more=True
    )
    result = await unconfigured.get_nodes(user_id="u1", org_id="o1", limit=1)

    assert result.success is True
    assert [item.id for item in result.items] == ["app1"]
    assert result.pagination.nextCursor is None
    assert result.pagination.prevCursor is None
    # A caller paging by number still has to learn there is a next page.
    assert result.pagination.hasNext is True


@pytest.mark.asyncio
async def test_a_cursor_is_refused_when_nothing_can_verify_it(
    logger, provider
) -> None:
    unconfigured = KnowledgeHubService(logger=logger, graph_provider=provider)
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    result = await unconfigured.get_nodes(user_id="u1", org_id="o1", cursor="anything")
    assert result.success is False
    assert "invalid cursor" in result.error.lower()


@pytest.mark.asyncio
async def test_page_numbers_still_walk_without_a_signer(logger, provider) -> None:
    """Walking uses cursors internally; they just never leave the process."""
    unconfigured = KnowledgeHubService(logger=logger, graph_provider=provider)
    provider.get_knowledge_hub_root_nodes_v2.side_effect = [
        envelope([row("app1", "App One", node_type="app")], total=3, has_more=True),
        envelope([row("app2", "App Two", node_type="app")], total=3, has_more=True),
    ]
    result = await unconfigured.get_nodes(user_id="u1", org_id="o1", page=2, limit=1)
    assert [item.id for item in result.items] == ["app2"]


@pytest.mark.asyncio
async def test_root_permissions_follow_the_callers_org_role(service, provider) -> None:
    """The graph User has no role, so the provider's root answer is MEMBER
    for every admin; the org role the router passes decides it instead."""
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    provider.get_knowledge_hub_context_permissions.return_value = {
        "role": "MEMBER", "canUpload": False, "canCreateFolders": False,
        "canEdit": False, "canDelete": False, "canManagePermissions": False,
    }
    result = await service.get_nodes(
        user_id="u1", org_id="o1", include=["permissions"], is_org_admin=True
    )
    assert result.permissions.role == "ADMIN"
    assert result.permissions.canManagePermissions is True


@pytest.mark.asyncio
async def test_collection_rows_carry_the_collection_role(service, provider) -> None:
    """Listing rows below an App have no role; collection content takes the
    caller's role on its collection (looked up once), connector content stays
    without one, and a role the query already set is kept."""
    rows = [
        {"id": "f1", "name": "F1", "nodeType": "folder", "origin": "COLLECTION", "connectorId": "kb-1"},
        {"id": "r1", "name": "R1", "nodeType": "record", "origin": "COLLECTION", "connectorId": "kb-1"},
        {"id": "r2", "name": "R2", "nodeType": "record", "origin": "COLLECTION", "connectorId": "kb-2",
         "userRole": "READER"},
        {"id": "g1", "name": "G1", "nodeType": "record", "origin": "CONNECTOR", "connectorId": "app1"},
    ]
    provider.get_user_kb_permission.return_value = "WRITER"
    await service._stamp_collection_roles(rows, "uk1")
    provider.get_user_kb_permission.assert_awaited_once_with("kb-1", "uk1")
    assert [r.get("userRole") for r in rows] == ["WRITER", "WRITER", "READER", None]


@pytest.mark.asyncio
async def test_collection_roles_are_looked_up_together(service, provider) -> None:
    """A page can name as many collections as it has rows, and one lookup after
    another made the listing wait for each in turn."""
    rows = [
        {"id": f"r{n}", "name": f"R{n}", "nodeType": "record", "origin": "COLLECTION", "connectorId": f"kb-{n}"}
        for n in range(3)
    ]
    running = most_at_once = 0

    async def role(kb_id: str, user_key: str) -> str:
        nonlocal running, most_at_once
        running += 1
        most_at_once = max(most_at_once, running)
        await asyncio.sleep(0)
        running -= 1
        return f"role-of-{kb_id}"

    provider.get_user_kb_permission.side_effect = role
    await service._stamp_collection_roles(rows, "uk1")

    assert provider.get_user_kb_permission.await_count == 3
    assert most_at_once == 3
    assert [r["userRole"] for r in rows] == ["role-of-kb-0", "role-of-kb-1", "role-of-kb-2"]


@pytest.mark.asyncio
async def test_a_failed_collection_role_lookup_still_fails_the_listing(service, provider) -> None:
    provider.get_user_kb_permission.side_effect = RuntimeError("graph down")
    rows = [{"id": "r1", "name": "R1", "nodeType": "record", "origin": "COLLECTION", "connectorId": "kb-1"}]
    with pytest.raises(RuntimeError):
        await service._stamp_collection_roles(rows, "uk1")


@pytest.mark.asyncio
async def test_switched_off_demo_apps_leave_the_gate(logger, provider, config_service) -> None:
    """The Acme demo a user switched off is out of the root listing and out of a
    scoped browse's gate, so a deep link into it answers like a missing node."""
    service = KnowledgeHubService(
        logger=logger, graph_provider=provider, config_service=config_service,
        excluded_app_ids=frozenset({"app1"}),
    )
    provider.get_knowledge_hub_root_nodes_v2.return_value = envelope([])
    await service.get_nodes(user_id="u1", org_id="o1")
    assert provider.get_knowledge_hub_root_nodes_v2.call_args.kwargs["user_app_ids"] == ["kb-1"]

    provider.get_knowledge_hub_connector_page_v3.return_value = page_of([], scope={"admitted": False})
    result = await service.get_nodes(user_id="u1", org_id="o1", parent_id="app1", parent_type="app")
    kwargs = provider.get_knowledge_hub_connector_page_v3.call_args.kwargs
    assert kwargs["gated_app_ids"] == ["kb-1"]
    # No grants are handed over: the page reads its connector's own, behind this gate.
    assert kwargs["granted_ids"] is None and kwargs.get("grants_by_connector") is None
    assert result.success is False and result.error == "Node not found"
