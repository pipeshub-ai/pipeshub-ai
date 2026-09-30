"""DELETE /api/v1/records/{id} with ENABLE_SOFT_DELETE on and off."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import EventTypes
from tests.unit.connectors.api.test_router_part1 import _mock_request

ROUTER = "app.connectors.api.router"


def _graph(result: dict) -> AsyncMock:
    graph = AsyncMock()
    graph.check_record_access_with_details = AsyncMock(return_value={"record": {}})
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
