import asyncio
import logging
from abc import ABC
from typing import Any, Dict, Optional

from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.core.interfaces.data_service.data_service import IDataService
from app.connectors.core.interfaces.error.error import IErrorHandlingService
from app.connectors.core.interfaces.event_service.event_service import IEventService
from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter
from app.connectors.core.interfaces.user_service.iuser_service import IUserService
from app.connectors.enums.enums import ConnectorType


class BaseConnectorService(IConnectorService, ABC):
    """Base connector service with common functionality, rate limiter, and user service integration"""

    def __init__(
        self,
        logger: logging.Logger,
        connector_type: ConnectorType,
        config: ConnectorConfig,
        auth_service: IAuthenticationService,
        data_service: IDataService,
        error_service: IErrorHandlingService,
        event_service: IEventService,
        rate_limiter: IRateLimiter,
        user_service: Optional[IUserService] = None,
    ) -> None:
        self.logger = logger
        self.connector_type = connector_type
        self.config = config
        self.auth_service = auth_service
        self.data_service = data_service
        self.error_service = error_service
        self.event_service = event_service
        self.rate_limiter = rate_limiter
        self.user_service = user_service
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
                    if not await self.auth_service.authenticate(credentials):
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
                    # This should be implemented by specific connectors
                    # For now, return True if we have a valid auth service
                    result = self._connected and self.auth_service is not None
                    
                    await self.rate_limiter.release("test")
                    return result
                except Exception:
                    await self.rate_limiter.release("test")
                    raise
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

    async def perform_rate_limited_operation(self, operation: str, operation_func) -> Any:
        """
        Helper method to perform operations with rate limiting.
        
        Args:
            operation (str): The operation being performed
            operation_func: The function to execute
            
        Returns:
            Any: Result of the operation
        """
        try:
            # Acquire rate limit token
            if await self.rate_limiter.acquire(operation):
                self.logger.debug(f"Rate limit token acquired for operation '{operation}'")
                
                try:
                    # Execute the operation
                    result = await operation_func()
                    
                    # Release rate limit token
                    await self.rate_limiter.release(operation)
                    self.logger.debug(f"Rate limit token released for operation '{operation}'")
                    
                    return result
                except Exception:
                    # Release token even if operation fails
                    await self.rate_limiter.release(operation)
                    raise
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
