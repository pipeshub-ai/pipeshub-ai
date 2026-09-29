"""Deleting a knowledge base also asks for its uploaded files to be removed from storage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import EventTypes

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
        side_effect=lambda kb_id: order.append("list") or DOC_IDS
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
    assert [e["payload"] for e in storage_events] == [
        {"orgId": "org1", "connectorId": "kb1", "documentIds": DOC_IDS}
    ]


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
