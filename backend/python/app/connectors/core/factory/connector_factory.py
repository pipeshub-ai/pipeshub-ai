"""Main connector factory for creating business apps connectors"""

import logging
from typing import Any, Dict, List, Optional, Type

from app.connectors.core.base.auth.authentication_service import (
    BaseAuthenticationService,
)
from app.connectors.core.base.data_service.data_service import (
    BaseDataProcessor,
    BaseDataService,
)
from app.connectors.core.base.error.error import BaseErrorHandlingService
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.base.sync_service.sync_service import BaseSyncService
from app.connectors.core.base.webhook.webhook_service import BaseWebhookService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_factory import (
    IConnectorFactory,
)
from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.enums.enums import ConnectorType


class ConnectorRegistry:
    """Registry for connector implementations"""

    def __init__(self) -> None:
        self._connectors: Dict[ConnectorType, Type[IConnectorService]] = {}
        self._auth_services: Dict[ConnectorType, Type[BaseAuthenticationService]] = {}
        self._data_services: Dict[ConnectorType, Type[BaseDataService]] = {}
        self._configs: Dict[ConnectorType, ConnectorConfig] = {}

    def register_connector(
        self,
        connector_type: ConnectorType,
        connector_class: Type[IConnectorService],
        auth_service_class: Type[BaseAuthenticationService],
        data_service_class: Type[BaseDataService],
        config: ConnectorConfig,
    ) -> None:
        """Register a connector implementation"""
        self._connectors[connector_type] = connector_class
        self._auth_services[connector_type] = auth_service_class
        self._data_services[connector_type] = data_service_class
        self._configs[connector_type] = config

    def get_connector_class(self, connector_type: ConnectorType) -> Optional[Type[IConnectorService]]:
        """Get connector class for a type"""
        return self._connectors.get(connector_type)

    def get_auth_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseAuthenticationService]]:
        """Get auth service class for a type"""
        return self._auth_services.get(connector_type)

    def get_data_service_class(self, connector_type: ConnectorType) -> Optional[Type[BaseDataService]]:
        """Get data service class for a type"""
        return self._data_services.get(connector_type)

    def get_config(self, connector_type: ConnectorType) -> Optional[ConnectorConfig]:
        """Get config for a connector type"""
        return self._configs.get(connector_type)

    def get_supported_connectors(self) -> List[ConnectorType]:
        """Get list of supported connector types"""
        return list(self._connectors.keys())

    def is_supported(self, connector_type: ConnectorType) -> bool:
        """Check if connector type is supported"""
        return connector_type in self._connectors


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
        connector_class: Type[IConnectorService],
        auth_service_class: Type[BaseAuthenticationService],
        data_service_class: Type[BaseDataService],
        config: ConnectorConfig,
    ) -> None:
        """Register a new connector implementation"""
        self.registry.register_connector(
            connector_type, connector_class, auth_service_class, data_service_class, config
        )
        self.logger.info(f"Registered connector: {connector_type.value}")

    def create_connector(self, connector_type: ConnectorType, config: ConnectorConfig) -> IConnectorService:
        """Create a connector instance"""
        try:
            if not self.registry.is_supported(connector_type):
                raise ValueError(f"Connector type {connector_type.value} is not supported")

            # Get registered classes
            connector_class = self.registry.get_connector_class(connector_type)
            auth_service_class = self.registry.get_auth_service_class(connector_type)
            data_service_class = self.registry.get_data_service_class(connector_type)

            if not all([connector_class, auth_service_class, data_service_class]):
                raise ValueError(f"Incomplete registration for connector type {connector_type.value}")

            # Create service instances
            auth_service = auth_service_class(self.logger, config)
            data_service = data_service_class(self.logger, auth_service)

            # Create connector instance
            connector = connector_class(
                logger=self.logger,
                connector_type=connector_type,
                config_service=config,
                auth_service=auth_service,
                data_service=data_service,
                error_service=self.default_error_service,
                event_service=self.default_event_service,
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

    def with_custom_auth_service(self, auth_service: BaseAuthenticationService) -> 'ConnectorBuilder':
        """Set a custom authentication service"""
        self._custom_services['auth_service'] = auth_service
        return self

    def with_custom_data_service(self, data_service: BaseDataService) -> 'ConnectorBuilder':
        """Set a custom data service"""
        self._custom_services['data_service'] = data_service
        return self

    def with_custom_error_service(self, error_service: BaseErrorHandlingService) -> 'ConnectorBuilder':
        """Set a custom error handling service"""
        self._custom_services['error_service'] = error_service
        return self

    def build(self) -> IConnectorService:
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

        # Apply custom services if provided
        if 'auth_service' in self._custom_services:
            connector.auth_service = self._custom_services['auth_service']

        if 'data_service' in self._custom_services:
            connector.data_service = self._custom_services['data_service']

        if 'error_service' in self._custom_services:
            connector.error_service = self._custom_services['error_service']

        return connector


class ConnectorManager:
    """Manager for handling multiple connector instances"""

    def __init__(self, factory: UniversalConnectorFactory) -> None:
        self.factory = factory
        self._active_connectors: Dict[str, IConnectorService] = {}
        self.logger = factory.logger

    async def create_and_connect(
        self,
        connector_type: ConnectorType,
        credentials: Dict[str, Any],
        config: Optional[ConnectorConfig] = None
    ) -> Optional[IConnectorService]:
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
            if await connector.connect(credentials):
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
                await connector.disconnect()
                self.logger.info(f"Disconnected connector: {connector_id}")
            except Exception as e:
                self.logger.error(f"Failed to disconnect connector {connector_id}: {str(e)}")

        self._active_connectors.clear()

    def get_active_connectors(self) -> Dict[str, IConnectorService]:
        """Get all active connectors"""
        return self._active_connectors.copy()

    def get_connector(self, connector_id: str) -> Optional[IConnectorService]:
        """Get a specific connector by ID"""
        return self._active_connectors.get(connector_id)

    async def remove_connector(self, connector_id: str) -> bool:
        """Remove a connector"""
        if connector_id in self._active_connectors:
            connector = self._active_connectors[connector_id]
            try:
                await connector.disconnect()
                del self._active_connectors[connector_id]
                self.logger.info(f"Removed connector: {connector_id}")
                return True
            except Exception as e:
                self.logger.error(f"Failed to remove connector {connector_id}: {str(e)}")
                return False
        return False
