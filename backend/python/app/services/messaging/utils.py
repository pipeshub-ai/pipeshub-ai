"""Broker-agnostic messaging utilities.

Wraps the existing KafkaUtils with broker type detection,
creating appropriate configs for either Kafka or Redis Streams.
"""
from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer
from app.containers.query import QueryAppContainer
from app.services.messaging.config import (
    REDIS_STREAMS_MAXLEN,
    MessageBrokerType,
    RedisConfig,
    RedisStreamsConfig,
    Topic,
    get_message_broker_type,
)
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)

AppContainer = ConnectorAppContainer | IndexingAppContainer | QueryAppContainer


class MessagingUtils:
    """Broker-agnostic messaging utilities that create appropriate configs."""

    @staticmethod
    def get_broker_type() -> MessageBrokerType:
        return get_message_broker_type()

    @staticmethod
    async def _get_redis_config(app_container: AppContainer) -> RedisConfig:
        """Get Redis config from the configuration service."""
        config_service = app_container.config_service()
        return await config_service.get_redis_config()

    @staticmethod
    def _build_redis_streams_config(
        redis_config: RedisConfig,
        client_id: str,
        group_id: str,
        topics: list[str],
    ) -> RedisStreamsConfig:
        return RedisStreamsConfig(
            host=redis_config.host,
            port=redis_config.port,
            password=redis_config.password,
            db=redis_config.db,
            max_len=REDIS_STREAMS_MAXLEN,
            client_id=client_id,
            group_id=group_id,
            topics=topics,
        )

    @staticmethod
    async def _create_kafka_consumer_config(
        app_container: AppContainer,
        client_id: str,
        group_id: str,
        topics: list[str],
    ) -> KafkaConsumerConfig:
        config_service = app_container.config_service()
        kafka_config = await config_service.get_config(
            config_node_constants.KAFKA.value
        )
        if not kafka_config:
            raise ValueError("Kafka configuration not found")

        brokers = kafka_config.get("brokers")
        if not brokers:
            raise ValueError("Kafka brokers not found in configuration")

        return KafkaConsumerConfig(
            client_id=client_id,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            bootstrap_servers=brokers,
            topics=topics,
            ssl=kafka_config.get("ssl", False),
            sasl=kafka_config.get("sasl"),
        )

    @staticmethod
    async def create_consumer_config(
        app_container: AppContainer,
        client_id: str,
        group_id: str,
        topics: list[str],
    ) -> KafkaConsumerConfig | RedisStreamsConfig:
        """Create consumer config based on the configured broker type."""
        broker_type = get_message_broker_type()
        if broker_type == MessageBrokerType.KAFKA:
            return await MessagingUtils._create_kafka_consumer_config(
                app_container, client_id, group_id, topics
            )
        else:
            redis_config = await MessagingUtils._get_redis_config(app_container)
            return MessagingUtils._build_redis_streams_config(
                redis_config, client_id, group_id, topics
            )

    @staticmethod
    async def create_producer_config(
        app_container: ConnectorAppContainer,
    ) -> KafkaProducerConfig | RedisStreamsConfig:
        """Create producer config based on the configured broker type."""
        broker_type = get_message_broker_type()
        if broker_type == MessageBrokerType.KAFKA:
            config_service = app_container.config_service()
            kafka_config = await config_service.get_config(
                config_node_constants.KAFKA.value
            )
            if not kafka_config:
                raise ValueError("Kafka configuration not found")
            return KafkaProducerConfig(
                bootstrap_servers=kafka_config["brokers"],
                client_id="messaging_producer_client",
                ssl=kafka_config.get("ssl", False),
                sasl=kafka_config.get("sasl"),
            )
        else:
            redis_config = await MessagingUtils._get_redis_config(app_container)
            return MessagingUtils._build_redis_streams_config(
                redis_config, "messaging_producer_client", "", []
            )

    @staticmethod
    async def create_producer_config_from_service(
        config_service: ConfigurationService,
        client_id: str = "messaging_producer_client",
    ) -> KafkaProducerConfig | RedisStreamsConfig:
        """Create producer config using a ConfigurationService directly."""
        broker_type = get_message_broker_type()
        if broker_type == MessageBrokerType.KAFKA:
            kafka_config = await config_service.get_config(
                config_node_constants.KAFKA.value
            )
            brokers = kafka_config.get("brokers") or kafka_config.get("bootstrap_servers")
            if isinstance(brokers, str):
                brokers = [s.strip() for s in brokers.split(",")]
            return KafkaProducerConfig(
                bootstrap_servers=brokers,
                client_id=client_id,
                ssl=kafka_config.get("ssl", False),
                sasl=kafka_config.get("sasl"),
            )
        else:
            redis_config = await config_service.get_redis_config()
            return MessagingUtils._build_redis_streams_config(
                redis_config, client_id, "", []
            )

    @staticmethod
    async def create_entity_consumer_config(
        app_container: ConnectorAppContainer,
    ) -> KafkaConsumerConfig | RedisStreamsConfig:
        return await MessagingUtils.create_consumer_config(
            app_container,
            "entity_consumer_client",
            "entity_consumer_group",
            [Topic.ENTITY_EVENTS.value],
        )

    @staticmethod
    async def create_sync_consumer_config(
        app_container: ConnectorAppContainer,
    ) -> KafkaConsumerConfig | RedisStreamsConfig:
        return await MessagingUtils.create_consumer_config(
            app_container,
            "sync_consumer_client",
            "sync_consumer_group",
            [Topic.SYNC_EVENTS.value],
        )

    @staticmethod
    async def create_record_consumer_config(
        app_container: IndexingAppContainer,
    ) -> KafkaConsumerConfig | RedisStreamsConfig:
        return await MessagingUtils.create_consumer_config(
            app_container,
            "records_consumer_client",
            "records_consumer_group",
            [Topic.RECORD_EVENTS.value],
        )

    @staticmethod
    async def create_aiconfig_consumer_config(
        app_container: QueryAppContainer,
    ) -> KafkaConsumerConfig | RedisStreamsConfig:
        return await MessagingUtils.create_consumer_config(
            app_container,
            "aiconfig_consumer_client",
            "aiconfig_consumer_group",
            [Topic.ENTITY_EVENTS.value],
        )
