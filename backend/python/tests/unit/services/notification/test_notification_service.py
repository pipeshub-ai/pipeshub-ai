"""Tests for NotificationService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notification.notification_service import NotificationService
from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin


@pytest.mark.asyncio
async def test_publish_error_sends_expected_document() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(return_value=True)
    logger = MagicMock()
    svc = NotificationService(kafka_service, logger)

    payload = {
        "title": "S3: Bucket access denied",
        "message": "Bucket access denied",
        "connectorId": "conn-1",
        "connectorName": "S3",
        "redirectLink": "/connectors",
        "errorCode": "AccessDenied",
    }

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        payload=payload,
        type=NotificationType.CONNECTOR_SYNC_ERROR,
        origin=NotificationOrigin.CONNECTOR,
        severity=NotificationSeverity.ERROR,
    )

    kafka_service.publish_notification.assert_awaited_once()
    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["type"] == NotificationType.CONNECTOR_SYNC_ERROR.value
    assert doc["status"] == "unread"
    assert doc["severity"] == NotificationSeverity.ERROR.value
    assert doc["origin"] == NotificationOrigin.CONNECTOR.value
    assert doc["assignedTo"] == "507f1f77bcf86cd799439011"
    assert doc["orgId"] == "507f191e810c19729de860ea"
    assert doc["payload"]["title"] == "S3: Bucket access denied"
    assert doc["payload"]["message"] == "Bucket access denied"
    assert doc["payload"]["connectorId"] == "conn-1"
    assert doc["payload"]["connectorName"] == "S3"
    assert doc["payload"]["redirectLink"] == "/connectors"
    assert doc["payload"]["errorCode"] == "AccessDenied"


@pytest.mark.asyncio
async def test_publish_error_warning_type() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(return_value=True)
    svc = NotificationService(kafka_service, MagicMock())

    payload = {
        "title": "Rate limited",
        "message": "Rate limited",
        "connectorId": "c",
        "connectorName": "S3",
    }

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        payload=payload,
        type=NotificationType.CONNECTOR_WARNING,
        origin=NotificationOrigin.CONNECTOR,
        severity=NotificationSeverity.WARNING,
    )

    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["severity"] == NotificationSeverity.WARNING.value
    assert doc["type"] == NotificationType.CONNECTOR_WARNING.value


@pytest.mark.asyncio
async def test_publish_error_swallows_broker_failure() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(side_effect=RuntimeError("broker down"))
    logger = MagicMock()
    svc = NotificationService(kafka_service, logger)

    payload = {
        "title": "oops",
        "message": "oops",
        "connectorId": "c",
        "connectorName": "S3",
    }

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        payload=payload,
        type=NotificationType.CONNECTOR_SYNC_ERROR,
        origin=NotificationOrigin.CONNECTOR,
    )

    logger.warning.assert_called()
