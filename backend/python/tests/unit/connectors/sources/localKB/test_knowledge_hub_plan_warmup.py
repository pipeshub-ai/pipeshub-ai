"""The plan warm-up: one browse of each kind for a sample user, answers dropped."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import KnowledgeHubService

SAMPLE = {"userId": "u-1", "orgId": "org-1", "appId": "app-1", "groupId": "group-1", "recordId": "record-1"}


@pytest.fixture
def service() -> KnowledgeHubService:
    log = logging.getLogger("test_kh_plan_warmup")
    log.setLevel(logging.CRITICAL)
    svc = KnowledgeHubService(logger=log, graph_provider=AsyncMock())
    svc.get_nodes = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_without_a_sample_nothing_is_asked(service) -> None:
    service.graph_provider.get_knowledge_hub_warm_sample.return_value = None
    assert await service.warm_browse_plans() == 0
    service.get_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_kind_of_place_is_browsed_as_the_ui_does(service) -> None:
    service.graph_provider.get_knowledge_hub_warm_sample.return_value = SAMPLE
    assert await service.warm_browse_plans() == 7
    calls = [c.kwargs for c in service.get_nodes.await_args_list]
    assert all(c["user_id"] == "u-1" and c["org_id"] == "org-1" for c in calls)
    places = [(c.get("parent_type"), c.get("parent_id"), bool(c.get("only_containers"))) for c in calls]
    assert places == [
        (None, None, False), (None, None, False),
        ("app", "app-1", False), ("app", "app-1", True),
        ("recordGroup", "group-1", False), ("recordGroup", "group-1", True),
        ("record", "record-1", False),
    ]


@pytest.mark.asyncio
async def test_a_failing_request_does_not_stop_the_rest(service) -> None:
    service.graph_provider.get_knowledge_hub_warm_sample.return_value = SAMPLE
    service.get_nodes.side_effect = [RuntimeError("graph unavailable")] + [None] * 6
    assert await service.warm_browse_plans() == 7
    assert service.get_nodes.await_count == 7
