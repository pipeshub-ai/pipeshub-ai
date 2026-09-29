"""Deleting a knowledge base also asks for its uploaded files to be removed from storage."""

from __future__ import annotations

from unittest.mock import AsyncMock

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
async def test_the_files_are_listed_before_the_delete_and_published_after(service):
    _owner(service)
    order = []
    service.graph_provider.get_uploaded_document_ids = AsyncMock(
        side_effect=lambda kb_id: order.append("list") or DOC_IDS
    )
    service.graph_provider.delete_connector_instance.side_effect = (
        lambda **_: order.append("delete") or {"success": True, "virtual_record_ids": []}
    )

    result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is True
    assert order == ["list", "delete"]
    storage_events = [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
    assert [e["payload"] for e in storage_events] == [{"orgId": "org1", "documentIds": DOC_IDS}]


@pytest.mark.asyncio
async def test_a_listing_failure_does_not_stop_the_delete(service):
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("graph busy"))

    result = await service.delete_knowledge_base("kb1", "user1", "org1")

    assert result["success"] is True
    service.graph_provider.delete_connector_instance.assert_awaited_once()
    assert not [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
    assert any("uploaded files" in str(c.args) for c in service.logger.error.call_args_list)


@pytest.mark.asyncio
async def test_no_uploads_no_storage_event(service):
    _owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(return_value=[])

    await service.delete_knowledge_base("kb1", "user1", "org1")

    assert not [e for e in _published(service) if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
