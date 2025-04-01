from functools import wraps
import asyncio
import random
from googleapiclient.errors import HttpError
from app.utils.logger import logger

def token_refresh(func):
    """Decorator to check and refresh token before API call"""
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        await self._check_and_refresh_token()
        return await func(self, *args, **kwargs)
    return wrapper

def exponential_backoff(max_retries: int = 5, initial_delay: float = 1.0, max_delay: float = 32.0):
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
            logger.info(
                "🚀 Starting exponential backoff for function: %s", func.__name__)
            logger.debug("Initial parameters - Max Retries: %s, Initial Delay: %s, Max Delay: %s",
                         max_retries, initial_delay, max_delay)

            retries = 0
            delay = initial_delay

            while True:
                try:
                    logger.info(
                        "🔍 Attempting to execute function: %s", func.__name__)
                    result = await func(*args, **kwargs)
                    logger.info(
                        "✅ Successfully executed function: %s", func.__name__)
                    return result

                except HttpError as e:
                    status_code = e.resp.status
                    logger.warning(
                        "🚨 HttpError encountered: Status Code %s", status_code)

                    # Check if we should retry
                    should_retry = (
                        status_code in [429, 403] or  # Rate limits
                        (500 <= status_code <= 599)    # Server errors
                    )

                    if not should_retry or retries >= max_retries:
                        logger.error(
                            "❌ Final error (attempt %s/%s): %s",
                            retries + 1,
                            max_retries,
                            str(e)
                        )
                        raise

                    # Calculate delay with jitter
                    jitter = random.uniform(0, 0.1 * delay)  # 10% jitter
                    logger.debug("🎲 Generated jitter: %s seconds", jitter)

                    retry_after = None

                    if status_code in [429, 403]:
                        # Use Retry-After header if available
                        retry_after = e.resp.headers.get('Retry-After')
                        if retry_after:
                            delay = float(retry_after)
                            logger.info(
                                "📅 Using Retry-After header: %s seconds", delay)
                        else:
                            # Exponential backoff with jitter
                            delay = min(delay * 2 + jitter, max_delay)
                            logger.info(
                                "📈 Calculated exponential backoff delay: %s seconds", delay)

                        logger.warning(
                            "🔄 Rate limit (%s) exceeded. Retrying after %.2f seconds. Attempt %s/%s",
                            status_code,
                            delay,
                            retries + 1,
                            max_retries
                        )
                    else:
                        # Server errors
                        delay = min(delay * 2 + jitter, max_delay)
                        logger.warning(
                            "🔄 Server error %s. Retrying after %.2f seconds. Attempt %s/%s",
                            status_code,
                            delay,
                            retries + 1,
                            max_retries
                        )

                    logger.info(
                        "⏳ Sleeping for %s seconds before retry", delay)
                    await asyncio.sleep(delay)
                    retries += 1
                    logger.info("🔁 Retry attempt %s initiated", retries)

                except Exception as e:
                    logger.error("❌ Unexpected error in %s: %s",
                                 func.__name__, str(e))
                    raise

        return wrapper
    return decorator
