from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any, Dict


class IRateLimiter(AbstractAsyncContextManager, ABC):
    """
    Interface for rate limiting functionality.
    This interface defines the contract for rate limiters that can be implemented
    by different connectors to handle their specific rate limiting requirements.
    """

    @abstractmethod
    async def acquire(self, operation: str = "default") -> bool:
        """
        Acquire a rate limit token for the specified operation.
        Args:
            operation (str): The operation being rate limited (e.g., "read", "write", "search")
        Returns:
            bool: True if token was acquired, False if rate limit exceeded
        """
        pass

    @abstractmethod
    async def release(self, operation: str = "default") -> None:
        """
        Release a rate limit token for the specified operation.
        Args:
            operation (str): The operation being rate limited
        """
        pass

    @abstractmethod
    def get_rate_limit_info(self, operation: str = "default") -> Dict[str, Any]:
        """
        Get current rate limit information for the specified operation.
        Args:
            operation (str): The operation to get info for
        Returns:
            Dict[str, Any]: Rate limit information including current usage, limits, etc.
        """
        pass

    @abstractmethod
    def is_rate_limited(self, operation: str = "default") -> bool:
        """
        Check if the specified operation is currently rate limited.
        Args:
            operation (str): The operation to check
        Returns:
            bool: True if rate limited, False otherwise
        """
        pass

    @abstractmethod
    async def reset(self, operation: str = "default") -> None:
        """
        Reset rate limit counters for the specified operation.
        Args:
            operation (str): The operation to reset
        """
        pass

    async def __aenter__(self) -> "IRateLimiter":
        """Context manager entry - acquire a default token"""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - release the default token"""
        await self.release()
