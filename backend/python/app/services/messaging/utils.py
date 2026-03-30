"""Broker-agnostic messaging utilities.

Wraps the existing KafkaUtils with broker type detection,
creating appropriate configs for either Kafka or Redis Streams.
"""
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, Union

from app.config.constants.service import config_node_constants
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer
from app.containers.query import QueryAppContainer
from app.services.messaging.config import RedisStreamsConfig, get_message_broker_type
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)


class MessagingUtils:
    """Broker-agnostic messaging utilities that create appropriate configs."""

    @staticmethod
    def get_broker_type() -> str:
        return get_message_broker_type()

    @staticmethod
    async def _get_redis_config(
        app_container: Union[ConnectorAppContainer, IndexingAppContainer, QueryAppContainer],
    ) -> dict:
        """Get Redis config from the configuration service."""
        config_service = app_container.config_service()
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        if not redis_config:
            # Fall back to environment variables
            return {
                "host": os.getenv("REDIS_HOST", "localhost"),
                "port": int(os.getenv("REDIS_PORT", "6379")),
                "password": os.getenv("REDIS_PASSWORD") or None,
                "db": int(os.getenv("REDIS_DB", "0")),
            }
        return redis_config

    @staticmethod
    def _build_redis_streams_config(
        redis_config: dict,
        client_id: str,
        group_id: str,
        topics: list[str],
    ) -> RedisStreamsConfig:
        return RedisStreamsConfig(
            host=redis_config.get("host", "localhost"),
            port=int(redis_config.get("port", 6379)),
            password=redis_config.get("password"),
            db=int(redis_config.get("db", 0)),
            max_len=int(os.getenv("REDIS_STREAMS_MAXLEN", "10000")),
            client_id=client_id,
            group_id=group_id,
            topics=topics,
        )

    @staticmethod
    async def _create_kafka_consumer_config(
        app_container: Union[ConnectorAppContainer, IndexingAppContainer, QueryAppContainer],
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
        app_container: Union[ConnectorAppContainer, IndexingAppContainer, QueryAppContainer],
        client_id: str,
        group_id: str,
        topics: list[str],
    ) -> Union[KafkaConsumerConfig, RedisStreamsConfig]:
        """Create consumer config based on the configured broker type."""
        broker_type = get_message_broker_type()
        if broker_type == "kafka":
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
    ) -> Union[KafkaProducerConfig, RedisStreamsConfig]:
        """Create producer config based on the configured broker type."""
        broker_type = get_message_broker_type()
        if broker_type == "kafka":
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

    # Convenience methods that mirror KafkaUtils for specific consumer configs
    @staticmethod
    async def create_entity_consumer_config(
        app_container: ConnectorAppContainer,
    ) -> Union[KafkaConsumerConfig, RedisStreamsConfig]:
        return await MessagingUtils.create_consumer_config(
            app_container, "entity_consumer_client", "entity_consumer_group", ["entity-events"]
        )

    @staticmethod
    async def create_sync_consumer_config(
        app_container: ConnectorAppContainer,
    ) -> Union[KafkaConsumerConfig, RedisStreamsConfig]:
        return await MessagingUtils.create_consumer_config(
            app_container, "sync_consumer_client", "sync_consumer_group", ["sync-events"]
        )

    @staticmethod
    async def create_record_consumer_config(
        app_container: IndexingAppContainer,
    ) -> Union[KafkaConsumerConfig, RedisStreamsConfig]:
        return await MessagingUtils.create_consumer_config(
            app_container, "records_consumer_client", "records_consumer_group", ["record-events"]
        )

    @staticmethod
    async def create_aiconfig_consumer_config(
        app_container: QueryAppContainer,
    ) -> Union[KafkaConsumerConfig, RedisStreamsConfig]:
        return await MessagingUtils.create_consumer_config(
            app_container, "aiconfig_consumer_client", "aiconfig_consumer_group", ["entity-events"]
        )
