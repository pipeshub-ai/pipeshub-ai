"""Knowledge Hub service and authenticatedAs links.

The service knows nothing about links: a connector reached only through a link
arrives in the access context the provider returns (its gate and its grants), and
the provider's queries count the linked account themselves. These tests pin that
the service passes that context through untouched.
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
LINKED = "jira-1"


@pytest.fixture
def provider() -> AsyncMock:
    p = AsyncMock()
    p.get_user_by_user_id.return_value = {"_key": USER_KEY}
    # The provider's gate already includes the connector reachable only through a link.
    p.get_knowledge_hub_access_context_v2.return_value = {
        "grantee_ids": [USER_KEY], "gated_app_ids": ["own-1", LINKED],
    }
    p.get_knowledge_hub_access_v3.return_value = {
        "grantee_ids": [USER_KEY], "gated_app_ids": ["own-1", LINKED],
        "by_connector": {"own-1": [], LINKED: ["issue-1"]},
    }
    return p


@pytest.fixture
def service(provider) -> KnowledgeHubService:
    log = logging.getLogger("test_kh_authenticated_as")
    log.setLevel(logging.CRITICAL)
    return KnowledgeHubService(logger=log, graph_provider=provider)


@pytest.mark.asyncio
async def test_every_gated_app_the_provider_returns_is_listed(service, provider) -> None:
    provider.get_knowledge_hub_root_nodes_v2.return_value = {
        "partitions": [{"rows": [], "hasMore": False, "total": 0, "ids": []}], "scope": None,
    }
    await service.get_nodes(user_id="u1", org_id=ORG)
    assert provider.get_knowledge_hub_root_nodes_v2.await_args.kwargs["user_app_ids"] == ["own-1", LINKED]


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


@pytest.mark.asyncio
async def test_a_linked_connectors_grants_reach_the_listing_unchanged(service, provider) -> None:
    provider.get_knowledge_hub_connector_page_v3.return_value = {
        "rows": [], "hasMore": False, "total": 0, "counts": None,
        "scope": {"admitted": True, "nodeId": LINKED},
    }
    await service.get_nodes(user_id="u1", org_id=ORG, parent_id=LINKED, parent_type="app")
    kwargs = provider.get_knowledge_hub_connector_page_v3.await_args.kwargs
    assert kwargs["gated_app_ids"] == ["own-1", LINKED]
    # The page reads the connector's grants itself (the source account's among
    # them) for this user: nothing narrower is handed to it.
    assert kwargs["user_key"] and kwargs["granted_ids"] is None
    assert kwargs.get("grants_by_connector") is None
