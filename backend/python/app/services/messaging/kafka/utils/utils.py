from typing import Any, Awaitable, Callable, Dict, List, Union

from app.config.constants.arangodb import Connectors
from app.config.constants.service import config_node_constants
from app.connectors.services.event_service import EventService
from app.connectors.sources.google.gmail.services.event_service.event_service import (
    GmailEventService,
)
from app.connectors.sources.google.google_drive.services.event_service.event_service import (
    GoogleDriveEventService,
)
from app.containers.connector import ConnectorAppContainer
from app.containers.indexing import IndexingAppContainer
from app.containers.query import QueryAppContainer
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
    app_container: Union[ConnectorAppContainer, IndexingAppContainer, QueryAppContainer],
    client_id: str,
    group_id: str,
    topics: List[str]
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
            topics=topics
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
            client_id="messaging_producer_client"
        )

    @staticmethod
    async def create_entity_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for entity events"""
        return await KafkaUtils._create_base_consumer_config(app_container, "entity_consumer_client", "entity_consumer_group", ["entity-events"])


    @staticmethod
    async def create_sync_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for sync events"""
        return await KafkaUtils._create_base_consumer_config(app_container, "sync_consumer_client", "sync_consumer_group", ["sync-events"])


    @staticmethod
    async def create_record_kafka_consumer_config(app_container: IndexingAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for record events"""
        return await KafkaUtils._create_base_consumer_config(app_container, "records_consumer_client", "records_consumer_group", ["record-events"])


    @staticmethod
    async def create_aiconfig_kafka_consumer_config(app_container: QueryAppContainer) -> KafkaConsumerConfig:
        """Create Kafka configuration for AI config events"""
        return await KafkaUtils._create_base_consumer_config(app_container, "aiconfig_consumer_client", "aiconfig_consumer_group", ["entity-events"])


    @staticmethod
    async def kafka_config_to_dict(kafka_config: KafkaConsumerConfig) -> Dict[str, Any]:
        """Convert KafkaConsumerConfig dataclass to dictionary format for aiokafka consumer"""
        return {
            'bootstrap_servers': ",".join(kafka_config.bootstrap_servers),
            'group_id': kafka_config.group_id,
            'auto_offset_reset': kafka_config.auto_offset_reset,
            'enable_auto_commit': kafka_config.enable_auto_commit,
            'client_id': kafka_config.client_id,
            'topics': kafka_config.topics  # Include topics in the dictionary
        }

    @staticmethod
    async def create_entity_message_handler(app_container: ConnectorAppContainer) -> Callable[[Dict[str, Any]], Awaitable[bool]]:
        """Create a message handler for entity events"""
        logger = app_container.logger()
        arango_service = await app_container.arango_service()

        # Create the entity event service
        entity_event_service = EntityEventService(
            logger=logger,
            arango_service=arango_service,
            app_container=app_container
        )

        async def handle_entity_message(message: Dict[str, Any]) -> bool:
            """Handle incoming entity messages"""
            try:
                if message is None:
                    logger.warning("Received a None message, likely during shutdown. Skipping.")
                    return True
                event_type = message.get("eventType")
                payload = message.get("payload")

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
    async def create_record_message_handler(app_container: IndexingAppContainer) -> Callable[[Dict[str, Any]], Awaitable[bool]]:
        """Create a message handler for record events"""
        logger = app_container.logger()
        event_processor = await app_container.event_processor()
        redis_scheduler = await app_container.redis_scheduler()
        config_service =  app_container.config_service()
        # Create the entity event service
        record_event_service = RecordEventHandler(
            logger=logger,
            config_service=config_service,
            event_processor=event_processor,
            scheduler=redis_scheduler
        )

        async def handle_record_message(message: Dict[str, Any]) -> bool:
            """Handle incoming record messages"""
            try:

                if message is None:
                    logger.warning("Received a None message, likely during shutdown. Skipping.")
                    return True

                event_type = message.get("eventType")
                payload = message.get("payload")

                if not event_type:
                    logger.error("Missing event_type in message")
                    return False

                if not payload:
                    logger.error("Missing payload in message")
                    return False

                logger.info(f"Processing record event: {event_type}")
                return await record_event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing record message: {str(e)}", exc_info=True)
                return False

        return handle_record_message

    @staticmethod
    async def create_sync_message_handler(app_container: ConnectorAppContainer) -> Callable[[Dict[str, Any]], Awaitable[bool]]:
        """Create a message handler for sync events"""
        logger = app_container.logger()
        arango_service = await app_container.arango_service()

        async def handle_sync_message(message: Dict[str, Any]) -> bool:
            """Handle incoming sync messages"""
            try:

                if message is None:
                    logger.warning("Received a None message, likely during shutdown. Skipping.")
                    return True
                event_type = message.get("eventType")
                payload = message.get("payload", {})
                connector = payload.get("connector")

                sync_tasks_registry = getattr(app_container, 'sync_tasks_registry', {})
                # TODO: Handle connectorPublicUrlChanged correctly
                if event_type == "connectorPublicUrlChanged":
                    logger.info(f"Processing connectorPublicUrlChanged event: {payload}")
                    drive_sync_tasks = sync_tasks_registry.get('drive')
                    if not drive_sync_tasks:
                        logger.error("Drive sync tasks not found in registry")
                        return False

                    # Handle drive sync events
                    google_drive_event_service = GoogleDriveEventService(
                        logger=logger,
                        sync_tasks=drive_sync_tasks,
                        arango_service=arango_service,
                    )
                    logger.info(f"Processing sync event: {event_type} for GOOGLE DRIVE")
                    return await google_drive_event_service.process_event(event_type, payload)
                    return True

                if not event_type:
                    logger.error("Missing event_type in sync message")
                    return False

                if not connector:
                    logger.error("Missing connector in sync message")
                    return False

                logger.info(f"Processing sync event: {event_type} for connector {connector}")


                # Route sync events to appropriate connectors
                if connector.lower() == Connectors.GOOGLE_MAIL.value.lower():
                    # Create the sync event service
                    gmail_sync_tasks = sync_tasks_registry.get('gmail')
                    if not gmail_sync_tasks:
                        logger.error("Gmail sync tasks not found in registry")
                        return False


                    gmail_event_service = GmailEventService(
                        logger=logger,
                        sync_tasks=gmail_sync_tasks,
                        arango_service=arango_service,
                    )
                    logger.info(f"Processing sync event: {event_type} for GMAIL")
                    return await gmail_event_service.process_event(event_type, payload)

                elif connector.lower() == Connectors.GOOGLE_DRIVE.value.lower():
                    drive_sync_tasks = sync_tasks_registry.get('drive')
                    if not drive_sync_tasks:
                        logger.error("Drive sync tasks not found in registry")
                        return False

                    # Handle drive sync events
                    google_drive_event_service = GoogleDriveEventService(
                        logger=logger,
                        sync_tasks=drive_sync_tasks,
                        arango_service=arango_service,
                    )
                    logger.info(f"Processing sync event: {event_type} for GOOGLE DRIVE")
                    return await google_drive_event_service.process_event(event_type, payload)

                else:
                    event_service = EventService(
                        logger=logger,
                        arango_service=arango_service,
                        app_container=app_container,
                    )

                    logger.info(f"Processing sync event: {event_type} for {connector}")
                    return await event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing sync message: {str(e)}", exc_info=True)
                return False

        return handle_sync_message

    @staticmethod
    async def create_aiconfig_message_handler(app_container: QueryAppContainer) -> Callable[[Dict[str, Any]], Awaitable[bool]]:
        """Create a message handler for AI config events"""
        logger = app_container.logger()

        # get the retrieval_service from your container
        retrieval_service = await app_container.retrieval_service()

        # Create the AI config event service
        aiconfig_event_service = AiConfigEventService(
            logger=logger,
            retrieval_service=retrieval_service,
        )

        async def handle_aiconfig_message(message: Dict[str, Any]) -> bool:
            """Handle incoming AI config messages"""
            try:

                if message is None:
                    logger.warning("Received a None message, likely during shutdown. Skipping.")
                    return True

                event_type = message.get("eventType")
                payload = message.get("payload", {})

                if not event_type:
                    logger.error("Missing event_type in AI config message")
                    return False

                # Only process AI configuration events
                if event_type not in ["llmConfigured", "embeddingModelConfigured"]:
                    logger.debug(f"Skipping non-AI config event: {event_type}")
                    return True  # Return True to acknowledge the message without processing

                logger.info(f"Processing AI config event: {event_type}")
                return await aiconfig_event_service.process_event(event_type, payload)

            except Exception as e:
                logger.error(f"Error processing AI config message: {str(e)}", exc_info=True)
                return False

        return handle_aiconfig_message
