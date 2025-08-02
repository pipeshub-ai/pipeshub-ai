import asyncio
from datetime import datetime


# Uses a token bucket algorithm to limit how many tasks can start per second
# Calculates time passed since last check
# Adds tokens based on the rate
# If tokens are available, consumes one and continues
# If no tokens, waits until tokens accumulate
class RateLimiter:
    """Simple rate limiter to control how many tasks start per second"""

    def __init__(self, rate_limit_per_second) -> None:
        self.rate = rate_limit_per_second
        self.last_check = datetime.now()
        self.tokens = rate_limit_per_second
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        """Wait until a token is available"""
        async with self.lock:
            while True:
                now = datetime.now()
                time_passed = (now - self.last_check).total_seconds()

                # Add new tokens based on time passed
                self.tokens += time_passed * self.rate
                self.last_check = now

                # Cap tokens at the maximum rate
                if self.tokens > self.rate:
                    self.tokens = self.rate

                if self.tokens >= 1:
                    # Consume a token
                    self.tokens -= 1
                    break

                # Wait for some tokens to accumulate
                await asyncio.sleep(1.0 / self.rate)
