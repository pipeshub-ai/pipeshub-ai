import asyncio
import json
import ssl
from logging import Logger
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaProducer  # type: ignore

from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.utils.request_context import inject_envelope
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
        self._producer_lock = asyncio.Lock()

    @staticmethod
    def kafka_config_to_dict(kafka_config: KafkaProducerConfig) -> Dict[str, Any]:
        """Convert KafkaProducerConfig dataclass to dictionary format for aiokafka producer"""
        config: Dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'client_id': kafka_config.client_id,
        }

        # Add SSL/SASL configuration
        if kafka_config.ssl:
            config["ssl_context"] = ssl.create_default_context()
            sasl_config = kafka_config.sasl or {}
            if sasl_config.get("username"):
                config["security_protocol"] = "SASL_SSL"
                config["sasl_mechanism"] = sasl_config.get("mechanism", "SCRAM-SHA-512").upper()
                config["sasl_plain_username"] = sasl_config["username"]
                config["sasl_plain_password"] = sasl_config["password"]
            else:
                config["security_protocol"] = "SSL"

        return config

    # implementing abstract methods from IMessagingProducer
    async def initialize(self) -> None:
        """Initialize the Kafka producer with race condition protection"""
        if self.producer is not None:
            return  # Fast path: already initialized

        async with self._producer_lock:  # Serialize initialization
            # Double-check after acquiring lock
            if self.producer is not None:
                return

            producer = None
            try:
                if not self.kafka_config:
                    raise ValueError("Kafka configuration is not valid")

                producer_config = KafkaMessagingProducer.kafka_config_to_dict(self.kafka_config)

                producer = AIOKafkaProducer(**producer_config)
                await producer.start()

                # Only assign after successful start
                self.producer = producer
                self.logger.info(f"✅ Kafka producer initialized and started with client_id: {producer_config.get('client_id')}")

            except Exception as e:
                if producer is not None:
                    try:
                        await producer.stop()
                    except Exception as e:
                        self.logger.info(f"⚠️ Failed to stop Kafka producer during error handling: {str(e)}")
                self.producer = None
                self.logger.error(f"❌ Failed to initialize Kafka producer: {str(e)}")
                raise

    # implementing abstract methods from IMessagingProducer
    async def cleanup(self) -> None:
        """Stop the Kafka producer and clean up resources"""
        async with self._producer_lock:
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

            message = inject_envelope(message)
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
            raise

    async def send_messages(
        self,
        topic: str,
        messages: List[tuple[Optional[str], Dict[str, Any]]],
    ) -> List[bool]:
        """Publish many messages, returning per-message success in input order.

        Enqueues everything first and awaits the acks together. send_and_wait() blocks
        on the broker ack before the next record is enqueued, and aiokafka only keeps
        one batch per partition in flight at a time, so awaiting per record means
        nothing ever accumulates and each batch carries a single record. Handing the
        accumulator the whole set first is what lets it coalesce them.
        """
        if not messages:
            return []

        if self.producer is None:
            await self.initialize()

        # Enqueue everything before awaiting any ack, so the accumulator has the
        # whole set to coalesce. One record failing to enqueue must not sink the
        # rest, so each slot is tracked independently.
        futures: List[Optional[Any]] = []
        first_error: Optional[BaseException] = None
        for key, message in messages:
            try:
                envelope = inject_envelope(message)
                message_value = json.dumps(envelope).encode('utf-8')
                message_key = key.encode('utf-8') if key else None
                # send() returns a future once the record is buffered; it only blocks
                # when the accumulator is full, which is the backpressure we want.
                futures.append(
                    await self.producer.send(  # type: ignore
                        topic=topic,
                        key=message_key,
                        value=message_value,
                    )
                )
            except Exception as e:
                futures.append(None)
                first_error = first_error or e

        settled = iter(
            await asyncio.gather(*(f for f in futures if f is not None), return_exceptions=True)
        )

        acked: List[bool] = []
        for future in futures:
            if future is None:
                acked.append(False)
                continue
            result = next(settled)
            if isinstance(result, BaseException):
                acked.append(False)
                first_error = first_error or result
            else:
                acked.append(True)

        failed = len(acked) - sum(acked)
        if failed:
            self.logger.error(
                "❌ %s/%s messages failed to produce to %s; first error: %s",
                failed, len(acked), topic, first_error,
            )
        else:
            self.logger.info("✅ Produced %s messages to %s", len(acked), topic)
        return acked

    # implementing abstract methods from IMessagingProducer
    async def send_event(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        key: Optional[str] = None
    ) -> bool:
        """Send an event message with standardized format"""
        message = {
            'eventType': event_type,
            'payload': payload,
            'timestamp': get_epoch_timestamp_in_ms()
        }

        await self.send_message(
            topic=topic,
            message=message,
            key=key
        )

        self.logger.info(f"Successfully sent event with type: {event_type} to topic: {topic}")
        return True
