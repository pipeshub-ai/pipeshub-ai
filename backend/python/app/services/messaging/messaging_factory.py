from logging import Logger

from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.kafka.rate_limiter.rate_limiter import RateLimiter


class MessagingFactory:
    """Factory for creating messaging service instances"""

    @staticmethod
    def create_producer(
        logger: Logger,
        config: KafkaProducerConfig | None = None,
        broker_type: str = "kafka",
    ) -> IMessagingProducer:
        """Create a messaging producer"""
        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka producer config is required")
            return KafkaMessagingProducer(logger, config)
        raise ValueError(f"Unsupported broker type: {broker_type}")

    @staticmethod
    def create_consumer(
        logger: Logger,
        rate_limiter: RateLimiter | None = None,
        config: KafkaConsumerConfig | None = None,
        broker_type: str = "kafka",
    ) -> IMessagingConsumer:
        """Create a messaging consumer"""
        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka consumer config is required")
            return KafkaMessagingConsumer(logger, config, rate_limiter)
        raise ValueError(f"Unsupported broker type: {broker_type}")
