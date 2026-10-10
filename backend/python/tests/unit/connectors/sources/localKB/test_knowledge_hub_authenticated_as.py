"""Knowledge Hub service and authenticatedAs links.

The service knows nothing about links: a connector reached only through a link
arrives in the access context the provider returns (its gate and its grants), and
the provider's queries count the linked account themselves. These tests pin that
the service passes that context through untouched.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)

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
