"""DELETE /api/v1/records/{id} with ENABLE_SOFT_DELETE on and off."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import EventTypes, OriginTypes
from tests.unit.connectors.api.test_router_part1 import _mock_request

ROUTER = "app.connectors.api.router"


def _graph(result: dict) -> AsyncMock:
    graph = AsyncMock()
    # The route deletes KB uploads only; a synced record is refused before this point.
    graph.check_record_access_with_details = AsyncMock(
        return_value={"record": {"origin": OriginTypes.UPLOAD.value}}
    )
    graph.delete_record = AsyncMock(return_value=result)
    return graph


def _request() -> MagicMock:
    container = MagicMock()
    container.logger = MagicMock(return_value=MagicMock())
    return _mock_request(container=container)


async def test_flag_on_moves_the_record_to_the_trash_and_cleans_vectors_only() -> None:
    from app.connectors.api.router import delete_record

    graph = _graph({
        "success": True, "softDeleted": True, "orgId": "org-1", "connectorId": "kb1", "isKb": True,
        "batchId": "b1", "virtualRecordIds": ["v1"], "softDeletedRecords": [{"record_id": "rec-1"}],
    })
    kafka = AsyncMock()
    with patch(f"{ROUTER}.is_soft_delete_enabled", AsyncMock(return_value=True)), \
            patch(f"{ROUTER}.notify_kb_records_changed", AsyncMock()):
        result = await delete_record("rec-1", _request(), graph, kafka)

    assert graph.delete_record.await_args.kwargs["soft_delete"] is True
    assert result["softDeleted"] is True and result["batchId"] == "b1"
    (call,) = kafka.publish_event.await_args_list
    topic, event = call.args
    assert topic == "record-events"
    assert event["eventType"] == EventTypes.SOFT_DELETE_RECORDS.value
    assert event["payload"]["virtualRecordIds"] == ["v1"]


async def test_a_cleanup_the_broker_refuses_is_reported_as_pending() -> None:
    """publish_event answers False without raising when the broker refuses the event."""
    from app.connectors.api.router import delete_record

    graph = _graph({
        "success": True, "softDeleted": True, "orgId": "org-1", "connectorId": "kb1", "isKb": True,
        "batchId": "b1", "virtualRecordIds": ["v1"], "softDeletedRecords": [{"record_id": "rec-1"}],
    })
    kafka = AsyncMock()
    kafka.publish_event = AsyncMock(return_value=False)

    async def once(fn, **_kwargs) -> object:
        return await fn()

    with patch(f"{ROUTER}.is_soft_delete_enabled", AsyncMock(return_value=True)), \
            patch(f"{ROUTER}.notify_kb_records_changed", AsyncMock()), \
            patch(f"{ROUTER}.retry_async", once):
        result = await delete_record("rec-1", _request(), graph, kafka)

    assert result["softDeleted"] is True
    assert result["vectorCleanupPending"] is True
    assert result["vectorCleanupFailedVirtualRecordIds"] == ["v1"]


async def test_flag_off_is_the_hard_delete() -> None:
    from app.connectors.api.router import delete_record

    graph = _graph({
        "success": True,
        "eventData": {"eventType": "deleteRecord", "topic": "record-events", "payload": {"recordId": "rec-1"}},
    })
    kafka = AsyncMock()
    with patch(f"{ROUTER}.is_soft_delete_enabled", AsyncMock(return_value=False)):
        result = await delete_record("rec-1", _request(), graph, kafka)

    assert graph.delete_record.await_args.kwargs["soft_delete"] is False
    assert "softDeleted" not in result
    assert kafka.publish_event.await_args.args[1]["eventType"] == "deleteRecord"


@pytest.mark.parametrize("connector_name", ["DRIVE", "DROPBOX", "CONFLUENCE"])
async def test_flag_on_a_user_cannot_put_a_synced_record_in_the_trash(connector_name: str) -> None:
    """Only connectors trash synced records, so a synced parent in the trash was deleted at the source."""
    from app.connectors.api.router import delete_record

    graph = _graph({"success": True, "softDeleted": True})
    graph.check_record_access_with_details = AsyncMock(
        return_value={"record": {"origin": OriginTypes.CONNECTOR.value, "connectorName": connector_name}}
    )
    kafka = AsyncMock()
    with patch(f"{ROUTER}.is_soft_delete_enabled", AsyncMock(return_value=True)), \
            pytest.raises(HTTPException) as refused:
        await delete_record("rec-1", _request(), graph, kafka)

    assert refused.value.status_code == 403
    graph.delete_record.assert_not_awaited()
    graph.soft_delete_records.assert_not_awaited()
    kafka.publish_event.assert_not_awaited()
