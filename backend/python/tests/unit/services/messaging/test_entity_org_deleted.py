"""H2 OE-1..OE-3: ``orgDeleted`` erases the org's knowledge publications (P2.13 ``erase_org``) before the org is marked
inactive, flag on or off; a failed erasure leaves the org as it was and the event unhandled."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.harness.knowledge.service import KnowledgePublications
from app.services.messaging.kafka.handlers.entity import EntityEventService
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import Audience, PublicationDraft


@pytest.fixture(autouse=True)
def _no_paid_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """The org's paid-result purge and its delayed re-purge (main #6095) run after the erasure; their own tests hold them."""
    from app.services.messaging.kafka.handlers import entity

    cache = MagicMock()
    cache.purge_org = AsyncMock()
    monkeypatch.setattr(entity.paid_cache_access, "paid_cache_for", MagicMock(return_value=cache))
    monkeypatch.setattr(entity, "schedule_org_repurge", AsyncMock())


async def _service(*, fail: bool = False) -> tuple[EntityEventService, AsyncMock, InMemoryPublicationDirectory]:
    graph = AsyncMock()
    directory = InMemoryPublicationDirectory()
    for org, container, pid in (("org-1", "apps/kb-1", "p1"), ("org-1", "recordGroups/rg-1", "p2"), ("org-2", "apps/kb-9", "p3")):
        draft = PublicationDraft(org, container, Audience(org=True), "full", "owner")
        _ = await directory.create(draft, publication_id=pid, now_ms=1)
    if fail:
        directory.erase_org = AsyncMock(side_effect=ConnectionError("graph down"))  # type: ignore[method-assign]
    publications = KnowledgePublications(graph, None, directory=directory)
    service = EntityEventService(MagicMock(spec=logging.Logger), graph, MagicMock(), publications=publications)
    return service, graph, directory


async def test_oe1_org_deletion_erases_its_publications_then_marks_it_inactive() -> None:
    service, graph, directory = await _service()
    order: list[str] = []
    original = directory.erase_org

    async def erase(org_id: str) -> int:
        order.append("erase")
        return await original(org_id)

    async def upsert(*_: object, **__: object) -> bool:
        order.append("inactive")
        return True

    directory.erase_org = erase  # type: ignore[method-assign]
    graph.batch_upsert_nodes = AsyncMock(side_effect=upsert)
    assert await service.process_event("orgDeleted", {"orgId": "org-1"}) is True
    assert order == ["erase", "inactive"]
    assert await directory.list_for_org("org-1") == []
    assert [p.id for p in await directory.list_for_org("org-2")] == ["p3"]


async def test_oe2_a_failed_erasure_leaves_the_org_and_the_event_unhandled() -> None:
    """The failure reaches the consumer's classifier as itself: a store outage is transient and redelivered."""
    service, graph, _ = await _service(fail=True)
    with pytest.raises(ConnectionError, match="graph down"):
        await service.process_event("orgDeleted", {"orgId": "org-1"})
    graph.batch_upsert_nodes.assert_not_awaited()


async def test_oe3_erasure_does_not_depend_on_the_publications_flag() -> None:
    service, _, directory = await _service()
    assert service._publications is not None  # pyright: ignore[reportPrivateUsage]
    service._publications.enabled = AsyncMock(side_effect=AssertionError("the flag is not read"))  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    assert await service.process_event("orgDeleted", {"orgId": "org-1"}) is True
    assert await directory.list_for_org("org-1") == []


def test_oe_the_default_service_erases_through_the_graph_directory() -> None:
    graph = AsyncMock()
    service = EntityEventService(MagicMock(spec=logging.Logger), graph, MagicMock())
    from app.harness.knowledge.directory import GraphPublicationDirectory

    assert isinstance(service._publications.directory, GraphPublicationDirectory)  # pyright: ignore[reportPrivateUsage]
