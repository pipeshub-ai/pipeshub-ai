import json
from typing import Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import EventTypes
from app.services.messaging.interface.producer import IMessagingProducer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KafkaService:
    """Messaging service for connectors that delegates to IMessagingProducer.

    Despite the name (kept for backward compatibility), this class now works
    with any IMessagingProducer implementation (Kafka or Redis Streams).
    """

    def __init__(
        self,
        config_service: ConfigurationService,
        logger,
        producer: Optional[IMessagingProducer] = None,
    ) -> None:
        self.config_service = config_service
        self.logger = logger
        self._producer = producer

    async def _ensure_producer(self) -> None:
        """Ensure the producer is initialized and started"""
        if self._producer is None:
            raise RuntimeError(
                "No messaging producer configured. "
                "Set producer via constructor or set_producer()."
            )
        await self._producer.start()

    def set_producer(self, producer: IMessagingProducer) -> None:
        """Set the messaging producer (for deferred initialization)"""
        self._producer = producer

    async def publish_event(self, topic: str, event: Dict) -> bool:
        """
        Publish an event to a specified topic.
        :param topic: The topic/stream to publish to
        :param event: Dictionary containing the event data
        :return: True if successful, False otherwise
        """
        try:
            await self._ensure_producer()

            record_id = event.get("payload", {}).get("recordId")
            key = str(record_id) if record_id else str(event.get("timestamp", ""))

            return await self._producer.send_message(  # type: ignore
                topic=topic,
                message=event,
                key=key,
            )
        except Exception as e:
            self.logger.error("Failed to publish event to topic %s: %s", topic, str(e))
            raise

    async def send_event_to_kafka(self, event_data) -> bool | None:
        """
        Send an event to the record-events topic.
        :param event_data: Dictionary containing file processing details
        """
        try:
            await self._ensure_producer()

            formatted_event = {
                "eventType": event_data.get("eventType", EventTypes.NEW_RECORD.value),
                "timestamp": get_epoch_timestamp_in_ms(),
                "payload": {
                    "orgId": event_data.get("orgId"),
                    "recordId": event_data.get("recordId"),
                    "virtualRecordId": event_data.get("virtualRecordId", None),
                    "recordName": event_data.get("recordName"),
                    "recordType": event_data.get("recordType"),
                    "version": event_data.get("recordVersion", 0),
                    "connectorName": event_data.get("connectorName"),
                    "origin": event_data.get("origin"),
                    "extension": event_data.get("extension"),
                    "mimeType": event_data.get("mimeType"),
                    "body": event_data.get("body"),
                    "createdAtTimestamp": event_data.get("createdAtSourceTimestamp"),
                    "updatedAtTimestamp": event_data.get("modifiedAtSourceTimestamp"),
                    "sourceCreatedAtTimestamp": event_data.get(
                        "createdAtSourceTimestamp"
                    ),
                },
            }

            key = str(formatted_event["payload"]["recordId"])

            result = await self._producer.send_message(  # type: ignore
                topic="record-events",
                message=formatted_event,
                key=key,
            )

            if result:
                self.logger.info(
                    "Record %s successfully produced to record-events",
                    formatted_event["payload"]["recordId"],
                )

            return result

        except Exception as e:
            self.logger.error("Failed to send event: %s", str(e))
            return False

    async def stop_producer(self) -> None:
        """Stop the producer and clean up resources"""
        if self._producer:
            try:
                await self._producer.cleanup()
                self.logger.info("Messaging producer stopped successfully")
            except Exception as e:
                self.logger.error(f"Error stopping messaging producer: {str(e)}")

    async def __aenter__(self) -> "KafkaService":
        await self._ensure_producer()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop_producer()
