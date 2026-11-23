"""An appEnabled event that a newer toggle superseded starts no sync.

Two enables that overlap (a Node retry landing while the first attempt is still running, or two
clicks) both read the connector as off, both write it on and both send appEnabled, so two syncs
were started. Each sync toggle numbers its event; an appEnabled older than the connector's current
number has no work left: a newer enable sends its own event, and a newer disable left it off.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.messaging.kafka.handlers.entity import EntityEventService


def _service(app_doc: dict | None) -> EntityEventService:
    async def get_document(key: str, collection: str, transaction: object = None, **_kw: object) -> dict | None:
        if collection == CollectionNames.ORGS.value:
            return {"_key": "org-1", "accountType": "enterprise"}
        if collection == CollectionNames.APPS.value:
            return dict(app_doc) if app_doc is not None else None
        raise AssertionError(f"unexpected read of {collection}")

    graph = AsyncMock()
    graph.get_document = AsyncMock(side_effect=get_document)
    svc = EntityEventService(logger=MagicMock(), graph_provider=graph, app_container=MagicMock())
    svc._handle_sync_event = AsyncMock(return_value=True)
    svc._start_requeue_of_parked_records = AsyncMock()
    return svc


def _enabled(**seq: int) -> dict:
    return {"orgId": "org-1", "apps": ["confluence"], "connectorId": "c1", "syncAction": "immediate", **seq}


async def _handle(app_doc: dict | None, payload: dict) -> EntityEventService:
    svc = _service(app_doc)
    assert await svc.process_event("appEnabled", payload) is True
    return svc


def _started(svc: EntityEventService) -> bool:
    return svc._handle_sync_event.await_count > 0


@pytest.mark.asyncio
async def test_the_older_of_two_overlapping_enables_starts_no_sync() -> None:
    svc = await _handle({"_key": "c1", "isActive": True, "toggleSeq": 8}, _enabled(toggleSeq=7))

    assert not _started(svc)
    svc._start_requeue_of_parked_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_newer_of_two_overlapping_enables_starts_its_sync() -> None:
    svc = await _handle({"_key": "c1", "isActive": True, "toggleSeq": 8}, _enabled(toggleSeq=8))

    svc._handle_sync_event.assert_awaited_once()
    assert svc._handle_sync_event.await_args.kwargs["value"]["connectorId"] == "c1"
    svc._start_requeue_of_parked_records.assert_awaited_once_with("c1")


@pytest.mark.asyncio
async def test_an_enable_handled_after_a_later_disable_starts_no_sync() -> None:
    svc = await _handle({"_key": "c1", "isActive": False, "toggleSeq": 3}, _enabled(toggleSeq=2))

    assert not _started(svc)


@pytest.mark.asyncio
async def test_an_event_without_a_number_still_starts_its_sync_and_reads_no_app() -> None:
    svc = await _handle({"_key": "c1", "isActive": True, "toggleSeq": 5}, _enabled())

    assert _started(svc)
    reads = [c.args[1] for c in svc.graph_provider.get_document.await_args_list]
    assert CollectionNames.APPS.value not in reads


@pytest.mark.asyncio
async def test_a_connector_from_before_numbering_still_starts_its_sync() -> None:
    svc = await _handle({"_key": "c1", "isActive": True}, _enabled(toggleSeq=1))

    assert _started(svc)


@pytest.mark.asyncio
async def test_an_app_that_cannot_be_read_still_starts_its_sync() -> None:
    svc = await _handle(None, _enabled(toggleSeq=1))

    assert _started(svc)
