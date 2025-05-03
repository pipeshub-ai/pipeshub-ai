import asyncio
import random
from functools import wraps

from app.exceptions.connector_google_exceptions import (
    GoogleAuthError,
    GoogleConnectorError,
)


def token_refresh(func):
    """Decorator to check and refresh token before API call"""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            # Skip token refresh for delegated credentials
            has_is_delegated = hasattr(self, "is_delegated")
            if has_is_delegated:
                is_delegated_true = self.is_delegated
                if not is_delegated_true:
                    await self._check_and_refresh_token()
            return await func(self, *args, **kwargs)
        except Exception as e:
            raise GoogleAuthError(
                "Token refresh failed: " + str(e),
                details={"function": func.__name__, "error": str(e)},
            )

    return wrapper


def exponential_backoff(
    max_retries: int = 5, initial_delay: float = 1.0, max_delay: float = 32.0
):
    """
    Decorator implementing exponential backoff for rate limiting and server errors.
    Works with existing error conversion in methods.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            delay = initial_delay
            last_exception = None

            while retries <= max_retries:
                try:
                    return await func(*args, **kwargs)

                except GoogleConnectorError as e:
                    # This will catch all our custom Google exceptions
                    if retries >= max_retries:
                        raise  # If out of retries, let the converted error propagate
                    last_exception = e

                    # Calculate delay with jitter
                    jitter = random.uniform(0, 0.1 * delay)
                    delay = min(delay * 2 + jitter, max_delay)
                    await asyncio.sleep(delay)
                    retries += 1

                except Exception:
                    # For any non-Google exceptions, raise immediately
                    raise

            # If we somehow exit the loop without raising or returning
            raise last_exception or GoogleConnectorError(
                "Unexpected exit from retry loop",
                details={"function": func.__name__},
            )

        return wrapper
    return decorator
