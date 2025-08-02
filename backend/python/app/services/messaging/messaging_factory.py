from logging import Logger
from typing import Union

from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.google.common.sync_tasks import SyncTasks
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import (
    KafkaConsumerConfig,
    KafkaProducerConfig,
)
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.producer.producer import KafkaMessagingProducer
from app.services.messaging.kafka.rate_limiter.rate_limiter import RateLimiter

# from app.setups.connector_setup import AppContainer

class MessagingFactory:
    """Factory for creating messaging service instances"""

    @staticmethod
    def create_producer(
        broker_type: str = "kafka",
        logger: Logger = None,
        config: Union[KafkaProducerConfig] = None
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
        # app_container: AppContainer = None,
        logger: Logger = None,
        config: Union[KafkaConsumerConfig] = None,
        rate_limiter: RateLimiter = None
    ) -> IMessagingConsumer:
        """Create a messaging consumer"""
        if broker_type.lower() == "kafka":
            return KafkaMessagingConsumer(logger, config,rate_limiter)
        else:
            raise ValueError(f"Unsupported broker type: {broker_type}")
