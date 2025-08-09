import logging
from typing import Any, Dict

from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter


class NoOpRateLimiter(IRateLimiter):
    """
    No-operation rate limiter for connectors that don't require rate limiting.
    This implementation provides a null object pattern for rate limiting,
    allowing connectors to use the rate limiter interface without any
    actual rate limiting behavior.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """
        Initialize the no-op rate limiter.
        Args:
            logger (logging.Logger): Logger instance
        """
        self.logger = logger

    async def acquire(self, operation: str = "default") -> bool:
        """
        Always return True - no rate limiting applied.
        Args:
            operation (str): The operation being rate limited
        Returns:
            bool: Always True
        """
        self.logger.debug(f"No-op rate limiter: token acquired for operation '{operation}'")
        return True

    async def release(self, operation: str = "default") -> None:
        """
        No-op release - nothing to do.
        Args:
            operation (str): The operation being rate limited
        """
        self.logger.debug(f"No-op rate limiter: token released for operation '{operation}'")

    def get_rate_limit_info(self, operation: str = "default") -> Dict[str, Any]:
        """
        Return empty rate limit info since no rate limiting is applied.
        Args:
            operation (str): The operation to get info for
        Returns:
            Dict[str, Any]: Empty rate limit information
        """
        return {
            "operation": operation,
            "current_usage": 0,
            "max_rate": float('inf'),
            "time_window": 0,
            "operation_limit": float('inf'),
            "remaining": float('inf'),
            "is_rate_limited": False,
            "type": "no_op"
        }

    def is_rate_limited(self, operation: str = "default") -> bool:
        """
        Always return False - never rate limited.
        Args:
            operation (str): The operation to check
        Returns:
            bool: Always False
        """
        return False

    async def reset(self, operation: str = "default") -> None:
        """
        No-op reset - nothing to reset.
        Args:
            operation (str): The operation to reset
        """
        self.logger.debug(f"No-op rate limiter: reset called for operation '{operation}'")
