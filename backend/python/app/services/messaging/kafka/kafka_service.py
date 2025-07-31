import json
from logging import Logger

from aiokafka import AIOKafkaProducer # type: ignore

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import EventTypes
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.services.messaging.interface.messaging_service import IMessagingService
from app.services.messaging.messaging_factory import MessagingFactory
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.google.common.sync_tasks import SyncTasks
from app.services.messaging.kafka.config.kafka_config import KafkaConfig

class KafkaService(IMessagingService):
    def __init__(self, 
                config: ConfigurationService, 
                sync_tasks: SyncTasks, 
                arango_service: ArangoService, 
                logger: Logger,
                kafka_config: KafkaConfig) -> None:
        self.config_service = config
        self.producer = MessagingFactory.create_producer(
            "kafka", 
            config_service=config, 
            sync_tasks=sync_tasks, 
            arango_service=arango_service, 
            logger=logger,  
            kafka_config=kafka_config
        )
        self.logger = logger

    # implementing abstract methods from IMessagingService
    # TODO: Implement this based on code usage in other files
    async def initialize(self) -> None:
        """Initialize the Kafka service"""
        pass

    # implementing abstract methods from IMessagingService
    async def __aenter__(self) -> "KafkaService":
        """Async context manager entry"""
        await self.producer.start()
        return self

    # implementing abstract methods from IMessagingService
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.producer.stop()
