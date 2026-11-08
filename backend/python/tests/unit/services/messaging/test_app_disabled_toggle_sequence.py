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


class Apps:
    """The app document as the stores keep it. ``toggles`` land, one per read, after each read
    of the app: a toggle that commits while the handler is between its read and its write."""

    def __init__(self, doc: dict, *toggles: dict) -> None:
        self.doc = dict(doc)
        self.toggles = list(toggles)
        self.disabled = False

    async def get_document(self, key, collection, transaction=None, **_kw) -> dict:
        read = dict(self.doc)
        if self.toggles:
            self.doc.update(self.toggles.pop(0))
        return read

    async def update_node(self, key, collection, updates, transaction=None) -> bool:
        self.doc.update(updates)
        self.disabled = self.disabled or updates.get("isActive") is False
        return True

    async def update_node_fields_if_match(self, key, collection, updates, expected, transaction=None) -> bool:
        if any(self.doc.get(k) != v for k, v in expected.items()):
            return False
        return await self.update_node(key, collection, updates)


def _service(apps: Apps) -> EntityEventService:
    graph = AsyncMock()
    graph.get_document = AsyncMock(side_effect=apps.get_document)
    graph.update_node = AsyncMock(side_effect=apps.update_node)
    graph.update_node_fields_if_match = AsyncMock(side_effect=apps.update_node_fields_if_match)
    container = MagicMock()
    container.messaging_producer = AsyncMock()
    return EntityEventService(logger=MagicMock(), graph_provider=graph, app_container=container)


def _disabled(**seq: int) -> dict:
    return {"orgId": "org-1", "apps": ["confluence"], "connectorId": "c1", **seq}


async def _handle(apps: Apps, payload: dict) -> AsyncMock:
    with patch(_COORDINATOR) as coordinator:
        coordinator.return_value.request_stop = AsyncMock()
        assert await _service(apps).process_event("appDisabled", payload) is True
    return coordinator.return_value.request_stop


@pytest.mark.asyncio
async def test_an_off_handled_after_a_later_on_leaves_the_connector_on_and_its_sync_running() -> None:
    apps = Apps({"_key": "c1", "isActive": True, "toggleSeq": 2})

    stop = await _handle(apps, _disabled(toggleSeq=1))

    assert not apps.disabled and apps.doc["isActive"] is True
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_on_that_commits_between_the_read_and_the_write_is_not_overwritten() -> None:
    """The read sees the off's own number; the on lands before the write, which must then miss."""
    apps = Apps({"_key": "c1", "isActive": False, "toggleSeq": 1}, {"isActive": True, "toggleSeq": 2})

    stop = await _handle(apps, _disabled(toggleSeq=1))

    assert not apps.disabled and apps.doc["isActive"] is True
    stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_current_toggle_still_disables() -> None:
    apps = Apps({"_key": "c1", "isActive": False, "toggleSeq": 2})

    stop = await _handle(apps, _disabled(toggleSeq=2))

    assert apps.disabled
    stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_an_event_without_a_number_still_disables_a_connector_that_is_on() -> None:
    """A refresh token gone bad or a delete sends appDisabled with no toggle behind it."""
    apps = Apps({"_key": "c1", "isActive": True, "toggleSeq": 5})

    stop = await _handle(apps, _disabled())

    assert apps.disabled and apps.doc["isActive"] is False
    stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_an_older_off_still_disables_when_a_newer_toggle_left_the_connector_off() -> None:
    """A newer on that was reverted, or a newer off: stopping the sync is still owed, and the write is the same."""
    apps = Apps({"_key": "c1", "isActive": False, "toggleSeq": 3})

    stop = await _handle(apps, _disabled(toggleSeq=1))

    assert apps.disabled
    stop.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_a_connector_from_before_numbering_still_disables() -> None:
    apps = Apps({"_key": "c1", "isActive": True})

    await _handle(apps, _disabled(toggleSeq=1))

    assert apps.disabled
