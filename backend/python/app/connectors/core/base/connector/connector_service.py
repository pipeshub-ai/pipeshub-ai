from abc import ABC, abstractmethod
from logging import Logger
from typing import Dict, List, Optional

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.models.entities import Record
from app.connectors.core.registry.connector_builder import ConnectorScope


class BaseConnector(ABC):
    """Base abstract class for all connectors"""
    logger: Logger
    data_entities_processor: DataSourceEntitiesProcessor
    data_store_provider: DataStoreProvider
    config_service: ConfigurationService
    app: App
    connector_name: Connectors
    connector_id: str
    scope: str
    created_by: str
    creator_email: Optional[str]

    def __init__(
        self,
        app: App,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str
    ) -> None:
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.app = app
        self.connector_name = app.get_app_name()
        self.data_store_provider = data_store_provider
        self.config_service = config_service
        self.connector_id = connector_id
        self.scope = scope
        self.created_by = created_by
        self.creator_email = None

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
    def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
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
    async def cleanup(self) -> None:
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    async def reindex_records(self, record_results: List[Record]) -> None:
        NotImplementedError("This method should be implemented by the subclass")

    @classmethod
    @abstractmethod
    async def create_connector(cls, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> "BaseConnector":
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for a specific filter field.

        Args:
            filter_key: The filter field name (e.g., "space_keys", "page_ids")
            page: Current page number for offset-based pagination
            limit: Number of items per page for pagination
            search: Optional search query to filter options
            cursor: Optional cursor for cursor-based pagination (API-specific)

        Returns:
            FilterOptionsResponse object with options and pagination metadata
        """
        raise NotImplementedError("This method should be implemented by the subclass")

    def get_app(self) -> App:
        return self.app

    def get_app_group(self) -> AppGroup:
        return self.app.get_app_group()

    def get_app_name(self) -> str:
        return self.app.get_app_name()

    def get_app_group_name(self) -> str:
        return self.app.get_app_group_name()

    def get_connector_id(self) -> str:
        return self.connector_id

    async def _load_creator_email(self) -> None:
        """
        Load and cache the creator's email for personal scope connectors.
        
        This is useful for connectors that need to create permissions for the creator.
        Call this in init() if needed (typically for personal scope connectors).
        """
        if self.scope == ConnectorScope.PERSONAL.value and self.created_by:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_user_id(self.created_by)
                    if user and user.get("email"):
                        self.creator_email = user.get("email")
                        self.logger.debug(f"Cached creator email: {self.creator_email}")
            except Exception as e:
                self.logger.warning(f"Could not get user for created_by {self.created_by}: {e}")
