import logging
from typing import Any, Dict

from aiolimiter import AsyncLimiter

from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter


class BaseRateLimiter(IRateLimiter):
    """
    Base implementation of rate limiter with default behavior.
    This class provides a foundation for connector-specific rate limiters
    with logging and basic rate limiting functionality.
    """

    def __init__(self, logger: logging.Logger, max_rate: int = 100, time_window: int = 60) -> None:
        """
        Initialize the base rate limiter.
        Args:
            logger (logging.Logger): Logger instance for rate limiting events
            max_rate (int): Maximum requests per time window (default: 100)
            time_window (int): Time window in seconds (default: 60)
        """
        self.logger = logger
        self.max_rate = max_rate
        self.time_window = time_window

        # Create a single limiter for all operations by default
        # Connectors can override this to have operation-specific limiters
        self._limiter = AsyncLimiter(max_rate, time_window)

        # Track operation-specific usage
        self._operation_usage: Dict[str, int] = {}
        self._operation_limits: Dict[str, int] = {}

    async def acquire(self, operation: str = "default") -> bool:
        """
        Acquire a rate limit token for the specified operation.
        Args:
            operation (str): The operation being rate limited
        Returns:
            bool: True if token was acquired, False if rate limit exceeded
        """
        try:
            await self._limiter.acquire()
            self._operation_usage[operation] = self._operation_usage.get(operation, 0) + 1

            self.logger.debug(
                f"Rate limit token acquired for operation '{operation}'. "
                f"Usage: {self._operation_usage[operation]}"
            )
            return True

        except Exception as e:
            self.logger.warning(
                f"Rate limit exceeded for operation '{operation}'. "
                f"Error: {str(e)}"
            )
            return False

    async def release(self, operation: str = "default") -> None:
        """
        Release a rate limit token for the specified operation.
        Args:
            operation (str): The operation being rate limited
        """
        # For the base implementation, we don't need to do anything on release
        # as AsyncLimiter handles the token management automatically
        self.logger.debug(f"Rate limit token released for operation '{operation}'")

    def get_rate_limit_info(self, operation: str = "default") -> Dict[str, Any]:
        """
        Get current rate limit information for the specified operation.
        Args:
            operation (str): The operation to get info for
        Returns:
            Dict[str, Any]: Rate limit information
        """
        current_usage = self._operation_usage.get(operation, 0)
        operation_limit = self._operation_limits.get(operation, self.max_rate)

        return {
            "operation": operation,
            "current_usage": current_usage,
            "max_rate": self.max_rate,
            "time_window": self.time_window,
            "operation_limit": operation_limit,
            "remaining": max(0, operation_limit - current_usage),
            "is_rate_limited": current_usage >= operation_limit
        }

    def is_rate_limited(self, operation: str = "default") -> bool:
        """
        Check if the specified operation is currently rate limited.
        Args:
            operation (str): The operation to check
        Returns:
            bool: True if rate limited, False otherwise
        """
        current_usage = self._operation_usage.get(operation, 0)
        operation_limit = self._operation_limits.get(operation, self.max_rate)
        return current_usage >= operation_limit

    async def reset(self, operation: str = "default") -> None:
        """
        Reset rate limit counters for the specified operation.
        Args:
            operation (str): The operation to reset
        """
        if operation in self._operation_usage:
            del self._operation_usage[operation]

        if operation in self._operation_limits:
            del self._operation_limits[operation]

        self.logger.info(f"Rate limit counters reset for operation '{operation}'")

    def set_operation_limit(self, operation: str, limit: int) -> None:
        """
        Set a specific rate limit for an operation.
        Args:
            operation (str): The operation to set limit for
            limit (int): The rate limit for this operation
        """
        self._operation_limits[operation] = limit
        self.logger.info(f"Set rate limit for operation '{operation}': {limit}")

    def get_operation_usage(self, operation: str = "default") -> int:
        """
        Get current usage count for an operation.
        Args:
            operation (str): The operation to get usage for
        Returns:
            int: Current usage count
        """
        return self._operation_usage.get(operation, 0)
