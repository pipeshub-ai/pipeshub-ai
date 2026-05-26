"""Tests for ConnectorNotificationService."""

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

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        connector_id="conn-1",
        connector_name="S3",
        message="Bucket access denied",
        severity=NotificationSeverity.ERROR,
        error_code="AccessDenied",
    )

    kafka_service.publish_notification.assert_awaited_once()
    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["type"] == CONNECTOR_ERROR_TYPE
    assert doc["status"] == "Unread"
    assert doc["severity"] == "error"
    assert doc["origin"] == "Connector Service"
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
    svc = ConnectorNotificationService(kafka_service, MagicMock())

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        connector_id="c",
        connector_name="S3",
        message="Rate limited",
        severity=NotificationSeverity.WARNING,
    )

    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["severity"] == "warning"
    assert doc["type"] == CONNECTOR_WARNING_TYPE


@pytest.mark.asyncio
async def test_publish_error_swallows_broker_failure() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(side_effect=RuntimeError("broker down"))
    logger = MagicMock()
    svc = ConnectorNotificationService(kafka_service, logger)

    await svc.publish_notification(
        user_id="507f1f77bcf86cd799439011",
        org_id="507f191e810c19729de860ea",
        connector_id="c",
        connector_name="S3",
        message="oops",
    )

    logger.warning.assert_called()
