from abc import ABC, abstractmethod
from logging import Logger
from typing import Dict, Optional

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.models.entities import Record


class BaseConnector(ABC):
    """Base abstract class for all connectors"""
    logger: Logger
    data_entities_processor: DataSourceEntitiesProcessor
    arango_service: BaseArangoService
    config_service: ConfigurationService
    def __init__(self, logger, data_entities_processor: DataSourceEntitiesProcessor,
        arango_service: BaseArangoService, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.arango_service = arango_service
        self.config_service = config_service

    @abstractmethod
    async def init(self) -> bool:
        pass

    @abstractmethod
    def test_connection_and_access(self) -> bool:
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    def get_signed_url(self, record: Record) -> Optional[str]:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def stream_record(self, record: Record) -> StreamingResponse:
        NotImplementedError("This method is not supported by the subclass")

    @abstractmethod
    def download_record(self, record: Record) -> Optional[str]:
        NotImplementedError("This method is not supported by the subclass")


    @abstractmethod
    def run_sync(self) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def run_incremental_sync(self) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def handle_webhook_notification(self, notification: Dict) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def cleanup(self) -> None:
        NotImplementedError("This method should be implemented by the subclass")

    @classmethod
    @abstractmethod
    async def create_connector(cls, logger, arango_service: BaseArangoService, config_service: ConfigurationService) -> "BaseConnector":
        NotImplementedError("This method should be implemented by the subclass")
