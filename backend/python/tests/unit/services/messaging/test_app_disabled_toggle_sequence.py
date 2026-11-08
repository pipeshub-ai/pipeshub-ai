"""An appDisabled event that a newer toggle superseded leaves the connector on.

A quick off-then-on returns both requests before the entity consumer handles the off. Handling
it then wrote isActive false over the on, so the sync the on started was skipped as "disabled"
(the 11-07 nightly's Confluence mutation tests on Arango). Each toggle's event carries the
connector's toggle number; one older than the current, on a connector that is on, is skipped.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.messaging.kafka.handlers.entity import EntityEventService

_COORDINATOR = "app.services.messaging.kafka.handlers.entity.get_coordinator"


def _service(app_doc: dict) -> tuple[EntityEventService, AsyncMock]:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=app_doc)
    graph.update_node = AsyncMock()
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    return EntityEventService(logger=MagicMock(), graph_provider=graph, app_container=container), graph


def _disabled(**seq: int) -> dict:
    return {"orgId": "org-1", "apps": ["confluence"], "connectorId": "c1", **seq}


def _disabled_it(graph: AsyncMock) -> bool:
    return any(c.args[2].get("isActive") is False for c in graph.update_node.await_args_list)


@pytest.mark.asyncio
async def test_an_off_handled_after_a_later_on_leaves_the_connector_on_and_its_sync_running() -> None:
    svc, graph = _service({"_key": "c1", "isActive": True, "toggleSeq": 2})

    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await svc.process_event("appDisabled", _disabled(toggleSeq=1)) is True

    assert not _disabled_it(graph)
    coordinator.return_value.request_stop.assert_not_awaited()
    graph.reset_indexing_status_for_connector.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_current_toggle_still_disables() -> None:
    svc, graph = _service({"_key": "c1", "isActive": False, "toggleSeq": 2})

    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await svc.process_event("appDisabled", _disabled(toggleSeq=2)) is True

    assert _disabled_it(graph)
    coordinator.return_value.request_stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_an_event_without_a_number_still_disables_a_connector_that_is_on() -> None:
    """A refresh token gone bad or a delete sends appDisabled with no toggle behind it."""
    svc, graph = _service({"_key": "c1", "isActive": True, "toggleSeq": 5})

    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await svc.process_event("appDisabled", _disabled()) is True

    assert _disabled_it(graph)
    coordinator.return_value.request_stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_an_older_off_still_disables_when_a_newer_toggle_left_the_connector_off() -> None:
    """A newer on that was reverted, or a newer off: stopping the sync is still owed, and the write is the same."""
    svc, graph = _service({"_key": "c1", "isActive": False, "toggleSeq": 3})

    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await svc.process_event("appDisabled", _disabled(toggleSeq=1)) is True

    assert _disabled_it(graph)
    coordinator.return_value.request_stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_a_connector_from_before_numbering_still_disables() -> None:
    svc, graph = _service({"_key": "c1", "isActive": True})

    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await svc.process_event("appDisabled", _disabled(toggleSeq=1)) is True

    assert _disabled_it(graph)
