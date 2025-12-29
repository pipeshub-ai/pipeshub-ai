from logging import Logger
from typing import Union

from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.consumer.indexing_consumer import (
    IndexingKafkaConsumer,
)
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer


class MessagingFactory:
    """Factory for creating messaging service instances"""

    @staticmethod
    def create_producer(
        logger: Logger,
        config: Union[KafkaProducerConfig, None] = None,
        broker_type: str = "kafka",
    ) -> IMessagingProducer:
        """Create a messaging producer"""
        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka producer config is required")
            return KafkaMessagingProducer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")

    @staticmethod
    def create_consumer(
        logger: Logger,
        config: Union[KafkaConsumerConfig, None] = None,
        broker_type: str = "kafka",
        consumer_type: str = "simple",
    ) -> IMessagingConsumer:
        """Create a messaging consumer

        Args:
            logger: Logger instance
            config: Kafka consumer configuration
            broker_type: Type of message broker (currently only "kafka" supported)
            consumer_type: Type of consumer to create:
                - "simple": Basic consumer with single semaphore (default)
                - "indexing": Dual-semaphore consumer for indexing pipeline

        Returns:
            IMessagingConsumer instance
        """
        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka consumer config is required")

            if consumer_type == "indexing":
                return IndexingKafkaConsumer(logger, config)
            else:
                return KafkaMessagingConsumer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")
