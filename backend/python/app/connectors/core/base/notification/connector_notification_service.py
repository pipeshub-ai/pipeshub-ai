"""Publish connector errors/warnings to the notification topic/stream (Kafka or Redis)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.connectors.services.kafka_service import KafkaService


CONNECTOR_ERROR_TYPE = "CONNECTOR_ERROR"
CONNECTOR_WARNING_TYPE = "CONNECTOR_WARNING"
DEFAULT_CONNECTOR_NOTIFICATION_LINK = "workspace/connectors"


class NotificationSeverity(str, Enum):
    """Matches INotification.severity in backend/nodejs notification schema."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ConnectorNotificationService:
    """Builds INotification-shaped payloads and publishes via IMessagingProducer."""

    def __init__(self, kafka_service: KafkaService, logger: Any) -> None:
        self._kafka_service = kafka_service
        self._logger = logger

    async def publish_notification(
        self,
        *,
        user_id: str,
        org_id: str,
        connector_id: str,
        connector_name: str,
        title: str| None = None,
        message: str,
        severity: NotificationSeverity = NotificationSeverity.INFO,
        error_code: str | None = None,
    ) -> None:
        """Publish a user-visible connector notification. Swallows broker errors after logging."""
        try:
            notif_type = (
                CONNECTOR_WARNING_TYPE
                if severity is NotificationSeverity.WARNING
                else CONNECTOR_ERROR_TYPE
            )
            title = title or message[:100]
            payload: dict[str, Any] = {
                "title": title,
                "message": message,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "redirectLink": DEFAULT_CONNECTOR_NOTIFICATION_LINK,
            }
            if error_code:
                payload["errorCode"] = error_code

            document: dict[str, Any] = {
                "orgId": org_id,
                "type": notif_type,
                "severity": severity.value,
                "status": "Unread",
                "origin": "Connector Service",
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
