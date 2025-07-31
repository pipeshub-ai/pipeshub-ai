import json
from typing import Any, Dict, Optional, List

from aiokafka import AIOKafkaProducer # type: ignore

from app.services.messaging.interface.producer import IMessagingProducer
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from logging import Logger
from app.services.messaging.kafka.config.kafka_config import KafkaConfig

class KafkaMessagingProducer(IMessagingProducer):
    """Kafka implementation of messaging producer"""
    
    def __init__(self,
                logger: Logger,
                kafka_config: KafkaConfig) -> None:
        self.logger = logger
        self.producer: Optional[AIOKafkaProducer] = None
        self.kafka_config = kafka_config
        self.processed_messages: Dict[str, List[int]] = {}
    
    # implementing abstract methods from IMessagingProducer
    async def initialize(self) -> None:
        """Initialize the Kafka producer"""
        try:
            if not self.kafka_config:
                raise ValueError("Kafka configuration is not valid")
            
            brokers = self.kafka_config.get("bootstrap_servers", "localhost:9092")
            if isinstance(brokers, list):
                brokers = ",".join(brokers)
            elif (
                isinstance(brokers, str)
                and brokers.startswith("[")
                and brokers.endswith("]")
            ):
                brokers = brokers.strip("[]").replace("'", "").replace('"', "").strip()

            producer_config = {
                "bootstrap_servers": brokers,
                "client_id": self.kafka_config.get("client_id")
            }

            self.producer = AIOKafkaProducer(**producer_config)
            await self.producer.start()
            self.logger.info(f"✅ Kafka producer initialized and started with client_id: {self.kafka_config.get('client_id')}")

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
            
            record_metadata = await self.producer.send_and_wait(
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

            # Convert message to JSON string and encode to bytes
            message_bytes = json.dumps(message).encode('utf-8')

            # Send the message to sync-events topic using aiokafka
            await self.producer.send_message(
                topic=topic,
                message=message_bytes,
                key=key
            )

            self.logger.info(f"Successfully sent event with type: {event_type} to topic: {topic}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending sync event: {str(e)}")
            return False