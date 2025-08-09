# src/workers/rate_limiter.py
from aiolimiter import AsyncLimiter


class GoogleAPIRateLimiter:
    """Rate limiter for Google Drive API"""

    def __init__(self, max_rate: int = 6000) -> None:
        """
        Initialize rate limiter with Google's default quota

        Args:
            max_rate (int): Maximum requests per 100 seconds (default: 10000)
            Based on Google Drive API quotas
        """
        # Single limiter for all Drive API operations
        # Converting max_rate to per-second rate
        self.google_limiter = AsyncLimiter(max_rate / 100, 1)  # requests per second

    async def __aenter__(self) -> "GoogleAPIRateLimiter":
        await self.google_limiter.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
