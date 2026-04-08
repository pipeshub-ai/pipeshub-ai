from logging import Logger

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisStreamsConfig,
    get_message_broker_type,
)
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
        config: KafkaProducerConfig | RedisStreamsConfig | None = None,
        broker_type: MessageBrokerType | None = None,
    ) -> IMessagingProducer:
        """Create a messaging producer based on broker type"""
        if broker_type is None:
            broker_type = get_message_broker_type()

        if broker_type == MessageBrokerType.KAFKA:
            if config is None:
                raise ValueError("Kafka producer config is required")
            if not isinstance(config, KafkaProducerConfig):
                raise TypeError(
                    f"Expected KafkaProducerConfig, got {type(config).__name__}"
                )
            return KafkaMessagingProducer(logger, config)
        else:
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            return RedisStreamsProducer(logger, config)

    @staticmethod
    def create_consumer(
        logger: Logger,
        config: KafkaConsumerConfig | RedisStreamsConfig | None = None,
        broker_type: MessageBrokerType | None = None,
        consumer_type: ConsumerType = ConsumerType.SIMPLE,
    ) -> IMessagingConsumer:
        """Create a messaging consumer based on broker type"""
        if broker_type is None:
            broker_type = get_message_broker_type()

        if broker_type == MessageBrokerType.KAFKA:
            if config is None:
                raise ValueError("Kafka consumer config is required")
            if not isinstance(config, KafkaConsumerConfig):
                raise TypeError(
                    f"Expected KafkaConsumerConfig, got {type(config).__name__}"
                )
            if consumer_type == ConsumerType.INDEXING:
                return IndexingKafkaConsumer(logger, config)
            return KafkaMessagingConsumer(logger, config)
        else:
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            if consumer_type == ConsumerType.INDEXING:
                return IndexingRedisStreamsConsumer(logger, config)
            return RedisStreamsConsumer(logger, config)
