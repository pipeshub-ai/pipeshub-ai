"""Publish connector errors/warnings to the notification topic/stream (Kafka or Redis)."""

from __future__ import annotations

from typing import Any

from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin
from app.connectors.services.kafka_service import KafkaService


CONNECTOR_ERROR_TYPE = "CONNECTOR_ERROR"
CONNECTOR_WARNING_TYPE = "CONNECTOR_WARNING"
DEFAULT_CONNECTOR_NOTIFICATION_LINK = "workspace/connectors/"


class NotificationService:
    """Builds INotification-shaped payloads and publishes via IMessagingProducer."""

    def __init__(self, kafka_service: KafkaService, logger: Any) -> None:
        self._kafka_service = kafka_service
        self._logger = logger

    async def publish_notification(
        self,
        *,
        user_id: str,
        org_id: str,
        payload: dict[str, Any],
        type: NotificationType,
        origin: NotificationOrigin,
        severity: NotificationSeverity = NotificationSeverity.INFO,
    ) -> None:
        """Publish a user-visible connector notification. Swallows broker errors after logging."""
        try:
            document: dict[str, Any] = {
                "orgId": org_id,
                "type": type.value,
                "severity": severity.value,
                "status": "unread",
                "origin": origin.value,
                "assignedTo": user_id,
                "payload": payload,
                "isDeleted": False,
            }
            await self._kafka_service.publish_notification(document)
        except Exception as exc:  # noqa: BLE001 — must not break connector sync
            self._logger.warning(
                "Failed to publish connector notification (user will not see in-app alert): %s",
                exc,
                exc_info=True,
            )
