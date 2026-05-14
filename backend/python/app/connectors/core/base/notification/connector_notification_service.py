"""Publish connector errors/warnings to the notification topic/stream (Kafka or Redis)."""

from __future__ import annotations

from typing import Any

from app.connectors.services.kafka_service import KafkaService
from app.services.messaging.config import Topic
from app.utils.time_conversion import get_epoch_timestamp_in_ms


CONNECTOR_ERROR_TYPE = "CONNECTOR_ERROR"
CONNECTOR_WARNING_TYPE = "CONNECTOR_WARNING"
DEFAULT_CONNECTOR_NOTIFICATION_LINK = "/connectors"


class ConnectorNotificationService:
    """Builds INotification-shaped payloads and publishes via IMessagingProducer."""

    def __init__(self, kafka_service: KafkaService, logger: Any) -> None:
        self._kafka_service = kafka_service
        self._logger = logger

    async def publish_error(
        self,
        *,
        user_id: str,
        org_id: str,
        connector_id: str,
        connector_name: str,
        message: str,
        severity: str = "error",
        error_code: str | None = None,
    ) -> None:
        """Publish a user-visible connector notification. Swallows broker errors after logging."""
        try:
            notif_type = (
                CONNECTOR_WARNING_TYPE
                if severity.lower() == "warning"
                else CONNECTOR_ERROR_TYPE
            )
            title = f"{connector_name}: {message[:200]}"
            payload: dict[str, Any] = {
                "message": message,
                "connectorId": connector_id,
                "connectorName": connector_name,
                "severity": severity,
            }
            if error_code:
                payload["errorCode"] = error_code

            document: dict[str, Any] = {
                "title": title,
                "orgId": org_id,
                "type": notif_type,
                "link": DEFAULT_CONNECTOR_NOTIFICATION_LINK,
                "status": "Unread",
                "origin": "Internal Service",
                "assignedTo": user_id,
                "appName": str(connector_name),
                "appId": connector_id,
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
