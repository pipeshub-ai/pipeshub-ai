from typing import Union
from logging import Logger

from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.config.kafka_config import KafkaConfig
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.google.common.sync_tasks import SyncTasks
from app.setups.connector_setup import AppContainer

class MessagingFactory:
    """Factory for creating messaging service instances"""
    
    @staticmethod
    def create_producer(
        broker_type: str = "kafka",
        logger: Logger = None,
        config: Union[KafkaConfig] = None
    ) -> IMessagingProducer:
        """Create a messaging producer"""
        if broker_type.lower() == "kafka":
            return KafkaMessagingProducer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")
    
    @staticmethod
    def create_consumer(
        broker_type: str = "kafka",
        sync_tasks: SyncTasks = None,
        arango_service: ArangoService = None,
        app_container: AppContainer = None,
        logger: Logger = None,
        config: Union[KafkaConfig] = None
    ) -> IMessagingConsumer:
        """Create a messaging consumer"""
        if broker_type.lower() == "kafka":
            return KafkaMessagingConsumer(logger, config)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")
