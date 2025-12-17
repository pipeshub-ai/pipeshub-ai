from abc import ABC, abstractmethod
from typing import Any


class IUserService(ABC):
    """Interface for user service operations across different connectors.
    This interface defines common user-related operations that can be
    implemented by different connectors to handle user management,
    authentication, and user-specific data operations.
    """

    @abstractmethod
    async def connect_user(self, org_id: str, user_id: str, credentials: dict[str, Any] | None = None) -> bool:
        """Connect to the service for a specific user.

        Args:
            org_id (str): Organization identifier
            user_id (str): User identifier
            credentials (Optional[Dict[str, Any]]): User credentials
        Returns:
            bool: True if connection successful

        """

    @abstractmethod
    async def disconnect_user(self) -> bool:
        """Disconnect the current user from the service.

        Returns:
            bool: True if disconnection successful

        """

    @abstractmethod
    async def get_user_info(self, org_id: str) -> list[dict[str, Any]]:
        """Get information about the current user.

        Args:
            org_id (str): Organization identifier
        Returns:
            List[Dict[str, Any]]: List of user information dictionaries

        """

    @abstractmethod
    async def setup_change_monitoring(self, token: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Set up change monitoring/webhooks for the user's data.

        Args:
            token (Optional[Dict[str, Any]]): Previous monitoring token
        Returns:
            Optional[Dict[str, Any]]: Monitoring configuration or None if not supported

        """

    @abstractmethod
    async def stop_change_monitoring(self, channel_id: str | None, resource_id: str | None) -> bool:
        """Stop change monitoring for the user.

        Args:
            channel_id (Optional[str]): Monitoring channel identifier
            resource_id (Optional[str]): Resource identifier
        Returns:
            bool: True if monitoring stopped successfully

        """

    @abstractmethod
    async def get_changes(self, page_token: str) -> tuple[list[dict[str, Any]], str | None]:
        """Get changes since the last page token.

        Args:
            page_token (str): Token from previous change request
        Returns:
            Tuple[List[Dict[str, Any]], Optional[str]]: Changes and next page token

        """

    @abstractmethod
    async def get_start_page_token(self) -> str | None:
        """Get the initial page token for change monitoring.

        Returns:
            Optional[str]: Initial page token or None if not supported

        """

    @abstractmethod
    def get_service_info(self) -> dict[str, Any]:
        """Get information about the current service state.

        Returns:
            Dict[str, Any]: Service information including connection status

        """
