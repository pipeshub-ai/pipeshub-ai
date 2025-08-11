import json
from logging import Logger
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaProducer  # type: ignore

from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KafkaMessagingProducer(IMessagingProducer):
    """Kafka implementation of messaging producer"""

    def __init__(self,
                logger: Logger,
                kafka_config: KafkaProducerConfig) -> None:
        self.logger = logger
        self.producer: Optional[AIOKafkaProducer] = None
        self.kafka_config = kafka_config
        self.processed_messages: Dict[str, List[int]] = {}

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaProducerConfig) -> Dict[str, Any]:
        """Convert KafkaProducerConfig dataclass to dictionary format for aiokafka consumer"""
        return {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'client_id': kafka_config.client_id,
        }

    # implementing abstract methods from IMessagingProducer
    async def initialize(self) -> None:
        """Initialize the Kafka producer"""
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")
            producer_config = KafkaMessagingProducer.kafka_config_to_dict(self.kafka_config)

            self.producer = AIOKafkaProducer(**producer_config)
            await self.producer.start()
            self.logger.info(f"✅ Kafka producer initialized and started with client_id: {producer_config.get('client_id')}")

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Kafka producer: {str(e)}")
            raise

    # implementing abstract methods from IMessagingProducer
    async def cleanup(self) -> None:
        """Stop the Kafka producer and clean up resources"""
        if self.producer:
            try:
                await self.producer.stop()
                self.producer = None
                self.logger.info("✅ Kafka producer stopped successfully")
            except Exception as e:
                self.logger.error(f"❌ Error stopping Kafka producer: {str(e)}")

    # implementing abstract methods from IMessagingProducer
    async def start(self) -> None:
        """Start the Kafka producer"""
        if self.producer is None:
            await self.initialize()

    # implementing abstract methods from IMessagingProducer
    async def stop(self) -> None:
        """Stop the Kafka producer"""
        if self.producer:
            await self.stop()
            self.logger.info("✅ Kafka producer stopped successfully")

    # implementing abstract methods from IMessagingProducer
    async def send_message(
        self,
        topic: str,
        message: Dict[str, Any],
        key: Optional[str] = None
    ) -> bool:
        """Send a message to a Kafka topic"""
        try:
            if self.producer is None:
                await self.initialize()

            message_value = json.dumps(message).encode('utf-8')
            message_key = key.encode('utf-8') if key else None

            record_metadata = await self.producer.send_and_wait( # type: ignore
                topic=topic,
                key=message_key,
                value=message_value
            )

            self.logger.info(
                "✅ Message successfully produced to %s [%s] at offset %s",
                record_metadata.topic,
                record_metadata.partition,
                record_metadata.offset
            )

            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to send message to Kafka: {str(e)}")
            return False

    # implementing abstract methods from IMessagingProducer
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        key: Optional[str] = None
    ) -> bool:
        """Send an event message with standardized format"""
        try:
            # Prepare the message
            message = {
                'eventType': event_type,
                'payload': payload,
                'timestamp': get_epoch_timestamp_in_ms()
            }

            # Send the message to sync-events topic using aiokafka
            await self.send_message(
                topic=topic,
                message=message,
                key=key
            )

            self.logger.info(f"Successfully sent event with type: {event_type} to topic: {topic}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending sync event: {str(e)}")
            return False
