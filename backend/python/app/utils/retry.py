"""Generic async retry-with-backoff for side effects that must not be lost.

Use this to retry a single side effect (e.g. publishing an event) independently
of the work that already committed (e.g. a DB transaction), instead of
re-running the whole operation or swallowing the failure.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional, TypeVar

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.2,
    logger: Optional[logging.Logger] = None,
    description: str = "operation",
) -> T:
    """Retry an async zero-arg callable with exponential backoff.

    Backoff schedule with defaults: 0.2s, 0.4s, ... (base_delay * 2**attempt).
    Re-raises the last exception if every attempt fails.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_exception: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except Exception as e:
            last_exception = e
            if logger:
                logger.warning(
                    "Retryable %s failed (attempt %d/%d): %s",
                    description, attempt, max_attempts, e,
                )
            if attempt < max_attempts:
                await asyncio.sleep(base_delay_seconds * (2 ** (attempt - 1)))

    raise last_exception
