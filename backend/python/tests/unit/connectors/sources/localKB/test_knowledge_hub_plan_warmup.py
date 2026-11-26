"""The plan warm-up: one browse of each kind for a sample user, answers dropped."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.core import org_context

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
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = []
    assert await service.warm_browse_plans() == 0
    service.get_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_kind_of_place_is_browsed_as_the_ui_does(service) -> None:
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = [SAMPLE]
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
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = [SAMPLE]
    service.get_nodes.side_effect = [RuntimeError("graph unavailable")] + [None] * 6
    assert await service.warm_browse_plans() == 7
    assert service.get_nodes.await_count == 7


@pytest.mark.asyncio
async def test_a_sample_whose_app_its_user_switched_off_is_passed_over(service) -> None:
    """Demo data a user switched off leaves their gate, so every browse under it would be refused: the next App's sample is used."""
    other = {**SAMPLE, "userId": "u-2", "appId": "app-2", "groupId": "group-2", "recordId": "record-2"}
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = [SAMPLE, other]
    service._excluded_apps = AsyncMock(side_effect=lambda user, org: frozenset({"app-1"}) if user == "u-1" else frozenset())
    assert await service.warm_browse_plans() == 7
    calls = [c.kwargs for c in service.get_nodes.await_args_list]
    assert {c["user_id"] for c in calls} == {"u-2"}
    assert {c.get("parent_id") for c in calls} == {None, "app-2", "group-2", "record-2"}


@pytest.mark.asyncio
async def test_no_browse_when_every_sample_is_switched_off(service) -> None:
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = [SAMPLE]
    service._excluded_apps = AsyncMock(return_value=frozenset({"app-1"}))
    assert await service.warm_browse_plans() == 0
    service.get_nodes.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_read_is_made_inside_the_samples_own_org(service) -> None:
    """The keeper has no request, so nothing else binds an org: each sample's reads are bound to that sample's org only."""
    other = {**SAMPLE, "userId": "u-2", "orgId": "org-2", "appId": "app-2"}
    service.graph_provider.get_knowledge_hub_warm_samples.return_value = [SAMPLE, other]
    bound: list[tuple[str, str | None]] = []

    async def excluded(_user: str, org: str) -> frozenset[str]:
        bound.append((org, org_context.current_org_id()))
        return frozenset({"app-1"})

    async def get_nodes(*, org_id: str, **_kwargs: object) -> None:
        bound.append((org_id, org_context.current_org_id()))

    service._excluded_apps = excluded
    service.get_nodes = AsyncMock(side_effect=get_nodes)

    assert await service.warm_browse_plans() == 7

    assert bound[:2] == [("org-1", "org-1"), ("org-2", "org-2")]
    assert bound[2:] == [("org-2", "org-2")] * 7
    assert org_context.peek() is None
