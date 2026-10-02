"""DELETE /api/v1/records/{record_id}: an uploaded file's removal is scheduled before the record goes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import EventTypes
from app.connectors.api.router import delete_record

DOC_ID = "65f1c0ffee0123456789abcd"


def _request() -> MagicMock:
    user = {"userId": "user-a", "orgId": "org-a"}
    req = MagicMock()
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.app.container.logger.return_value = MagicMock()
    return req


def _provider(origin: str = "UPLOAD") -> AsyncMock:
    connector_name = "KB" if origin == "UPLOAD" else "DRIVE"
    provider = AsyncMock()
    provider.check_record_access_with_details = AsyncMock(
        return_value={"record": {"id": "r1", "origin": origin, "connectorName": connector_name}}
    )
    provider.get_document = AsyncMock(return_value={"id": "r1", "connectorId": "kb-1", "origin": origin})
    provider.get_uploaded_document_ids = AsyncMock(return_value=[DOC_ID])
    provider.delete_record = AsyncMock(return_value={"success": True, "eventData": None})
    return provider


@pytest.mark.asyncio
async def test_the_removal_is_published_before_the_record_is_deleted():
    provider, kafka, order = _provider(), AsyncMock(), []
    kafka.publish_event = AsyncMock(side_effect=lambda topic, event: order.append(event["eventType"]))
    provider.delete_record.side_effect = lambda **_: order.append("delete") or {"success": True, "eventData": None}

    result = await delete_record("r1", _request(), provider, kafka)

    assert result["success"] is True
    assert order == [EventTypes.DELETE_STORED_DOCUMENTS.value, "delete"]
    provider.get_uploaded_document_ids.assert_awaited_once_with("kb-1", under_record_ids=["r1"])
    payload = kafka.publish_event.await_args.args[1]["payload"]
    assert {k: v for k, v in payload.items() if k != "scheduledAt"} == {
        "orgId": "org-a", "connectorId": "kb-1", "documentIds": [DOC_ID],
    }


@pytest.mark.asyncio
async def test_a_removal_that_cannot_be_published_deletes_nothing():
    provider, kafka = _provider(), AsyncMock()
    kafka.publish_event = AsyncMock(side_effect=RuntimeError("broker down"))

    with patch("app.utils.retry.asyncio.sleep", AsyncMock()), pytest.raises(HTTPException) as caught:
        await delete_record("r1", _request(), provider, kafka)

    assert caught.value.status_code == 503
    assert "nothing was deleted" in caught.value.detail
    assert kafka.publish_event.await_count == 3
    provider.delete_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_connector_record_schedules_no_storage_removal():
    provider, kafka = _provider(origin="CONNECTOR"), AsyncMock()

    with pytest.raises(HTTPException) as caught:
        await delete_record("r1", _request(), provider, kafka)

    assert caught.value.status_code == 403
    provider.get_uploaded_document_ids.assert_not_awaited()
    kafka.publish_event.assert_not_awaited()
    provider.delete_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_read_deletes_nothing() -> None:
    provider, kafka = _provider(), AsyncMock()
    provider.get_document = AsyncMock(side_effect=RuntimeError("graph 503"))

    with pytest.raises(HTTPException) as caught:
        await delete_record("r1", _request(), provider, kafka)

    assert caught.value.status_code == 503
    provider.get_document.assert_awaited_once()
    assert provider.get_document.await_args.kwargs == {"raise_on_error": True}
    provider.delete_record.assert_not_awaited()
    kafka.publish_event.assert_not_awaited()
