from abc import ABC, abstractmethod
from logging import Logger
from typing import Dict, Optional

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.models.entities import Record


class BaseConnector(ABC):
    """Base abstract class for all connectors"""
    logger: Logger
    data_entities_processor: DataSourceEntitiesProcessor
    data_store_provider: DataStoreProvider
    config_service: ConfigurationService
    app: App
    connector_name: Connectors

    def __init__(self, app: App, logger, data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.app = app
        self.connector_name = app.get_app_name()
        self.data_store_provider = data_store_provider
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
    async def create_connector(cls, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> "BaseConnector":
        NotImplementedError("This method should be implemented by the subclass")

    def get_app(self) -> App:
        return self.app

    def get_app_group(self) -> AppGroup:
        return self.app.get_app_group()

    def get_app_name(self) -> str:
        return self.app.get_app_name()

    def get_app_group_name(self) -> str:
        return self.app.get_app_group_name()
