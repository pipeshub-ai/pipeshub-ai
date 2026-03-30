from logging import Logger
from typing import Union

from app.services.messaging.config import RedisStreamsConfig, get_message_broker_type
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
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer


class MessagingFactory:
    """Factory for creating messaging service instances"""

    @staticmethod
    def create_producer(
        logger: Logger,
        config: Union[KafkaProducerConfig, RedisStreamsConfig, None] = None,
        broker_type: Union[str, None] = None,
    ) -> IMessagingProducer:
        """Create a messaging producer based on broker type"""
        if broker_type is None:
            broker_type = get_message_broker_type()

        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka producer config is required")
            if not isinstance(config, KafkaProducerConfig):
                raise TypeError(
                    f"Expected KafkaProducerConfig, got {type(config).__name__}"
                )
            return KafkaMessagingProducer(logger, config)
        elif broker_type.lower() == "redis":
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            return RedisStreamsProducer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")

    @staticmethod
    def create_consumer(
        logger: Logger,
        config: Union[KafkaConsumerConfig, RedisStreamsConfig, None] = None,
        broker_type: Union[str, None] = None,
        consumer_type: str = "simple",
    ) -> IMessagingConsumer:
        """Create a messaging consumer based on broker type

        Args:
            logger: Logger instance
            config: Consumer configuration (Kafka or Redis Streams)
            broker_type: Type of message broker (None = auto-detect from env)
            consumer_type: Type of consumer to create:
                - "simple": Basic consumer (default)
                - "indexing": Dual-semaphore consumer for indexing pipeline

        Returns:
            IMessagingConsumer instance
        """
        if broker_type is None:
            broker_type = get_message_broker_type()

        if broker_type.lower() == "kafka":
            if config is None:
                raise ValueError("Kafka consumer config is required")
            if not isinstance(config, KafkaConsumerConfig):
                raise TypeError(
                    f"Expected KafkaConsumerConfig, got {type(config).__name__}"
                )
            if consumer_type == "indexing":
                return IndexingKafkaConsumer(logger, config)
            else:
                return KafkaMessagingConsumer(logger, config)
        elif broker_type.lower() == "redis":
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            if consumer_type == "indexing":
                return IndexingRedisStreamsConsumer(logger, config)
            else:
                return RedisStreamsConsumer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")
