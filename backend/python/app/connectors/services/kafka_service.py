import asyncio
import json
from typing import Any, Dict, List

from confluent_kafka import Producer

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    Connectors,
    EventTypes,
    OriginTypes,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KafkaService:
    def __init__(self, config: ConfigurationService, logger) -> None:
        self.config_service = config
        self.producer = None
        self.logger = logger

    def delivery_report(self, err, msg) -> None:
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

    async def send_event_to_kafka(self, event_data) -> bool | None:
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

    async def send_batched_events_to_kafka(self, events_data: List[Dict[str, Any]], org_id: str, record_type: str) -> bool | None:
        """
        Send a batch of related events to Kafka as a single event.
        Only use this for batches with size > 1.
        :param events_data: List of dictionaries containing event details
        :param org_id: Organization ID
        :param record_type: Type of records being sent
        """
        try:
            if not events_data or len(events_data) <= 1:
                return True

            kafka_config = await self.config_service.get_config(
                config_node_constants.KAFKA.value
            )
            if not isinstance(kafka_config, dict):
                raise ValueError("Kafka configuration must be a dictionary")

            # Format the records for the batch - only include necessary fields
            formatted_records = []
            for record in events_data:
                formatted_record = {
                    "recordId": record.get('_key'),
                    "recordName": record.get('recordName', ''),
                    "recordType": record_type,
                    "signedUrlRoute": record.get('signedUrlRoute'),
                    "mimeType": record.get('mimeType'),
                    "createdAtSourceTimestamp": record.get('sourceCreatedAtTimestamp'),
                    "modifiedAtSourceTimestamp": record.get('sourceLastModifiedTimestamp'),
                    "userId": record.get('userId'),
                    "channelId": record.get('channelId'),
                    "threadTs": record.get('threadTs'),
                    "text": record.get('text')
                }
                formatted_records.append(formatted_record)

            # Create the batch event with minimal required fields
            batch_event = {
                "eventType": EventTypes.NEW_RECORD.value,
                "timestamp": get_epoch_timestamp_in_ms(),
                "payload": {
                    "orgId": org_id,
                    "recordId": formatted_records[0].get('recordId'),
                    "recordName": f"Batch of {len(formatted_records)} {record_type} records",
                    "recordType": record_type,
                    "connectorName": Connectors.SLACK.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "mimeType": "application/slack_batch",
                    "body": {
                        "records": formatted_records,
                        "batchInfo": {
                            "batchSize": len(formatted_records),
                            "batchTimestamp": get_epoch_timestamp_in_ms(),
                            "recordType": record_type
                        }
                    },
                    "createdAtTimestamp": formatted_records[0].get('createdAtSourceTimestamp'),
                    "updatedAtTimestamp": formatted_records[-1].get('modifiedAtSourceTimestamp'),
                    "sourceCreatedAtTimestamp": formatted_records[0].get('createdAtSourceTimestamp')
                }
            }

            # Get broker configuration
            brokers = kafka_config.get("brokers", "localhost:9092")
            if isinstance(brokers, list):
                brokers = ",".join(brokers)
            elif isinstance(brokers, str) and brokers.startswith("[") and brokers.endswith("]"):
                brokers = brokers.strip("[]").replace("'", "").replace('"', "").strip()

            # Create producer with minimal configuration
            producer_config = {
                "bootstrap.servers": brokers,
                "client.id": kafka_config.get("client_id", "file-processor"),
            }

            # Send the batch event
            self.producer = Producer(producer_config)
            self.producer.produce(
                topic="record-events",
                key=str(batch_event["payload"]["recordId"]),
                value=json.dumps(batch_event),
                callback=self.delivery_report,
            )
            await asyncio.to_thread(self.producer.flush)

            self.logger.info(f"📨 Sent Kafka batch event with {len(formatted_records)} {record_type} records")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to send batch event to Kafka: %s", str(e))
            return False
