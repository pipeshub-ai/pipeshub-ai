"""Publish connector errors/warnings to the notification topic/stream (Kafka or Redis)."""

from __future__ import annotations

from typing import Any
from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin, NotificationStatus, NotificationRecipientRole
from app.connectors.services.kafka_service import KafkaService


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
        payload: dict[str, Any],
        redirect_link: str | None = None,
        recipient_user_ids: list[str],
        recipient_roles: list[NotificationRecipientRole],
    ) -> None:
        """Publish a user-visible connector notification. Swallows broker errors after logging."""
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
            await self._kafka_service.publish_notification(document)
        except Exception as exc:  # noqa: BLE001 — must not break connector sync
            self._logger.warning(
                "Failed to publish connector notification: \n Title: %s \n type: %s",
                title,
                type.value,
                exc_info=True,
            )
