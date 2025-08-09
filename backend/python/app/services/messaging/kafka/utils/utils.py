from typing import Any, Awaitable, Callable, Dict

from app.config.constants.service import config_node_constants
from app.config.utils.named_constants.arangodb_constants import Connectors
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

async def create_entity_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
    """Create Kafka configuration for entity events"""
    config_service = app_container.config_service()
    kafka_config = await config_service.get_config(
        config_node_constants.KAFKA.value
    )
    brokers = kafka_config['brokers'] # type: ignore

    return KafkaConsumerConfig(
        client_id='entity_consumer_client',
        group_id='entity_consumer_group',
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        bootstrap_servers=brokers, # type: ignore
        topics=['entity-events']  # Entity-specific topics
    )

async def create_sync_kafka_consumer_config(app_container: ConnectorAppContainer) -> KafkaConsumerConfig:
    """Create Kafka configuration for sync events"""
    config_service = app_container.config_service()
    kafka_config = await config_service.get_config(
        config_node_constants.KAFKA.value
    )
    brokers = kafka_config['brokers'] # type: ignore

    return KafkaConsumerConfig(
        client_id='sync_consumer_client',
        group_id='sync_consumer_group',
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        bootstrap_servers=brokers, # type: ignore
        topics=['sync-events']  # Sync-specific topics
    )

async def create_record_kafka_consumer_config(app_container: IndexingAppContainer) -> KafkaConsumerConfig:
    """Create Kafka configuration for record events"""
    config_service = app_container.config_service()
    kafka_config = await config_service.get_config(
        config_node_constants.KAFKA.value
    )
    brokers = kafka_config['brokers'] # type: ignore

    return KafkaConsumerConfig(
        client_id='records_consumer_client',
        group_id='records_consumer_group',
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        bootstrap_servers=brokers, # type: ignore
        topics=['record-events']  # Records-specific topics
    )

async def create_aiconfig_kafka_consumer_config(app_container: QueryAppContainer) -> KafkaConsumerConfig:
    """Create Kafka configuration for AI config events"""
    config_service = app_container.config_service()
    kafka_config = await config_service.get_config(
        config_node_constants.KAFKA.value
    )
    brokers = kafka_config['brokers'] # type: ignore

    return KafkaConsumerConfig(
        client_id='aiconfig_consumer_client',
        group_id='aiconfig_consumer_group',
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        bootstrap_servers=brokers, # type: ignore
        topics=['entity-events']  # AI config events come through entity-events topic
    )

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

async def create_record_message_handler(app_container: ConnectorAppContainer) -> Callable[[Dict[str, Any]], Awaitable[bool]]:
    """Create a message handler for entity events"""
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
            if connector == Connectors.GOOGLE_MAIL.value:
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

            elif connector == Connectors.GOOGLE_DRIVE.value:
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
                logger.warning(f"Unknown sync event type: {event_type}")
                return False

        except Exception as e:
            logger.error(f"Error processing sync message: {str(e)}", exc_info=True)
            return False

    return handle_sync_message

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
