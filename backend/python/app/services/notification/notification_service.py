"""Publish connector errors/warnings to the notification topic/stream (Kafka or Redis)."""

from __future__ import annotations

from typing import Any
from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin, NotificationStatus, NotificationRecipientRole
from app.connectors.services.kafka_service import KafkaService
from app.core.envelope import sign_enabled


def notification_message(document: dict[str, Any]) -> dict[str, Any]:
    """What goes on the notification topic for *document*. Signed, a message covers only its ``payload``: with
    ENVELOPE_SIGN on the whole notification goes inside it, where the consumer reads it, so its org, recipients and
    text cannot be changed in transit. With signing off it is the document, as before. Publish it with the
    document's org bound, so it can be signed."""
    if not sign_enabled():
        return document
    return {"payload": {"notification": document, "orgId": document.get("orgId")}}


class NotificationService:
    """Builds INotification-shaped payloads and publishes via IMessagingProducer."""

    def __init__(self, kafka_service: KafkaService, logger: Any) -> None:
        self._kafka_service = kafka_service
        self._logger = logger

    async def publish_notification(
        self,
        *,
        org_id: str,
        origin: NotificationOrigin,
        type: NotificationType,
        severity: NotificationSeverity,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        redirect_link: str | None = None,
        recipient_user_ids: list[str] | None = None,
        recipient_roles: list[NotificationRecipientRole] | None = None,
    ) -> bool:
        """Publish a user-visible connector notification; returns whether the broker took it.

        Swallows broker errors after logging.
        """
        try:
            document: dict[str, Any] = {
                "orgId": org_id,
                "type": type.value,
                "severity": severity.value,
                "status": NotificationStatus.UNREAD.value,
                "originService": origin.value,
                "title": title,
                "message": message,
                "redirectLink": redirect_link,
                "payload": payload,
                "recipientUserIds": recipient_user_ids or [],
                "recipientRoles": [role.value for role in recipient_roles] if recipient_roles else [],
                "isDeleted": False,
            }
            return await self._kafka_service.publish_notification(notification_message(document)) is True
        except Exception as exc:  # noqa: BLE001 — must not break connector sync
            self._logger.warning(
                "Failed to publish connector notification: \n Title: %s \n type: %s",
                title,
                type.value,
                exc_info=True,
            )
            return False
