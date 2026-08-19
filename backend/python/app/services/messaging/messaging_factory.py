from logging import Logger
from typing import TYPE_CHECKING, Optional

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisConfig,
    RedisStreamsConfig,
    get_message_broker_type,
)
from app.services.messaging.distributed_concurrency import (
    DistributedConcurrencyManager,
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
from app.services.messaging.retry_manager import RetryManager

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.services.resource_governor import ResourceGovernor


class MessagingFactory:
    """Factory for creating messaging service instances.

    For consumers that implement failure-based retry tracking (KafkaMessagingConsumer,
    RedisStreamsConsumer when configured with a RetryManager), a RetryManager can be
    provided for persistent retry tracking across restarts. If not provided, consumers
    may operate with different or no retry tracking depending on their implementation.
    """

    @staticmethod
    def create_retry_manager(
        logger: Logger,
        redis_config: RedisConfig,
        ttl_seconds: int = RetryManager.DEFAULT_TTL_SECONDS,
    ) -> RetryManager:
        """Create a RetryManager for persistent failure retry tracking.

        This RetryManager stores retry counts in Redis and can be used by both Kafka
        and Redis Streams consumers that implement failure-based retry logic.

        Args:
            logger: Logger instance
            redis_config: Redis configuration for retry tracking storage
            ttl_seconds: TTL for retry keys (default: 24 hours)

        Returns:
            RetryManager instance (must call initialize() before use)
        """
        return RetryManager(
            logger=logger,
            redis_config=redis_config,
            ttl_seconds=ttl_seconds,
        )

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
        retry_manager: Optional[RetryManager] = None,
        producer: Optional[IMessagingProducer] = None,
        concurrency_manager: Optional[DistributedConcurrencyManager] = None,
        governor: "ResourceGovernor | None" = None,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
    ) -> IMessagingConsumer:
        """Create a messaging consumer based on broker type.

        Args:
            logger: Logger instance
            config: Consumer configuration (Kafka or Redis Streams)
            broker_type: Message broker type (auto-detected if None)
            consumer_type: Consumer type (SIMPLE or INDEXING)
            retry_manager: Optional RetryManager for persistent failure retry tracking.
                           If provided, the consumer will use failure-based retry semantics.
                           Otherwise, it may use broker-native retry mechanisms.
            producer: Optional producer for re-queueing failed messages (INDEXING consumers only).
                      Failed messages are published back to the same topic/stream for retry.
            governor: Optional ResourceGovernor (INDEXING consumers only). When provided,
                      the consumer routes parsing/indexing admission through its adaptive
                      gates instead of the static MAX_CONCURRENT_* semaphores.
            backpressure_coordinator: Optional BackpressureCoordinator (INDEXING consumers
                      only). When provided, the consumer pauses reading new messages
                      whenever a downstream service (parsing/docling/embedding) it shares
                      the coordinator with last signalled 429+Retry-After.

        Returns:
            IMessagingConsumer instance
        """
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
                return IndexingKafkaConsumer(
                    logger,
                    config,
                    retry_manager,
                    producer,
                    concurrency_manager,
                    governor,
                    backpressure_coordinator,
                )
            return KafkaMessagingConsumer(logger, config, retry_manager)
        else:
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            if consumer_type == ConsumerType.INDEXING:
                return IndexingRedisStreamsConsumer(
                    logger,
                    config,
                    retry_manager,
                    producer,
                    concurrency_manager,
                    governor,
                    backpressure_coordinator,
                )
            return RedisStreamsConsumer(logger, config, retry_manager)
