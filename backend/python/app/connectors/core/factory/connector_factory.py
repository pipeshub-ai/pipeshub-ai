"""Main connector factory for creating business apps connectors"""

import logging
from typing import Any, Dict, List, Optional, Type

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_processor import BaseDataProcessor
from app.connectors.core.base.data_service.data_service import BaseDataService
from app.connectors.core.base.error.error import BaseErrorHandlingService
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.base.rate_limiter.rate_limiter import BaseRateLimiter
from app.connectors.core.base.sync_service.sync_service import BaseSyncService
from app.connectors.core.base.token_service.token_service import BaseTokenService
from app.connectors.core.base.user_service.user_service import BaseUserService
from app.connectors.core.base.webhook.webhook_service import BaseWebhookService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_factory import (
    IConnectorFactory,
)
from app.connectors.enums.enums import ConnectorType


class ConnectorRegistry:
    """Registry for connector implementations"""

    def __init__(self) -> None:
        self._connectors: Dict[ConnectorType, Type[BaseConnector]] = {}
        self._token_services: Dict[ConnectorType, Type[BaseTokenService]] = {}
        self._data_services: Dict[ConnectorType, Type[BaseDataService]] = {}
        self._data_processors: Dict[ConnectorType, Type[BaseDataProcessor]] = {}
        self._user_services: Dict[ConnectorType, Type[BaseUserService]] = {}
        self._sync_services: Dict[ConnectorType, Type[BaseSyncService]] = {}
        self._rate_limiter_classes: Dict[ConnectorType, Type[BaseRateLimiter]] = {}
        self._error_service_classes: Dict[ConnectorType, Type[BaseErrorHandlingService]] = {}
        self._event_service_classes: Dict[ConnectorType, Type[BaseEventService]] = {}
        self._configs: Dict[ConnectorType, ConnectorConfig] = {}

    def register_connector(
        self,
        connector_type: ConnectorType,
        connector_class: Type[BaseConnector],
        token_service_class: Type[BaseTokenService],
        data_service_class: Type[BaseDataService],
        data_processor_class: Type[BaseDataProcessor],
        sync_service_class: Type[BaseSyncService],
        user_service_class: Type[BaseUserService],
        error_service_class: Type[BaseErrorHandlingService],
        event_service_class: Type[BaseEventService],
        rate_limiter_class: Type[BaseRateLimiter],
        config: ConnectorConfig,
    ) -> None:
        """Register a connector implementation"""
        self._token_services[connector_type] = token_service_class
        self._data_services[connector_type] = data_service_class
        self._data_processors[connector_type] = data_processor_class
        self._sync_services[connector_type] = sync_service_class
        self._user_services[connector_type] = user_service_class
        self._error_service_classes[connector_type] = error_service_class
        self._event_service_classes[connector_type] = event_service_class
        self._rate_limiter_classes[connector_type] = rate_limiter_class
        self._connectors[connector_type] = connector_class
        self._configs[connector_type] = config

    def get_connector_class(self, connector_type: ConnectorType) -> Optional[Type[BaseConnector]]:
        """Get connector class for a type"""
        return self._connectors.get(connector_type)

    def get_token_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseTokenService]]:
        """Get token service class for a type"""
        return self._token_services.get(connector_type)

    def get_data_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseDataService]]:
        """Get data service class for a type"""
        return self._data_services.get(connector_type)

    def get_data_processor_class(self, connector_type: ConnectorType) -> Optional[Type[BaseDataProcessor]]:
        """Get data processor class for a type"""
        return self._data_processors.get(connector_type)

    def get_sync_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseSyncService]]:
        """Get sync service class for a type"""
        return self._sync_services.get(connector_type)

    def get_user_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseUserService]]:
        """Get user service class for a type"""
        return self._user_services.get(connector_type)

    def get_config(self, connector_type: ConnectorType) -> Optional[ConnectorConfig]:
        """Get config for a connector type"""
        return self._configs.get(connector_type)

    def get_supported_connectors(self) -> List[ConnectorType]:
        """Get list of supported connector types"""
        return list(self._connectors.keys())

    def is_supported(self, connector_type: ConnectorType) -> bool:
        """Check if connector type is supported"""
        return connector_type in self._connectors

    def get_rate_limiter_class(self, connector_type: ConnectorType) -> Optional[Type[BaseRateLimiter]]:
        """Get rate limiter class for a type"""
        return self._rate_limiter_classes.get(connector_type)

    def get_error_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseErrorHandlingService]]:
        """Get error service class for a type"""
        return self._error_service_classes.get(connector_type)

    def get_event_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseEventService]]:
        """Get event service class for a type"""
        return self._event_service_classes.get(connector_type)

class UniversalConnectorFactory(IConnectorFactory):
    """Universal factory for creating any connector type"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.registry = ConnectorRegistry()
        self._initialize_default_services()

    def _initialize_default_services(self) -> None:
        """Initialize default service implementations"""
        # These would be replaced with actual implementations
        # Note: Default services are not used in practice, they're just placeholders
        # The actual services are created with proper config in create_connector
        self.default_error_service = BaseErrorHandlingService(self.logger)
        self.default_event_service = BaseEventService(self.logger)
        self.default_webhook_service = BaseWebhookService(self.logger)
        self.default_sync_service = BaseSyncService(self.logger)
        self.default_data_processor = BaseDataProcessor(self.logger)

    def register_connector_implementation(
        self,
        connector_type: ConnectorType,
        connector_class: Type[BaseConnector],
        token_service_class: Type[BaseTokenService],
        data_service_class: Type[BaseDataService],
        data_processor_class: Type[BaseDataProcessor],
        sync_service_class: Type[BaseSyncService],
        user_service_class: Type[BaseUserService],
        error_service_class: Type[BaseErrorHandlingService],
        event_service_class: Type[BaseEventService],
        rate_limiter_class: Type[BaseRateLimiter],
        config: ConnectorConfig,
    ) -> None:
        """Register a new connector implementation"""
        self.registry.register_connector(
            connector_type, connector_class, token_service_class, data_service_class, data_processor_class, sync_service_class, user_service_class, error_service_class, event_service_class, rate_limiter_class, config
        )
        self.logger.info(f"Registered connector: {connector_type.value}")

    def create_connector(self, connector_type: ConnectorType, config: ConnectorConfig) -> BaseConnector:
        """Create a connector instance"""
        try:
            self.logger.info(f"Creating connector for {connector_type.value} with config: {config}")
            if not self.registry.is_supported(connector_type):
                raise ValueError(f"Connector type {connector_type.value} is not supported")

            # Get registered classes
            connector_class = self.registry.get_connector_class(connector_type)
            token_service_class = self.registry.get_token_service_class(connector_type)
            data_service_class = self.registry.get_data_service_class(connector_type)
            data_processor_class = self.registry.get_data_processor_class(connector_type)
            sync_service_class = self.registry.get_sync_service_class(connector_type)
            user_service_class = self.registry.get_user_service_class(connector_type)
            error_service_class = self.registry.get_error_service_class(connector_type)
            event_service_class = self.registry.get_event_service_class(connector_type)
            rate_limiter_class = self.registry.get_rate_limiter_class(connector_type)

            # Create service instances
            if (connector_class is None or token_service_class is None or data_service_class is None or
                data_processor_class is None or sync_service_class is None or user_service_class is None or
                error_service_class is None or event_service_class is None or rate_limiter_class is None):
                raise ValueError(f"Incomplete registration for connector type {connector_type.value}")

            token_service = token_service_class(self.logger, config)
            data_service = data_service_class(self.logger, token_service)
            data_processor = data_processor_class(self.logger)

            # Create connector instance
            connector = connector_class(
                logger=self.logger,
                data_entities_processor=data_processor,
                arango_service=data_service,
            )

            self.logger.info(f"Created connector instance for {connector_type.value}")
            return connector

        except Exception as e:
            self.logger.error(f"Failed to create connector {connector_type.value}: {str(e)}")
            raise

    def get_supported_connectors(self) -> List[ConnectorType]:
        """Get list of supported connector types"""
        return self.registry.get_supported_connectors()

    def validate_connector_type(self, connector_type: ConnectorType) -> bool:
        """Validate if connector type is supported"""
        return self.registry.is_supported(connector_type)

    def get_rate_limiter_class(self, connector_type: ConnectorType) -> Optional[Type[BaseRateLimiter]]:
        """Get rate limiter class for a type"""
        return self.registry.get_rate_limiter_class(connector_type)

    def get_error_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseErrorHandlingService]]:
        """Get error service class for a type"""
        return self.registry.get_error_service_class(connector_type)

    def get_event_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseEventService]]:
        """Get event service class for a type"""
        return self.registry.get_event_service_class(connector_type)

    def get_connector_info(self, connector_type: ConnectorType) -> Dict[str, Any]:
        """Get information about a connector type"""
        if not self.validate_connector_type(connector_type):
            return {"error": f"Connector type {connector_type.value} not supported"}

        config = self.registry.get_config(connector_type)
        return {
            "connector_type": connector_type.value,
            "supported": True,
            "config": {
                "authentication_type": config.authentication_type.value,
                "base_url": config.base_url,
                "api_version": config.api_version,
                "webhook_support": config.webhook_support,
                "batch_operations": config.batch_operations,
                "real_time_sync": config.real_time_sync,
            } if config else None,
        }


class ConnectorBuilder:
    """Builder pattern for creating connectors with custom configurations"""

    def __init__(self, factory: UniversalConnectorFactory) -> None:
        self.factory = factory
        self._connector_type: Optional[ConnectorType] = None
        self._config: Optional[ConnectorConfig] = None
        self._custom_services: Dict[str, Any] = {}

    def for_connector_type(self, connector_type: ConnectorType) -> 'ConnectorBuilder':
        """Set the connector type"""
        self._connector_type = connector_type
        return self

    def with_config(self, config: ConnectorConfig) -> 'ConnectorBuilder':
        """Set the connector configuration"""
        self._config = config
        return self

    def with_custom_token_service(self, token_service: BaseTokenService) -> 'ConnectorBuilder':
        """Set a custom token service"""
        self._custom_services['token_service'] = token_service
        return self

    def with_custom_data_service(self, data_service: BaseDataService) -> 'ConnectorBuilder':
        """Set a custom data service"""
        self._custom_services['data_service'] = data_service
        return self

    def with_custom_error_service(self, error_service: BaseErrorHandlingService) -> 'ConnectorBuilder':
        """Set a custom error handling service"""
        self._custom_services['error_service'] = error_service
        return self

    def build(self) -> BaseConnector:
        """Build the connector instance"""
        if not self._connector_type:
            raise ValueError("Connector type must be specified")

        if not self._config:
            # Use default config from registry
            self._config = self.factory.registry.get_config(self._connector_type)
            if not self._config:
                raise ValueError(f"No configuration found for connector type {self._connector_type.value}")

        # Create connector using factory
        connector = self.factory.create_connector(self._connector_type, self._config)
        return connector


class ConnectorManager:
    """Manager for handling multiple connector instances"""

    def __init__(self, factory: UniversalConnectorFactory) -> None:
        self.factory = factory
        self._active_connectors: Dict[str, BaseConnector] = {}
        self.logger = factory.logger

    async def create_and_connect(
        self,
        connector_type: ConnectorType,
        credentials: Dict[str, Any],
        config: Optional[ConnectorConfig] = None
    ) -> Optional[BaseConnector]:
        """Create a connector and connect it"""
        try:
            if not config:
                config = self.factory.registry.get_config(connector_type)

            if not config:
                self.logger.error(f"No configuration found for connector type {connector_type.value}")
                return None

            # Create connector
            connector = self.factory.create_connector(connector_type, config)

            # Connect
            if await connector.init():
                connector_id = f"{connector_type.value}_{len(self._active_connectors)}"
                self._active_connectors[connector_id] = connector
                self.logger.info(f"Created and connected connector: {connector_id}")
                return connector
            else:
                self.logger.error(f"Failed to connect connector {connector_type.value}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to create and connect connector {connector_type.value}: {str(e)}")
            return None

    async def disconnect_all(self) -> None:
        """Disconnect all active connectors"""
        for connector_id, connector in self._active_connectors.items():
            try:
                await connector.cleanup()
                self.logger.info(f"Disconnected connector: {connector_id}")
            except Exception as e:
                self.logger.error(f"Failed to disconnect connector {connector_id}: {str(e)}")

        self._active_connectors.clear()

    def get_active_connectors(self) -> Dict[str, BaseConnector]:
        """Get all active connectors"""
        return self._active_connectors.copy()

    def get_connector(self, connector_id: str) -> Optional[BaseConnector]:
        """Get a specific connector by ID"""
        return self._active_connectors.get(connector_id)

    async def remove_connector(self, connector_id: str) -> bool:
        """Remove a connector"""
        if connector_id in self._active_connectors:
            connector = self._active_connectors[connector_id]
            try:
                await connector.cleanup()
                del self._active_connectors[connector_id]
                self.logger.info(f"Removed connector: {connector_id}")
                return True
            except Exception as e:
                self.logger.error(f"Failed to remove connector {connector_id}: {str(e)}")
                return False
        return False
