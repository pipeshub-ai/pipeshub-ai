"""Tests for NotificationService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notification.notification_service import NotificationService
from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin, NotificationRecipientRole


@pytest.mark.asyncio
async def test_publish_error_sends_expected_document() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(return_value=True)
    logger = MagicMock()
    svc = NotificationService(kafka_service, logger)

    payload = {
        "connectorId": "conn-1",
        "connectorName": "S3",
        "errorCode": "AccessDenied",
    }

    await svc.publish_notification(
        org_id="507f191e810c19729de860ea",
        origin=NotificationOrigin.CONNECTOR,
        type=NotificationType.CONNECTOR_SYNC_ERROR,
        severity=NotificationSeverity.ERROR,
        title="S3: Bucket access denied",
        message="Bucket access denied",
        payload=payload,
        redirect_link="/connectors",
        recipient_user_ids=["507f1f77bcf86cd799439011"],
        recipient_roles=[],
    )

    kafka_service.publish_notification.assert_awaited_once()
    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["type"] == NotificationType.CONNECTOR_SYNC_ERROR.value
    assert doc["status"] == "unread"
    assert doc["severity"] == NotificationSeverity.ERROR.value
    assert doc["originService"] == NotificationOrigin.CONNECTOR.value
    assert doc["recipientUserIds"] == ["507f1f77bcf86cd799439011"]
    assert doc["recipientRoles"] == []
    assert doc["orgId"] == "507f191e810c19729de860ea"
    assert doc["title"] == "S3: Bucket access denied"
    assert doc["message"] == "Bucket access denied"
    assert doc["payload"]["connectorId"] == "conn-1"


@pytest.mark.asyncio
async def test_publish_error_warning_type() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(return_value=True)
    svc = NotificationService(kafka_service, MagicMock())

    await svc.publish_notification(
        org_id="507f191e810c19729de860ea",
        origin=NotificationOrigin.CONNECTOR,
        type=NotificationType.CONNECTOR_WARNING,
        severity=NotificationSeverity.WARNING,
        title="Rate limited",
        message="Rate limited",
        payload={"connectorId": "c", "connectorName": "S3"},
        recipient_user_ids=["507f1f77bcf86cd799439011"],
        recipient_roles=[NotificationRecipientRole.ADMIN],
    )

    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["severity"] == NotificationSeverity.WARNING.value
    assert doc["type"] == NotificationType.CONNECTOR_WARNING.value
    assert doc["recipientRoles"] == ["admin"]


@pytest.mark.asyncio
async def test_publish_error_swallows_broker_failure() -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(side_effect=RuntimeError("broker down"))
    logger = MagicMock()
    svc = NotificationService(kafka_service, logger)

    await svc.publish_notification(
        org_id="507f191e810c19729de860ea",
        origin=NotificationOrigin.CONNECTOR,
        type=NotificationType.CONNECTOR_SYNC_ERROR,
        severity=NotificationSeverity.ERROR,
        title="oops",
        message="oops",
        payload={"connectorId": "c", "connectorName": "S3"},
        recipient_user_ids=["507f1f77bcf86cd799439011"],
        recipient_roles=[],
    )

    logger.warning.assert_called()
