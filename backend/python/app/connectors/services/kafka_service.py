import json
from typing import Dict

from aiokafka import AIOKafkaProducer

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import EventTypes
from app.config.constants.service import config_node_constants
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KafkaService:
    def __init__(self, config_service: ConfigurationService, logger) -> None:
        self.config_service = config_service
        self.producer = None
        self.logger = logger

    async def _ensure_producer(self) -> None:
        """Ensure producer is initialized and started"""
        if self.producer is None:
            try:
                kafka_config = await self.config_service.get_config(
                    config_node_constants.KAFKA.value
                )
                if not isinstance(kafka_config, dict):
                    raise ValueError("Kafka configuration must be a dictionary")

                brokers = kafka_config.get("brokers", "localhost:9092")
                if isinstance(brokers, list):
                    brokers = ",".join(brokers)
                elif (
                    isinstance(brokers, str)
                    and brokers.startswith("[")
                    and brokers.endswith("]")
                ):
                    brokers = brokers.strip("[]").replace("'", "").replace('"', "").strip()

                producer_config = {
                    "bootstrap_servers": brokers,  # aiokafka uses bootstrap_servers
                    "client_id": kafka_config.get("client_id", "file-processor"),
                    "request_timeout_ms": 30000,
                    "retry_backoff_ms": 100,
                    "enable_idempotence": True
                }

                self.producer = AIOKafkaProducer(**producer_config)
                await self.producer.start()
                self.logger.info("✅ Kafka producer initialized and started")

            except Exception as e:
                self.logger.error(f"❌ Failed to initialize Kafka producer: {str(e)}")
                raise

    async def publish_event(self, topic: str, event: Dict) -> bool:
        """
        Publish an event to a specified Kafka topic.
        :param topic: The Kafka topic to publish to
        :param event: Dictionary containing the event data
        :return: True if successful, False otherwise
        """
        try:
            # Ensure producer is ready
            await self._ensure_producer()

            # Convert event to JSON bytes for aiokafka
            message_value = json.dumps(event).encode('utf-8')

            # Use recordId from payload as key if available, otherwise use timestamp
            record_id = event.get("payload", {}).get("recordId")
            message_key = str(record_id).encode('utf-8') if record_id else str(event.get("timestamp", "")).encode('utf-8')

            # Send message and wait for delivery
            record_metadata = await self.producer.send_and_wait(
                topic=topic,
                key=message_key,
                value=message_value
            )

            # Log successful delivery
            self.logger.info(
                "✅ Event successfully published to %s [%s] at offset %s",
                record_metadata.topic,
                record_metadata.partition,
                record_metadata.offset
            )

            return True

        except Exception as e:
            self.logger.error("❌ Failed to publish event to topic %s: %s", topic, str(e))
            raise

    async def send_event_to_kafka(self, event_data) -> bool | None:
        """
        Send an event to Kafka asynchronously.
        :param event_data: Dictionary containing file processing details
        """
        try:
            # Ensure producer is ready
            await self._ensure_producer()

            # Standardize event format
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
                    "signedUrlRoute": event_data.get("signedUrlRoute"),
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

            # Convert to JSON bytes for aiokafka
            message_value = json.dumps(formatted_event).encode('utf-8')
            message_key = str(formatted_event["payload"]["recordId"]).encode('utf-8')

            # Send message and wait for delivery
            record_metadata = await self.producer.send_and_wait(
                topic="record-events",
                key=message_key,
                value=message_value
            )

            # Log successful delivery
            self.logger.info(
                "✅ Record %s successfully produced to %s [%s] at offset %s",
                formatted_event["payload"]["recordId"],
                record_metadata.topic,
                record_metadata.partition,
                record_metadata.offset
            )

            return True

        except Exception as e:
            self.logger.error("❌ Failed to send event to Kafka: %s", str(e))
            return False

    async def stop_producer(self) -> None:
        """Stop the Kafka producer and clean up resources"""
        if self.producer:
            try:
                await self.producer.stop()
                self.producer = None
                self.logger.info("✅ Kafka producer stopped successfully")
            except Exception as e:
                self.logger.error(f"❌ Error stopping Kafka producer: {str(e)}")

    async def __aenter__(self) -> "KafkaService":
        """Async context manager entry"""
        await self._ensure_producer()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.stop_producer()
