from logging import Logger
from typing import TYPE_CHECKING

from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisConfig,
    RedisStreamsConfig,
    get_message_broker_type,
    messaging_env,
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
from app.services.messaging.lanes.hash_router import build_lane_router
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.redis_streams.consumer import RedisStreamsConsumer
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.retry_manager import RetryManager
from app.services.messaging.scheduling.interface import (
    FairnessKeyExtractor,
    FairSchedulerConfig,
    WeightProvider,
)
from app.services.messaging.scheduling.key_extractors import CompositeKeyExtractor

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.services.resource_governor import ResourceGovernor


def _fair_scheduler_config_from_env() -> FairSchedulerConfig:
    """OSS default fair-scheduling config.

    Lives here rather than on ``MessagingEnvConfig`` so the generic messaging
    config module stays independent of the scheduling package; this factory is
    already the one place that knows how consumers are composed.
    """
    return FairSchedulerConfig(
        enabled=messaging_env.fair_scheduling_enabled,
        key_fields=messaging_env.fair_scheduling_key_fields,
        default_quantum=messaging_env.fair_scheduling_quantum,
        max_buffered_messages=messaging_env.fair_scheduling_max_buffer,
        max_per_entity_messages=messaging_env.fair_scheduling_max_per_entity,
        max_dwell_seconds=messaging_env.fair_scheduling_max_dwell_seconds,
        parallel_partitions=messaging_env.fair_scheduling_parallel_partitions,
    )


def lane_config_from_env() -> LaneConfig:
    """OSS default lane config. ``lane_count`` of 1 disables laning."""
    return LaneConfig(
        lane_count=messaging_env.fair_scheduling_lane_count,
        lane_key_field=messaging_env.fair_scheduling_lane_key_field,
        laned_topics=messaging_env.fair_scheduling_laned_topics,
    )


def lane_topics_for(topic: str, broker_type: MessageBrokerType | None = None) -> list[str]:
    """Every topic a consumer must subscribe to to receive all of ``topic``.

    With laning off this is just ``[topic]``, so callers can use it
    unconditionally.
    """
    lane_config = lane_config_from_env()
    if not lane_config.enabled or topic not in lane_config.laned_topics:
        return [topic]
    if broker_type is None:
        broker_type = get_message_broker_type()
    router = build_lane_router(lane_config, broker_type == MessageBrokerType.KAFKA)
    return router.lane_topics(topic)


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
        lane_config: LaneConfig | None = None,
    ) -> IMessagingProducer:
        """Create a messaging producer based on broker type.

        Args:
            lane_config: Optional lane routing config; defaults to the
                env-driven one. Laning is off unless ``lane_count > 1``.
        """
        if broker_type is None:
            broker_type = get_message_broker_type()

        producer: IMessagingProducer
        if broker_type == MessageBrokerType.KAFKA:
            if config is None:
                raise ValueError("Kafka producer config is required")
            if not isinstance(config, KafkaProducerConfig):
                raise TypeError(
                    f"Expected KafkaProducerConfig, got {type(config).__name__}"
                )
            producer = KafkaMessagingProducer(logger, config)
        else:
            if config is None:
                raise ValueError("Redis Streams config is required")
            if not isinstance(config, RedisStreamsConfig):
                raise TypeError(
                    f"Expected RedisStreamsConfig, got {type(config).__name__}"
                )
            producer = RedisStreamsProducer(logger, config)

        # Wrapping here is what routes all twelve existing publish sites
        # without editing any of them -- including the two that publish with
        # no key at all. With laning off the producer is returned unwrapped,
        # so the publish path stays exactly what it is today.
        lane_config = lane_config or lane_config_from_env()
        if not lane_config.enabled:
            return producer
        return LaneAwareProducer(
            logger,
            producer,
            build_lane_router(lane_config, broker_type == MessageBrokerType.KAFKA),
            lane_config,
        )

    @staticmethod
    def create_consumer(
        logger: Logger,
        config: KafkaConsumerConfig | RedisStreamsConfig | None = None,
        broker_type: MessageBrokerType | None = None,
        consumer_type: ConsumerType = ConsumerType.SIMPLE,
        retry_manager: RetryManager | None = None,
        producer: IMessagingProducer | None = None,
        concurrency_manager: DistributedConcurrencyManager | None = None,
        governor: "ResourceGovernor | None" = None,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
        fair_scheduler_config: FairSchedulerConfig | None = None,
        key_extractor: FairnessKeyExtractor | None = None,
        weight_provider: WeightProvider | None = None,
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
            fair_scheduler_config: Optional fair-scheduling config (INDEXING consumers only).
                      Defaults to the env-driven OSS config (``messaging_env.fair_scheduler_config()``)
                      when not provided -- the injection point for changing buffer sizing
                      without touching the consumer classes.
            key_extractor: Optional fairness-key extractor (INDEXING consumers only). Defaults
                      to a ``CompositeKeyExtractor`` over
                      ``fair_scheduler_config.key_fields`` (``orgId`` then
                      ``connectorId``: fair between customers, then between each
                      customer's individual connector syncs).
            weight_provider: Optional per-key DRR quantum provider (INDEXING consumers only),
                      for giving some keys a larger share than others. Defaults to a
                      flat quantum for every key.

        Returns:
            IMessagingConsumer instance
        """
        if broker_type is None:
            broker_type = get_message_broker_type()

        effective_fair_config = fair_scheduler_config or _fair_scheduler_config_from_env()
        effective_key_extractor = key_extractor or CompositeKeyExtractor(
            fields=effective_fair_config.key_fields
        )

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
                    fair_scheduler_config=effective_fair_config,
                    key_extractor=effective_key_extractor,
                    weight_provider=weight_provider,
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
                    fair_scheduler_config=effective_fair_config,
                    key_extractor=effective_key_extractor,
                    weight_provider=weight_provider,
                )
            return RedisStreamsConsumer(logger, config, retry_manager)
