import asyncio
import logging
from abc import ABC
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from app.connectors.core.base.data_processor.data_processor import BaseDataProcessor
from app.connectors.core.base.data_service.data_service import BaseDataService
from app.connectors.core.base.error.error import BaseErrorHandlingService
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.base.rate_limiter.rate_limiter import BaseRateLimiter
from app.connectors.core.base.sync_service.sync_service import BaseSyncService
from app.connectors.core.base.token_service.token_service import BaseTokenService
from app.connectors.core.base.user_service.user_service import BaseUserService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.enums.enums import ConnectorType

T = TypeVar("T")

class BaseConnectorService(IConnectorService, ABC):
    """Base connector service with common functionality"""

    def __init__(
        self,
        logger: logging.Logger,
        connector_type: ConnectorType,
        config: ConnectorConfig,
        token_service: BaseTokenService,
        data_service: BaseDataService,
        data_processor: BaseDataProcessor,
        error_service: BaseErrorHandlingService,
        event_service: BaseEventService,
        rate_limiter: BaseRateLimiter,
        user_service: BaseUserService,
        sync_service: BaseSyncService,
    ) -> None:
        self.logger = logger
        self.connector_type = connector_type
        self.config = config
        self.token_service = token_service
        self.data_service = data_service
        self.data_processor = data_processor
        self.error_service = error_service
        self.event_service = event_service
        self.rate_limiter = rate_limiter
        self.user_service = user_service
        self.sync_service = sync_service
        self._connected = False
        self._connection_lock = asyncio.Lock()

    async def connect(self, credentials: Dict[str, Any]) -> bool:
        """Connect to the service with rate limiting"""
        async with self._connection_lock:
            try:
                self.logger.info(f"Connecting to {self.connector_type.value}")

                # Use rate limiting for connection
                async with self.rate_limiter:
                    # Authenticate
                    if not await self.token_service.authenticate(credentials):
                        raise Exception("Authentication failed")

                    # Test connection
                    if not await self.test_connection():
                        raise Exception("Connection test failed")

                    # Initialize user service if available
                    if self.user_service:
                        org_id = credentials.get("org_id")
                        user_id = credentials.get("user_id")
                        if org_id and user_id:
                            await self.user_service.connect_user(org_id, user_id, credentials)
                            self.logger.info(f"User service connected for {self.connector_type.value}")

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

                # Disconnect user service if available
                if self.user_service:
                    await self.user_service.disconnect_user()
                    self.logger.info(f"User service disconnected for {self.connector_type.value}")

                # Use rate limiting for disconnection if needed
                async with self.rate_limiter:
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
        """Test the connection with rate limiting"""
        try:
            # Use rate limiting for test operation
            if await self.rate_limiter.acquire("test"):

                try:
                    return self._connected and self.token_service is not None
                finally:
                    await self.rate_limiter.release("test")
            else:
                self.logger.warning(f"Rate limit exceeded for test operation on {self.connector_type.value}")
                return False
        except Exception as e:
            self.error_service.log_error(e, "test_connection", {
                "connector_type": self.connector_type.value
            })
            return False

    def get_service_info(self) -> Dict[str, Any]:
        """Get service information including rate limit and user service details"""
        rate_limit_info = self.rate_limiter.get_rate_limit_info()
        service_info = {
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
            "rate_limiting": rate_limit_info,
        }

        # Add user service info if available
        if self.user_service:
            service_info["user_service"] = self.user_service.get_service_info()

        return service_info

    def is_connected(self) -> bool:
        """Check if service is connected"""
        return self._connected

    def has_user_service(self) -> bool:
        """Check if user service is available"""
        return self.user_service is not None

    async def get_user_info(self, org_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user information if user service is available.
        Args:
            org_id (str): Organization identifier
        Returns:
            Optional[Dict[str, Any]]: User information or None if no user service
        """
        if not self.user_service:
            self.logger.warning(f"No user service available for {self.connector_type.value}")
            return None

        try:
            user_info = await self.user_service.get_user_info(org_id)
            return user_info[0] if user_info else None
        except Exception as e:
            self.logger.error(f"Failed to get user info: {str(e)}")
            return None

    async def setup_change_monitoring(self, token: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Setup change monitoring if user service is available.
        Args:
            token (Optional[Dict[str, Any]]): Previous monitoring token
        Returns:
            Optional[Dict[str, Any]]: Monitoring configuration or None
        """
        if not self.user_service:
            self.logger.warning(f"No user service available for {self.connector_type.value}")
            return None

        try:
            return await self.user_service.setup_change_monitoring(token)
        except Exception as e:
            self.logger.error(f"Failed to setup change monitoring: {str(e)}")
            return None

    async def get_changes(self, page_token: str) -> Optional[tuple]:
        """
        Get changes if user service is available.
        Args:
            page_token (str): Token from previous change request
        Returns:
            Optional[tuple]: Changes and next page token or None
        """
        if not self.user_service:
            self.logger.warning(f"No user service available for {self.connector_type.value}")
            return None

        try:
            return await self.user_service.get_changes(page_token)
        except Exception as e:
            self.logger.error(f"Failed to get changes: {str(e)}")
            return None

    async def perform_rate_limited_operation(self, operation: str, operation_func: Callable[..., Awaitable[T]]) -> T:
        """
        Helper method to perform operations with rate limiting.
        Args:
            operation (str): The operation being performed
            operation_func: The function to execute
        Returns:
            T: Result of the operation
        """
        try:
            # Acquire rate limit token
            if await self.rate_limiter.acquire(operation):
                self.logger.debug(f"Rate limit token acquired for operation '{operation}'")

                try:
                    # Execute the operation
                    result = await operation_func()

                    return result
                finally:
                    # Release token even if operation fails
                    await self.rate_limiter.release(operation)
                    self.logger.debug(f"Rate limit token released for operation '{operation}'")

            else:
                self.logger.warning(f"Rate limit exceeded for operation '{operation}'")
                raise Exception(f"Rate limit exceeded for operation '{operation}'")

        except Exception as e:
            self.logger.error(f"Error in rate limited operation '{operation}': {str(e)}")
            raise

    def get_rate_limit_status(self, operation: str = "default") -> Dict[str, Any]:
        """
        Get current rate limit status for an operation.
        Args:
            operation (str): The operation to check
        Returns:
            Dict[str, Any]: Rate limit status information
        """
        return self.rate_limiter.get_rate_limit_info(operation)
