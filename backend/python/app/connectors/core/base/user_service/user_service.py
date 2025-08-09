import logging
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter
from app.connectors.core.interfaces.user_service.iuser_service import IUserService


class BaseUserService(IUserService):
    """
    Base implementation of user service with common functionality.
    This class provides default implementations and common patterns
    for user service operations across different connectors.
    """

    def __init__(
        self,
        logger: logging.Logger,
        rate_limiter: IRateLimiter,
        config: ConnectorConfig,
    ) -> None:
        """
        Initialize the base user service.
        Args:
            logger (logging.Logger): Logger instance
            rate_limiter (IRateLimiter): Rate limiter for API calls
            config (Any): Service configuration
        """
        self.logger = logger
        self.rate_limiter = rate_limiter
        self.config = config
        self._connected = False
        self._org_id = None
        self._user_id = None
        self._service = None

    async def connect_user(self, org_id: str, user_id: str, credentials: Optional[Dict[str, Any]] = None) -> bool:
        """
        Connect to the service for a specific user.
        Args:
            org_id (str): Organization identifier
            user_id (str): User identifier
            credentials (Optional[Dict[str, Any]]): User credentials
        Returns:
            bool: True if connection successful
        """
        try:
            self._org_id = org_id
            self._user_id = user_id

            if credentials:
                self.credentials = credentials

            # Use rate limiting for connection
            async with self.rate_limiter:
                success = await self._perform_connection()
                if success:
                    self._connected = True
                    self.logger.info(f"✅ Connected user {user_id} to service")
                return success

        except Exception as e:
            self.logger.error(f"❌ Failed to connect user {user_id}: {str(e)}")
            return False

    async def disconnect_user(self) -> bool:
        """
        Disconnect the current user from the service.
        Returns:
            bool: True if disconnection successful
        """
        try:
            if self._service:
                await self._cleanup_service()
                self._service = None

            self._connected = False
            self._org_id = None
            self._user_id = None
            self.credentials = None

            self.logger.info("✅ User disconnected from service")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect user: {str(e)}")
            return False

    async def get_user_info(self, org_id: str) -> List[Dict[str, Any]]:
        """
        Get information about the current user.
        Args:
            org_id (str): Organization identifier
        Returns:
            List[Dict[str, Any]]: List of user information dictionaries
        """
        try:
            async with self.rate_limiter:
                user_info = await self._fetch_user_info(org_id)
                self.logger.info(f"✅ Retrieved user info for org {org_id}")
                return user_info

        except Exception as e:
            self.logger.error(f"❌ Failed to get user info: {str(e)}")
            return []

    async def setup_change_monitoring(self, token: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Set up change monitoring/webhooks for the user's data.
        Args:
            token (Optional[Dict[str, Any]]): Previous monitoring token
        Returns:
            Optional[Dict[str, Any]]: Monitoring configuration or None if not supported
        """
        try:
            async with self.rate_limiter:
                monitoring_config = await self._setup_monitoring(token)
                if monitoring_config:
                    self.logger.info("✅ Change monitoring setup successful")
                return monitoring_config

        except Exception as e:
            self.logger.error(f"❌ Failed to setup change monitoring: {str(e)}")
            return None

    async def stop_change_monitoring(self, channel_id: Optional[str], resource_id: Optional[str]) -> bool:
        """
        Stop change monitoring for the user.
        Args:
            channel_id (Optional[str]): Monitoring channel identifier
            resource_id (Optional[str]): Resource identifier
        Returns:
            bool: True if monitoring stopped successfully
        """
        try:
            if not channel_id or not resource_id:
                self.logger.warning("⚠️ No channel ID or resource ID to stop")
                return True

            async with self.rate_limiter:
                success = await self._stop_monitoring(channel_id, resource_id)
                if success:
                    self.logger.info("✅ Change monitoring stopped successfully")
                return success

        except Exception as e:
            self.logger.error(f"❌ Failed to stop change monitoring: {str(e)}")
            return False

    async def get_changes(self, page_token: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Get changes since the last page token.
        Args:
            page_token (str): Token from previous change request
        Returns:
            Tuple[List[Dict[str, Any]], Optional[str]]: Changes and next page token
        """
        try:
            async with self.rate_limiter:
                changes, next_token = await self._fetch_changes(page_token)
                self.logger.info(f"✅ Retrieved {len(changes)} changes")
                return changes, next_token

        except Exception as e:
            self.logger.error(f"❌ Failed to get changes: {str(e)}")
            return [], None

    async def get_start_page_token(self) -> Optional[str]:
        """
        Get the initial page token for change monitoring.
        Returns:
            Optional[str]: Initial page token or None if not supported
        """
        try:
            async with self.rate_limiter:
                token = await self._fetch_start_page_token()
                if token:
                    self.logger.info(f"✅ Retrieved start page token: {token}")
                return token

        except Exception as e:
            self.logger.error(f"❌ Failed to get start page token: {str(e)}")
            return None

    def get_service_info(self) -> Dict[str, Any]:
        """
        Get information about the current service state.
        Returns:
            Dict[str, Any]: Service information including connection status
        """
        return {
            "connected": self._connected,
            "org_id": self._org_id,
            "user_id": self._user_id,
            "service_type": self.__class__.__name__,
            "rate_limiter_info": self.rate_limiter.get_rate_limit_info()
        }

    # Abstract methods that must be implemented by specific connectors
    @abstractmethod
    async def _perform_connection(self) -> bool:
        """Perform the actual connection logic. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _cleanup_service(self) -> None:
        """Clean up service resources. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _fetch_user_info(self, org_id: str) -> List[Dict[str, Any]]:
        """Fetch user information. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _setup_monitoring(self, token: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Setup change monitoring. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _stop_monitoring(self, channel_id: str, resource_id: str) -> bool:
        """Stop change monitoring. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _fetch_changes(self, page_token: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch changes. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _fetch_start_page_token(self) -> Optional[str]:
        """Fetch start page token. Must be implemented by subclasses."""
        pass
