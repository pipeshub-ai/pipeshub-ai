import ssl
from collections.abc import AsyncGenerator
from typing import Any

from app.config.constants.service import KafkaConfig as KafkaConstants, config_node_constants
from app.connectors.services.event_service import EventService
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer
from app.containers.query import QueryAppContainer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import (
    IndexingMessageHandler,
    MessageHandler,
    PipelineEvent,
    StreamMessage,
    Topic,
)
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.handlers.ai_config import AiConfigEventService
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.services.messaging.kafka.handlers.record import RecordEventHandler


class KafkaUtils:
    @staticmethod
    async def _create_base_consumer_config(
    app_container: ConnectorAppContainer | IndexingAppContainer | QueryAppContainer,
    client_id: str,
    group_id: str,
    topics: list[str]
) -> KafkaConsumerConfig:
        """Create a base Kafka consumer configuration."""
        config_service = app_container.config_service()
        kafka_config = await config_service.get_config(
            config_node_constants.KAFKA.value
        )

        if not kafka_config:
            raise ValueError("Kafka configuration not found")

        brokers = kafka_config.get('brokers') # type: ignore
        if not brokers:
            raise ValueError("Kafka brokers not found in configuration")

        return KafkaConsumerConfig(
            client_id=client_id,
            group_id=group_id,
            auto_offset_reset='earliest',
            enable_auto_commit=True,
            bootstrap_servers=brokers,
            topics=topics,
            ssl=kafka_config.get('ssl', False),
            sasl=kafka_config.get('sasl')
        )

    @staticmethod
    async def create_producer_config(app_container: ConnectorAppContainer) -> KafkaProducerConfig:
        """Create Kafka configuration for producer"""
        config_service = app_container.config_service()
        kafka_config = await config_service.get_config(
            config_node_constants.KAFKA.value
        )
        if not kafka_config:
            raise ValueError("Kafka configuration not found")

        return KafkaProducerConfig(
            bootstrap_servers=kafka_config["brokers"], # type: ignore
            client_id=KafkaConstants.CLIENT_ID_MESSAGING_PRODUCER.value,
            ssl=kafka_config.get('ssl', False),
            sasl=kafka_config.get('sasl')
        )

    @staticmethod
    async def create_entity_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for entity events"""
        return await KafkaUtils._create_base_consumer_config(
            app_container, KafkaConstants.CLIENT_ID_ENTITY_CONSUMER.value, KafkaConstants.GROUP_ID_ENTITY.value, [Topic.ENTITY_EVENTS.value]
        )


    @staticmethod
    async def create_sync_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for sync events"""
        return await KafkaUtils._create_base_consumer_config(
            app_container, KafkaConstants.CLIENT_ID_SYNC_CONSUMER.value, KafkaConstants.GROUP_ID_SYNC.value, [Topic.SYNC_EVENTS.value]
        )


    @staticmethod
    async def create_record_kafka_consumer_config(app_container: IndexingAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for record events"""
        return await KafkaUtils._create_base_consumer_config(
            app_container,
            KafkaConstants.CLIENT_ID_RECORDS_CONSUMER.value,
            KafkaConstants.GROUP_ID_RECORDS.value,
            [Topic.RECORD_EVENTS.value],
        )


    @staticmethod
    async def create_aiconfig_kafka_consumer_config(app_container: QueryAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for AI config events"""
        return await KafkaUtils._create_base_consumer_config(
            app_container, KafkaConstants.CLIENT_ID_AICONFIG_CONSUMER.value, KafkaConstants.GROUP_ID_AICONFIG.value, [Topic.ENTITY_EVENTS.value]
        )


    @staticmethod
    async def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        config: dict[str, Any] = {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics  # Include topics in the dictionary
        }

        # Add SSL/SASL configuration for AWS MSK
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

    @staticmethod
    async def create_entity_message_handler(
        app_container: ConnectorAppContainer, graph_provider: IGraphDBProvider
    ) -> MessageHandler:
        """Create a message handler for entity events"""
        logger = app_container.logger()

        entity_event_service = EntityEventService(
            logger=logger,
            graph_provider=graph_provider,
            app_container=app_container
        )

        async def handle_entity_message(message: StreamMessage) -> bool:
            try:
                event_type = message.eventType
                payload = message.payload

                if not event_type:
                    logger.error("Missing event_type in message")
                    return False

                if not payload:
                    logger.error("Missing payload in message")
                    return False

                logger.info(f"Processing entity event: {event_type}")
                return await entity_event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing entity message: {str(e)}", exc_info=True)
                return False

        return handle_entity_message

    @staticmethod
    async def create_record_message_handler(
        app_container: IndexingAppContainer,
    ) -> IndexingMessageHandler:
        """Create a message handler for record events.

        Returns an async generator function that yields PipelineEvent during processing.
        """
        logger = app_container.logger()
        event_processor = getattr(app_container, '_event_processor', None)
        if not event_processor:
            event_processor = await app_container.event_processor()
        config_service = app_container.config_service()
        record_event_service = RecordEventHandler(
            logger=logger,
            config_service=config_service,
            event_processor=event_processor,
        )

        async def handle_record_message(message: StreamMessage) -> AsyncGenerator[PipelineEvent, None]:
            try:
                event_type = message.eventType
                payload = message.payload

                if not event_type:
                    logger.error("Missing event_type in message")
                    return

                if not payload:
                    logger.error("Missing payload in message")
                    return

                logger.info(f"Processing record event: {event_type}")
                async for event in record_event_service.process_event(event_type, payload):
                    yield event

            except Exception as e:
                logger.error(f"Error processing record message: {str(e)}", exc_info=True)
                raise

        return handle_record_message

    @staticmethod
    async def create_sync_message_handler(
        app_container: ConnectorAppContainer, graph_provider: IGraphDBProvider
    ) -> MessageHandler:
        """Create a message handler for sync events"""
        logger = app_container.logger()

        async def handle_sync_message(message: StreamMessage) -> bool:
            try:
                event_type = message.eventType
                payload = message.payload

                if not event_type:
                    logger.error("Missing event_type in sync message")
                    return False

                connector = None
                if "." in event_type:
                    connector = event_type.split(".")[0].lower()
                else:
                    connector = payload.get("connector")

                if not connector:
                    logger.error("Missing connector in event_type or payload")
                    return False

                logger.info(f"Processing sync event: {event_type} for connector {connector}")

                event_service = EventService(
                    logger=logger,
                    graph_provider=graph_provider,
                    app_container=app_container,
                )
                logger.info(f"Processing sync event: {event_type} for {connector}")
                return await event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing sync message: {str(e)}", exc_info=True)
                return False

        return handle_sync_message

    @staticmethod
    async def create_aiconfig_message_handler(
        app_container: QueryAppContainer,
    ) -> MessageHandler:
        """Create a message handler for AI config events"""
        logger = app_container.logger()

        retrieval_service = await app_container.retrieval_service()

        aiconfig_event_service = AiConfigEventService(
            logger=logger,
            retrieval_service=retrieval_service,
        )

        async def handle_aiconfig_message(message: StreamMessage) -> bool:
            try:
                event_type = message.eventType
                payload = message.payload

                if not event_type:
                    logger.error("Missing event_type in AI config message")
                    return False

                # Only process AI configuration events
                if event_type not in ["llmConfigured", "embeddingModelConfigured"]:
                    logger.debug(f"Skipping non-AI config event: {event_type}")
                    return True

                logger.info(f"Processing AI config event: {event_type}")
                return await aiconfig_event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing AI config message: {str(e)}", exc_info=True)
                return False

        return handle_aiconfig_message
