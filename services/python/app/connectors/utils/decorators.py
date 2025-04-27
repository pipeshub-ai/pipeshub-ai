import asyncio
import random
from functools import wraps

from googleapiclient.errors import HttpError

from app.exceptions.connector_google_exceptions import (
    AdminQuotaError,
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

    Args:
        max_retries (int): Maximum number of retry attempts
        initial_delay (float): Initial delay in seconds
        max_delay (float): Maximum delay in seconds
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            delay = initial_delay

            while True:
                try:
                    return await func(*args, **kwargs)

                except HttpError as e:
                    status_code = e.resp.status
                    error_details = {
                        "status_code": status_code,
                        "function": func.__name__,
                        "attempt": retries + 1,
                        "max_retries": max_retries,
                    }

                    # Check if we should retry
                    should_retry = status_code in [429, 403] or (  # Rate limits
                        500 <= status_code <= 599
                    )  # Server errors

                    if not should_retry or retries >= max_retries:
                        if status_code in [429, 403]:
                            raise AdminQuotaError(
                                "API quota exceeded: " + str(e),
                                details={**error_details, "error": str(e)},
                            )
                        else:
                            raise GoogleConnectorError(
                                f"HTTP error {status_code}: " + str(e),
                                details={**error_details, "error": str(e)},
                            )

                    # Calculate delay with jitter
                    jitter = random.uniform(0, 0.1 * delay)
                    retry_after = e.resp.headers.get("Retry-After")

                    if retry_after:
                        delay = float(retry_after)
                    else:
                        delay = min(delay * 2 + jitter, max_delay)

                    await asyncio.sleep(delay)
                    retries += 1

                except Exception as e:
                    raise GoogleConnectorError(
                        "Unexpected error in Google API call: " + str(e),
                        details={"function": func.__name__, "error": str(e)},
                    )

        return wrapper

    return decorator
