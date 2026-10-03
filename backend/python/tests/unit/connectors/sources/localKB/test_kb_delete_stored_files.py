"""Deleting a knowledge base also asks for its uploaded files to be removed from storage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, EventTypes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

DOC_IDS = ["65f1c0ffee0123456789abc1", "65f1c0ffee0123456789abc2"]


def _owner(service):
    service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    service.graph_provider.delete_connector_instance = AsyncMock(
        return_value={"success": True, "virtual_record_ids": []}
    )


def _published(service) -> list[dict]:
    return [call.args[1] for call in service.kafka_service.publish_event.await_args_list]


@pytest.mark.asyncio
async def test_the_files_are_listed_and_their_removal_published_before_the_delete(service):
    """Published first: after the graph delete a lost event could never be recovered."""
    _owner(service)
    order = []
    service.graph_provider.get_uploaded_document_ids = AsyncMock(
        side_effect=lambda kb_id, **_: order.append("list") or DOC_IDS
    )
    service.kafka_service.publish_event = AsyncMock(
        side_effect=lambda topic, event: order.append(event["eventType"])
    )
    service.graph_provider.delete_connector_instance.side_effect = (
        lambda **_: order.append("delete") or {"success": True, "virtual_record_ids": []}
    )

    result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is True
    assert order[:3] == ["list", EventTypes.DELETE_STORED_DOCUMENTS.value, "delete"]
    storage_events = [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
    assert [{k: v for k, v in e["payload"].items() if k != "scheduledAt"} for e in storage_events] == [
        {"orgId": "org1", "connectorId": "kb1", "documentIds": DOC_IDS}
    ]
    assert all(isinstance(e["payload"]["scheduledAt"], int) for e in storage_events)


@pytest.mark.asyncio
async def test_a_storage_event_that_cannot_be_published_deletes_nothing(service):
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=DOC_IDS)
    service.kafka_service.publish_event = AsyncMock(side_effect=RuntimeError("broker down"))

    with patch("app.utils.retry.asyncio.sleep", AsyncMock()):
        result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is False
    assert result["code"] == 503
    assert "nothing was deleted" in result["reason"]
    assert service.kafka_service.publish_event.await_count == 3  # retried before giving up
    service.graph_provider.delete_connector_instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_storage_event_the_broker_refuses_deletes_nothing(service) -> None:
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=DOC_IDS)
    service.kafka_service.publish_event = AsyncMock(return_value=False)

    with patch("app.utils.retry.asyncio.sleep", AsyncMock()):
        result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is False
    assert result["code"] == 503
    assert service.kafka_service.publish_event.await_count == 3
    service.graph_provider.delete_connector_instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_listing_failure_deletes_nothing_and_asks_to_retry(service):
    """The listed ids are the only handle on the files, so no list means no delete."""
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("graph busy"))

    result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is False
    assert result["code"] == 503
    assert "nothing was deleted" in result["reason"]
    service.graph_provider.delete_connector_instance.assert_not_awaited()
    service.kafka_service.publish_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_publish_that_fails_once_is_retried(service):
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=DOC_IDS)
    attempts = []

    async def flaky(topic, event):
        attempts.append(event["eventType"])
        if event["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value and attempts.count(event["eventType"]) == 1:
            raise RuntimeError("broker hiccup")

    service.kafka_service.publish_event = AsyncMock(side_effect=flaky)
    with patch("app.utils.retry.asyncio.sleep", AsyncMock()):
        result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is True
    assert attempts.count(EventTypes.DELETE_STORED_DOCUMENTS.value) == 2


@pytest.mark.asyncio
async def test_no_uploads_no_storage_event(service):
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[])

    await service.delete_knowledge_base("kb1", "user1", "org1")

    assert not [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]


def _writer(service):
    service.graph_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk1"})
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    service.graph_provider.validate_folder_in_kb = AsyncMock(return_value=True)
    service.graph_provider.validate_folder_exists_in_kb = AsyncMock(return_value=True)
    service.graph_provider.get_document = AsyncMock(return_value={"connectorId": "kb1", "orgId": "org1"})
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=DOC_IDS)


DELETES = {
    "folder": lambda service: service.delete_folder("kb1", "f1", "user1"),
    "records at the root": lambda service: service.delete_records_in_kb("kb1", ["r1", "r2"], "user1"),
    "records in a folder": lambda service: service.delete_records_in_folder("kb1", "f1", ["r1"], "user1"),
}
ROOTS = {"folder": ["f1"], "records at the root": ["r1", "r2"], "records in a folder": ["r1"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(DELETES))
async def test_the_files_under_what_is_deleted_are_scheduled_first(service, kind):
    _writer(service)
    order = []
    service.kafka_service.publish_event = AsyncMock(side_effect=lambda topic, event: order.append("publish"))
    service.processor_for_kb.return_value.on_records_deleted_cascade = AsyncMock(
        side_effect=lambda *a, **k: order.append("delete") or {"success": True, "successfully_deleted": 1, "total_requested": 1}
    )

    result = await DELETES[kind](service)

    assert result["success"] is True
    assert order == ["publish", "delete"]
    service.graph_provider.get_uploaded_document_ids.assert_awaited_once_with("kb1", under_record_ids=ROOTS[kind])
    event = service.kafka_service.publish_event.await_args.args[1]
    assert {k: v for k, v in event["payload"].items() if k != "scheduledAt"} == {
        "orgId": "org1", "connectorId": "kb1", "documentIds": DOC_IDS,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(DELETES))
async def test_a_removal_that_cannot_be_published_deletes_nothing(service, kind):
    _writer(service)
    service.kafka_service.publish_event = AsyncMock(side_effect=RuntimeError("broker down"))

    with patch("app.utils.retry.asyncio.sleep", AsyncMock()):
        result = await DELETES[kind](service)

    assert result["success"] is False
    assert result["code"] == 503
    assert "nothing was deleted" in result["reason"]
    service.processor_for_kb.return_value.on_records_deleted_cascade.assert_not_awaited()


def _documents(records: dict, kb: dict | None) -> Callable[..., Awaitable[dict | None]]:
    async def read(key: str, collection: str, *args: object, **kwargs: object) -> dict | None:
        return kb if collection == CollectionNames.APPS.value else records.get(key)
    return read


def _every_read_raises(service) -> bool:
    return all(c.kwargs.get("raise_on_error") is True for c in service.graph_provider.get_document.await_args_list)


@pytest.mark.asyncio
async def test_the_organisation_comes_from_the_kb_when_no_record_names_it(service) -> None:
    _writer(service)
    service.graph_provider.get_document = AsyncMock(
        side_effect=_documents({"f1": {"connectorId": "kb1"}}, {"_key": "kb1", "orgId": "org-from-kb"})
    )

    result = await service.delete_folder("kb1", "f1", "user1")

    assert result["success"] is True
    event = service.kafka_service.publish_event.await_args_list[0].args[1]
    assert event["payload"]["orgId"] == "org-from-kb"
    assert _every_read_raises(service)


@pytest.mark.asyncio
async def test_an_unreadable_record_deletes_nothing(service) -> None:
    """A failed read is not "no organisation": nothing is scheduled or deleted."""
    _writer(service)

    service.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("graph busy"))

    result = await service.delete_folder("kb1", "f1", "user1")

    assert (result["success"], result["code"]) == (False, 503)
    assert _every_read_raises(service)
    service.kafka_service.publish_event.assert_not_awaited()
    service.processor_for_kb.return_value.on_records_deleted_cascade.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_organisation_anywhere_deletes_nothing(service) -> None:
    _writer(service)
    service.graph_provider.get_document = AsyncMock(side_effect=_documents({"f1": {"connectorId": "kb1"}}, {"_key": "kb1"}))

    result = await service.delete_folder("kb1", "f1", "user1")

    assert (result["success"], result["code"]) == (False, 503)
    service.processor_for_kb.return_value.on_records_deleted_cascade.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(DELETES))
@pytest.mark.parametrize("trash_on", [True, False])
async def test_with_the_trash_on_the_files_stay_until_the_purge(service, kind, trash_on) -> None:
    """A restore needs the original uploads, so a soft delete schedules no removal.

    The flag is read once and handed to the cascade, so the two cannot disagree.
    """
    _writer(service)
    cascade = AsyncMock(return_value={"success": True, "successfully_deleted": 1, "total_requested": 1})
    service.processor_for_kb.return_value.on_records_deleted_cascade = cascade

    with patch(
        "app.connectors.sources.localKB.handlers.kb_service.is_soft_delete_enabled",
        AsyncMock(return_value=trash_on),
    ):
        result = await DELETES[kind](service)

    assert result["success"] is True
    assert cascade.await_args.kwargs["soft_delete"] is trash_on
    removals = [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
    assert bool(removals) is not trash_on
    assert service.graph_provider.get_uploaded_document_ids.await_count == (0 if trash_on else 1)
