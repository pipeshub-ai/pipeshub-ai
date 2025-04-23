import asyncio
import json

from confluent_kafka import Producer

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import EventTypes
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KafkaService:
    def __init__(self, config: ConfigurationService, logger):
        self.config_service = config
        self.producer = None
        self.logger = logger

    def delivery_report(self, err, msg):
        """Delivery report for produced messages."""
        if err is not None:
            self.logger.error("❌ Delivery failed for record %s: %s", msg.key(), err)
        else:
            self.logger.info(
                "✅ Record %s successfully produced to %s [%s]",
                msg.key(),
                msg.topic(),
                msg.partition(),
            )

    async def send_event_to_kafka(self, event_data):
        """
        Send an event to Kafka.
        :param event_data: Dictionary containing file processing details
        """
        try:
            kafka_config = await self.config_service.get_config(
                config_node_constants.KAFKA.value
            )
            if not isinstance(kafka_config, dict):
                raise ValueError("Kafka configuration must be a dictionary")

            # Standardize event format
            formatted_event = {
                "eventType": event_data.get("eventType", EventTypes.NEW_RECORD.value),
                "timestamp": get_epoch_timestamp_in_ms(),
                "payload": {
                    "orgId": event_data.get("orgId"),
                    "recordId": event_data.get("recordId"),
                    "recordName": event_data.get("recordName"),
                    "recordType": event_data.get("recordType"),
                    "version": event_data.get("recordVersion", 0),
                    "signedUrlRoute": event_data.get("signedUrlRoute"),
                    "metadataRoute": event_data.get("metadataRoute"),
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

            # self.logger.info(f"Formatted event: {formatted_event}")
            # self.logger.info(f"kafka config: {kafka_config}")
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
                "bootstrap.servers": brokers,
                "client.id": kafka_config.get("client_id", "file-processor"),
            }
            self.producer = Producer(producer_config)
            self.producer.produce(
                topic="record-events",
                key=str(formatted_event["payload"]["recordId"]),
                # Properly serialize to JSON
                value=json.dumps(formatted_event),
                callback=self.delivery_report,
            )
            await asyncio.to_thread(self.producer.flush)
            return True
        except Exception as e:
            self.logger.error("❌ Failed to send event to Kafka: %s", str(e))
            return False
