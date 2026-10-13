"""Knowledge Hub service and authenticatedAs links.

The service knows nothing about links: connectors reached only through a link arrive in
the app-id list the provider returns, and the provider's queries count the linked account
themselves. These tests pin that the service stays a pass-through.
"""

import logging
from collections.abc import Awaitable
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

USER_KEY = "creator-key"
ORG = "org-1"


@pytest.fixture
def provider() -> AsyncMock:
    p = AsyncMock()
    # get_user_app_ids already includes connectors reachable only through a link
    p.get_user_app_ids = AsyncMock(return_value=["own-1", "jira-1"])
    p.get_user_permission_app_ids = AsyncMock(return_value=[])
    return p


@pytest.fixture
def service(provider) -> KnowledgeHubService:
    log = logging.getLogger("test_kh_authenticated_as")
    log.setLevel(logging.CRITICAL)
    return KnowledgeHubService(logger=log, graph_provider=provider)


class TestRootLevel:
    async def test_every_app_id_the_provider_returns_is_used(self, service, provider) -> None:
        provider.get_knowledge_hub_root_nodes = AsyncMock(return_value={"nodes": [], "total": 0})

        await service._get_root_level_nodes(USER_KEY, ORG, 0, 50, "name", "asc", None, None, None, only_containers=False)

        assert provider.get_knowledge_hub_root_nodes.await_args.kwargs["user_app_ids"] == ["own-1", "jira-1"]

    async def test_connector_filter_still_applies(self, service, provider) -> None:
        provider.get_knowledge_hub_root_nodes = AsyncMock(return_value={"nodes": [], "total": 0})

        await service._get_root_level_nodes(
            USER_KEY, ORG, 0, 50, "name", "asc", None, None, ["own-1"], only_containers=False
        )

        assert provider.get_knowledge_hub_root_nodes.await_args.kwargs["user_app_ids"] == ["own-1"]


class TestSearch:
    def _search(self, service, **kwargs) -> Awaitable:
        defaults = {
            "user_key": USER_KEY, "org_id": ORG, "skip": 0, "limit": 2, "sort_by": "updatedAt",
            "sort_order": "desc", "q": "x", "node_types": None, "record_types": None, "origins": None,
            "connector_ids": None, "indexing_status": None, "created_at": None, "updated_at": None,
            "size": None, "only_containers": False,
        }
        defaults.update(kwargs)
        return service._search_nodes(**defaults)

    async def test_search_is_a_single_provider_call_as_the_caller(self, service, provider) -> None:
        provider.get_knowledge_hub_search = AsyncMock(return_value={"nodes": [], "total": 0})

        await self._search(service)

        provider.get_knowledge_hub_search.assert_awaited_once()
        assert provider.get_knowledge_hub_search.await_args.kwargs["user_key"] == USER_KEY

    async def test_scoped_search_passes_the_parent_through(self, service, provider) -> None:
        provider.get_knowledge_hub_search = AsyncMock(return_value={"nodes": [], "total": 0})

        await self._search(service, parent_id="jira-1", parent_type="app")

        provider.get_knowledge_hub_search.assert_awaited_once()
        assert provider.get_knowledge_hub_search.await_args.kwargs["parent_id"] == "jira-1"

    async def test_paging_arguments_reach_the_provider_unchanged(self, service, provider) -> None:
        provider.get_knowledge_hub_search = AsyncMock(return_value={"nodes": [], "total": 0})

        await self._search(service, skip=2, limit=2)

        call = provider.get_knowledge_hub_search.await_args.kwargs
        assert (call["skip"], call["limit"]) == (2, 2)


class TestAppLookupFailure:
    async def test_browse_reports_an_error_not_an_empty_listing(self, service, provider) -> None:
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": USER_KEY})
        provider.get_user_app_ids = AsyncMock(side_effect=RuntimeError("graph down"))
        provider.get_knowledge_hub_root_nodes = AsyncMock(return_value={"nodes": [], "total": 0})

        response = await service.get_nodes(user_id="user-1", org_id=ORG)

        assert response.success is False
        assert response.items == []
        provider.get_knowledge_hub_root_nodes.assert_not_awaited()

    async def test_name_search_on_neo4j_reports_an_error_not_an_empty_listing(self) -> None:
        # A name search reaches the app lookup inside the provider's own search,
        # not through the service, so the real provider is wired in here.
        neo4j = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        neo4j.client = AsyncMock()
        neo4j.client.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
        neo4j.get_user_by_user_id = AsyncMock(return_value={"_key": USER_KEY})
        service = KnowledgeHubService(logger=logging.getLogger("test_kh_authenticated_as"), graph_provider=neo4j)

        response = await service.get_nodes(user_id="user-1", org_id=ORG, q="budget")

        assert response.success is False
        assert response.items == []
