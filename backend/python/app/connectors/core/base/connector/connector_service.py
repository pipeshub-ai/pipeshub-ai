import asyncio
import logging
from abc import ABC
from typing import Any, Dict

from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.core.interfaces.data_processor.data_processor import IDataProcessor
from app.connectors.core.interfaces.data_service.data_service import IDataService
from app.connectors.core.interfaces.error.error import IErrorHandlingService
from app.connectors.core.interfaces.event_service.event_service import IEventService
from app.connectors.core.interfaces.token_service.itoken_service import ITokenService
from app.connectors.enums.enums import ConnectorType


class BaseConnectorService(IConnectorService, ABC):
    """Base connector service with common functionality"""

    def __init__(
        self,
        logger: logging.Logger,
        connector_type: ConnectorType,
        config: ConnectorConfig,
        token_service: ITokenService,
        data_service: IDataService,
        data_processor: IDataProcessor,
        error_service: IErrorHandlingService,
        event_service: IEventService,
    ) -> None:
        self.logger = logger
        self.connector_type = connector_type
        self.config = config
        self.token_service = token_service
        self.data_service = data_service
        self.data_processor = data_processor
        self.error_service = error_service
        self.event_service = event_service
        self._connected = False
        self._connection_lock = asyncio.Lock()

    async def connect(self, credentials: Dict[str, Any]) -> bool:
        """Connect to the service"""
        async with self._connection_lock:
            try:
                self.logger.info(f"Connecting to {self.connector_type.value}")

                # Authenticate
                if not await self.token_service.authenticate(credentials):
                    raise Exception("Authentication failed")

                # Test connection
                if not await self.test_connection():
                    raise Exception("Connection test failed")

                self._connected = True

                self.logger.info(f"Successfully connected to {self.connector_type.value}")
                return True

            except Exception as e:
                self.error_service.log_error(e, "connect", {
                    "connector_type": self.connector_type.value,
                    "credentials_keys": list(credentials.keys()) if credentials else []
                })
                return False

    async def disconnect(self) -> bool:
        """Disconnect from the service"""
        async with self._connection_lock:
            try:
                self.logger.info(f"Disconnecting from {self.connector_type.value}")

                # Revoke token if available
                # This would be implemented in specific auth services

                self._connected = False

                self.logger.info(f"Successfully disconnected from {self.connector_type.value}")
                return True

            except Exception as e:
                self.error_service.log_error(e, "disconnect", {
                    "connector_type": self.connector_type.value
                })
                return False

    async def test_connection(self) -> bool:
        """Test the connection"""
        try:
            # This should be implemented by specific connectors
            # For now, return True if we have a valid auth service
            return self._connected and self.token_service is not None
        except Exception as e:
            self.error_service.log_error(e, "test_connection", {
                "connector_type": self.connector_type.value
            })
            return False

    def get_service_info(self) -> Dict[str, Any]:
        """Get service information"""
        return {
            "connector_type": self.connector_type.value,
            "connected": self._connected,
            "config": {
                "base_url": self.config.base_url,
                "api_version": self.config.api_version,
                "webhook_support": self.config.webhook_support,
                "batch_operations": self.config.batch_operations,
                "real_time_sync": self.config.real_time_sync,
            },
            "rate_limits": self.config.rate_limits,
            "scopes": self.config.scopes,
        }

    def is_connected(self) -> bool:
        """Check if service is connected"""
        return self._connected
