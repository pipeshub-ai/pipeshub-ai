"""Every way records reach a person respects their demo data switch.

Search goes through the retrieval service; browsing through the Knowledge Hub
service; and three agent tools open records by id, which no scope filter covers.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog
from app.agents.actions.knowledge_graph.models import LookupMatch
from app.agents.actions.knowledge_graph.navigator import GraphNavigator
from app.agents.actions.knowledge_graph.resolver import RecordResolver
from app.connectors.api import router as connector_router
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.modules.demo_data.chat import EXCLUDED_APP_IDS_STATE_KEY
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils.fetch_full_record import UNAVAILABLE, _RecordResolver

OFF = frozenset({"demo-1"})


# --- Search ------------------------------------------------------------------

def _retrieval(container_flag: bool) -> RetrievalService:
    service = object.__new__(RetrievalService)
    service.logger = MagicMock()
    service.config_service = MagicMock()
    service.graph_provider = MagicMock()
    service.graph_provider.get_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})
    service.graph_provider.get_accessible_containers = AsyncMock()
    service._get_user_cached = AsyncMock(return_value={"userId": "u1"})
    service._container_filter_enabled = AsyncMock(return_value=container_flag)
    return service


@pytest.mark.asyncio
async def test_search_leaves_the_demo_out_for_someone_who_switched_it_off() -> None:
    service = _retrieval(container_flag=False)
    with patch("app.modules.retrieval.retrieval_service.excluded_demo_connector_ids", AsyncMock(return_value=OFF)):
        containers, accessible, _ = await service._resolve_search_scope("u1", "org", {}, None)
    assert containers is None and accessible == {"v1": "r1"}
    kwargs = service.graph_provider.get_accessible_virtual_record_ids.await_args.kwargs
    assert kwargs["exclude_app_ids"] == OFF


@pytest.mark.asyncio
async def test_an_exclusion_keeps_the_record_id_path_even_with_container_filtering_on() -> None:
    # A container scope has no way to leave one app out.
    service = _retrieval(container_flag=True)
    with patch("app.modules.retrieval.retrieval_service.excluded_demo_connector_ids", AsyncMock(return_value=OFF)):
        containers, _, _ = await service._resolve_search_scope("u1", "org", {}, None)
    assert containers is None
    service.graph_provider.get_accessible_containers.assert_not_called()


@pytest.mark.asyncio
async def test_an_unreadable_setting_does_not_break_search() -> None:
    service = _retrieval(container_flag=False)
    with patch(
        "app.modules.retrieval.retrieval_service.excluded_demo_connector_ids",
        AsyncMock(side_effect=RuntimeError("kv down")),
    ):
        _, accessible, _ = await service._resolve_search_scope("u1", "org", {}, None)
    assert accessible == {"v1": "r1"}
    assert service.graph_provider.get_accessible_virtual_record_ids.await_args.kwargs["exclude_app_ids"] == frozenset()


# --- Browsing ----------------------------------------------------------------

def _hub(documents: dict[str, dict] | None = None) -> KnowledgeHubService:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key-1"})
    graph.get_user_app_ids = AsyncMock(return_value=["jira-1", "demo-1"])
    graph.get_user_permission_app_ids = AsyncMock(return_value=[])
    graph.get_knowledge_hub_root_nodes = AsyncMock(return_value={"nodes": [], "total": 0})
    graph.get_knowledge_hub_children = AsyncMock(return_value={"nodes": [], "total": 0})
    graph.get_knowledge_hub_search = AsyncMock(return_value={"nodes": [], "total": 0})
    graph.get_document = AsyncMock(side_effect=lambda key, collection: (documents or {}).get(key))
    return KnowledgeHubService(logger=MagicMock(), graph_provider=graph, excluded_app_ids=OFF)


@pytest.mark.asyncio
async def test_the_record_listing_search_leaves_the_demo_out() -> None:
    hub = _hub()
    await hub.get_nodes(user_id="u1", org_id="org", q="pricing", flattened=True)
    assert hub.graph_provider.get_knowledge_hub_search.await_args.kwargs["exclude_app_ids"] == OFF


@pytest.mark.asyncio
async def test_opening_a_demo_folder_by_id_shows_nothing() -> None:
    hub = _hub({"rg-1": {"connectorId": "demo-1"}})
    hub._validate_node_existence_and_type = AsyncMock()
    items, total, _ = await hub._get_children_nodes(
        user_key="user-key-1", org_id="org", parent_id="rg-1", parent_type="recordGroup",
        skip=0, limit=10, sort_by="name", sort_order="asc", q=None, node_types=None,
        record_types=None, origins=None, connector_ids=None, indexing_status=None,
        created_at=None, updated_at=None, size=None, only_containers=False, excluded_app_ids=OFF,
    )
    assert (items, total) == ([], 0)
    hub.graph_provider.get_knowledge_hub_children.assert_not_called()


@pytest.mark.asyncio
async def test_a_real_folder_still_opens() -> None:
    hub = _hub({"rg-2": {"connectorId": "jira-1"}})
    assert await hub._belongs_to("rg-2", "recordGroup", OFF) is False
    assert await hub._belongs_to("demo-1", "app", OFF) is True


# --- Tools that open records by id --------------------------------------------

@pytest.mark.asyncio
async def test_fetch_record_treats_a_demo_record_as_unavailable() -> None:
    graph = MagicMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"ok": True})
    graph.get_document = AsyncMock(return_value={"connectorId": "demo-1", "indexingStatus": "COMPLETED"})
    resolver = _RecordResolver(
        virtual_record_id_to_result={}, graph_provider=graph, blob_store=MagicMock(),
        config_service=MagicMock(), org_id="org", user_id="u1", frontend_url=None,
    )
    with patch("app.utils.fetch_full_record.excluded_demo_connector_ids", AsyncMock(return_value=OFF)):
        _, record, reason = await resolver.resolve("rec-1")
    assert record is None and reason == UNAVAILABLE


def _match(record_id: str, name: str) -> LookupMatch:
    return LookupMatch(
        id=record_id, name=name, record_type="PULL_REQUEST", connector_name="GITHUB",
        web_url=None, indexing_status="COMPLETED", identifier_used="x",
    )


@pytest.mark.asyncio
async def test_lookup_record_drops_a_demo_match() -> None:
    graph = MagicMock()
    graph.get_knowledge_hub_node_access = AsyncMock(return_value={"name": "PR #211"})
    graph.get_document = AsyncMock(side_effect=lambda key, collection: {"connectorId": "demo-1" if key == "d" else "jira-1"})
    resolver = RecordResolver(
        graph_provider=graph, catalog=MagicMock(), org_id="org", user_id="u1", user_key="k",
        folder_mime_types=[], excluded_app_ids=OFF,
    )
    resolver._fetch_candidates = AsyncMock(return_value=(
        [_match("d", "PR #211"), _match("j", "Real")],
        ["weburl"],
    ))
    matches, _ = await resolver._resolve_one("https://github.acme-demo.example/svc-export/pull/211")
    assert [m.id for m in matches] == ["j"]


@pytest.mark.asyncio
async def test_navigate_to_a_demo_node_finds_nothing() -> None:
    graph = MagicMock()
    graph.get_knowledge_hub_node_access = AsyncMock(return_value={"nodeType": "record", "name": "Pricing"})
    graph.get_document = AsyncMock(return_value={"connectorId": "demo-1"})
    navigator = GraphNavigator(graph_provider=graph, user_id="u1", user_key="k", org_id="org", excluded_app_ids=OFF)
    view = await navigator.navigate(node_id="rec-1")
    assert view.current is None and view.rows == []


@pytest.mark.asyncio
async def test_the_source_catalog_never_lists_switched_off_demo_data() -> None:
    state = {
        "agent_knowledge": [{"connectorId": "demo-1", "type": "Demo"}, {"connectorId": "jira-1", "type": "JIRA"}],
        EXCLUDED_APP_IDS_STATE_KEY: OFF,
    }
    catalog = await ConnectorCatalog.build(state, graph_provider=MagicMock(), user_key="k", org_id="org")
    assert catalog.connector_ids() == ["jira-1"]


# --- Opening a record by id over HTTP ------------------------------------------

_HIDE = "app.modules.demo_data.access.excluded_demo_connector_ids"


def _http_request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "org"}
    request.app.container.logger.return_value = MagicMock()
    request.app.container.config_service.return_value = MagicMock()
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize(("excluded", "expect_404"), [(OFF, True), (frozenset(), False)])
async def test_the_record_page_hides_a_switched_off_demo_record(excluded, expect_404) -> None:
    graph = MagicMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"record": {"id": "rec-1"}})
    graph.get_document = AsyncMock(return_value={"connectorId": "demo-1"})
    with patch(_HIDE, AsyncMock(return_value=excluded)):
        if expect_404:
            with pytest.raises(HTTPException) as err:
                await connector_router.get_record_by_id(record_id="rec-1", request=_http_request(), graph_provider=graph)
            assert err.value.status_code == 404
        else:
            result = await connector_router.get_record_by_id(record_id="rec-1", request=_http_request(), graph_provider=graph)
            assert result == {"record": {"id": "rec-1"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("excluded", "expect_404"), [(OFF, True), (frozenset(), False)])
async def test_the_file_stream_hides_a_switched_off_demo_record(excluded, expect_404) -> None:
    graph = MagicMock()
    record = MagicMock(org_id="org", connector_id="demo-1")
    graph.get_document = AsyncMock(return_value={"_key": "org"})
    graph.get_record_by_id = AsyncMock(return_value=record)
    graph.check_record_access_with_details = AsyncMock(return_value={"ok": True})
    content = AsyncMock(return_value="bytes")
    with patch(_HIDE, AsyncMock(return_value=excluded)), \
            patch.object(connector_router, "_resolve_record_content_response", content), \
            patch.object(connector_router, "is_request_admin", MagicMock(return_value=False)):
        call = connector_router.stream_record(
            request=_http_request(), record_id="rec-1", convertTo=None, version=None,
            graph_provider=graph, config_service=MagicMock(),
        )
        if expect_404:
            with pytest.raises(HTTPException) as err:
                await call
            assert err.value.status_code == 404
            content.assert_not_called()
        else:
            assert await call == "bytes"


# --- Deep links into the Knowledge Hub -----------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("flattened", [False, True], ids=["browse", "search"])
async def test_a_deep_link_to_a_demo_folder_answers_like_a_missing_one(flattened) -> None:
    hub = _hub({"rg-1": {"connectorId": "demo-1"}})
    hub._get_current_node_info = AsyncMock(return_value={"name": "Pricing"})
    response = await hub.get_nodes(
        user_id="u1", org_id="org", parent_id="rg-1", parent_type="recordGroup", flattened=flattened,
    )
    assert response.success is False and response.errorCode == 404
    assert response.currentNode is None and response.items == []
    hub._get_current_node_info.assert_not_called()


@pytest.mark.asyncio
async def test_the_source_filter_does_not_offer_switched_off_demo_data() -> None:
    hub = _hub()
    hub.graph_provider.get_knowledge_hub_filter_options = AsyncMock(
        return_value={"apps": [{"id": "demo-1", "name": "Acme"}, {"id": "jira-1", "name": "Jira"}]}
    )
    filters = await hub._get_available_filters("user-key-1", "org", OFF)
    assert [a.id for a in filters.connectors] == ["jira-1"]


# --- Concurrent record reads ---------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_reads_all_wait_for_the_setting() -> None:
    graph = MagicMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"ok": True})
    graph.get_document = AsyncMock(return_value={"connectorId": "demo-1", "indexingStatus": "COMPLETED"})
    resolver = _RecordResolver(
        virtual_record_id_to_result={}, graph_provider=graph, blob_store=MagicMock(),
        config_service=MagicMock(), org_id="org", user_id="u1", frontend_url=None,
    )
    resolver._download = AsyncMock(return_value={"id": "leak"})

    async def slow_lookup(*_args: object) -> frozenset[str]:
        await asyncio.sleep(0.05)
        return OFF

    with patch("app.utils.fetch_full_record.excluded_demo_connector_ids", side_effect=slow_lookup) as lookup:
        results = await asyncio.gather(*(resolver.resolve(f"rec-{i}") for i in range(3)))
    assert [r[2] for r in results] == [UNAVAILABLE] * 3
    resolver._download.assert_not_called()
    assert lookup.await_count == 1
