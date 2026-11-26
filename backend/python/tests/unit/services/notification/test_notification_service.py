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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker", "expected"),
    [
        (AsyncMock(return_value=True), True),
        (AsyncMock(return_value=False), False),
        (AsyncMock(side_effect=RuntimeError("broker down")), False),
    ],
)
async def test_publish_says_whether_the_broker_took_it(broker: AsyncMock, expected: bool) -> None:
    kafka_service = MagicMock()
    kafka_service.publish_notification = broker
    svc = NotificationService(kafka_service, MagicMock())

    taken = await svc.publish_notification(
        org_id="507f191e810c19729de860ea",
        origin=NotificationOrigin.CONNECTOR,
        type=NotificationType.CONNECTOR_WARNING,
        severity=NotificationSeverity.WARNING,
        title="t",
        message="m",
    )

    assert taken is expected


# --- Signed notifications: the signature covers only "payload", so the whole notification must be inside it. ---

ORG = "507f191e810c19729de860ea"


async def _publish_signed(monkeypatch: pytest.MonkeyPatch, *, details: dict | None) -> dict:
    import json

    from app.core import envelope
    from app.core.org_context import for_org

    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    key = envelope.derive_key("notification-signed-test")
    envelope.configure(None, key)
    try:
        kafka_service = MagicMock()
        kafka_service.publish_notification = AsyncMock(return_value=True)
        svc = NotificationService(kafka_service, MagicMock())
        with for_org(ORG, job="test"):
            await svc.publish_notification(
                org_id=ORG,
                origin=NotificationOrigin.CONNECTOR,
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title="S3: Bucket access denied",
                message="Bucket access denied",
                payload=details,
                recipient_user_ids=["507f1f77bcf86cd799439011"],
            )
            message = kafka_service.publish_notification.await_args.args[0]
            signed = envelope.sign_message("notification", message)
        assert "ctx" in signed, "the notification is signed"
        assert signed["ctx"]["orgId"] == ORG
        assert set(signed) <= {"ctx", "payload", "eventType", "eventId"}, "nothing the consumer reads is left unsigned"
        return json.loads(signed["payload"])
    finally:
        envelope.configure(None, None)


@pytest.mark.asyncio
async def test_signed_notification_carries_the_whole_document_inside_the_signed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    body = await _publish_signed(monkeypatch, details={"connectorId": "conn-1"})

    assert body["orgId"] == ORG
    doc = body["notification"]
    assert doc["orgId"] == ORG
    assert doc["title"] == "S3: Bucket access denied"
    assert doc["recipientUserIds"] == ["507f1f77bcf86cd799439011"]
    assert doc["payload"] == {"connectorId": "conn-1"}


@pytest.mark.asyncio
async def test_a_notification_without_details_is_signed_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # It used to go out unsigned (its "payload" was None), and a consumer enforcing the envelope dropped it.
    body = await _publish_signed(monkeypatch, details=None)

    assert body["notification"]["payload"] is None
    assert body["notification"]["type"] == NotificationType.CONNECTOR_SYNC_ERROR.value


@pytest.mark.asyncio
async def test_with_signing_off_the_document_is_sent_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVELOPE_SIGN", "false")
    kafka_service = MagicMock()
    kafka_service.publish_notification = AsyncMock(return_value=True)
    svc = NotificationService(kafka_service, MagicMock())
    await svc.publish_notification(
        org_id=ORG, origin=NotificationOrigin.CONNECTOR, type=NotificationType.CONNECTOR_SYNC_ERROR,
        severity=NotificationSeverity.ERROR, title="t", message="m",
    )
    doc = kafka_service.publish_notification.await_args.args[0]
    assert doc["orgId"] == ORG and doc["title"] == "t" and "notification" not in doc
