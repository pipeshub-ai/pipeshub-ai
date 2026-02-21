"""
Shared utilities for agent actions.

This module provides common utilities for running async operations
from synchronous tool contexts, reducing code duplication across
agent action classes.
"""

import asyncio
import concurrent.futures
import logging
from typing import Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Coroutine[None, None, T]) -> T:
    """Run an async coroutine from a synchronous context.

    Uses the main FastAPI event loop when available.
    Falls back to creating a temporary loop only if absolutely necessary.

    Args:
        coro: Coroutine to run

    Returns:
        Result of the coroutine

    Raises:
        Exception: Any exception raised by the coroutine
    """
    try:
        # First, check if we're already in an async context
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context - schedule on the same loop
            # This shouldn't happen for sync tools, but handle it gracefully
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop - we're in sync context
            # Get the event loop for this thread (should be the main FastAPI loop)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    # Loop is closed - this shouldn't happen in FastAPI, but handle it
                    raise RuntimeError("Event loop is closed")

                # Use the main loop - schedule work on it from sync context
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                return future.result()
            except RuntimeError:
                # No event loop for this thread - only happens in tests or edge cases
                # Create a temporary loop as last resort
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()
                    asyncio.set_event_loop(None)

    except Exception as e:
        logger.error(f"Error running async operation: {e}")
        raise


class AsyncRunnerMixin:
    """Mixin class for running async operations via the main event loop.

    This mixin uses the main FastAPI event loop instead of creating
    a separate background loop, simplifying event loop management.

    Usage:
        class MyTool(AsyncRunnerMixin):
            def __init__(self, client):
                super().__init__()
                self.client = client

            def some_method(self):
                result = self._run_async(self.client.async_method())
                return result
    """

    def __init__(self) -> None:
        """Initialize the mixin (no background loop needed)."""
        # No initialization needed - we use the main loop
        pass

    def _run_async(self, coro: Coroutine[None, None, T]) -> T:
        """Run a coroutine safely from sync context via the main event loop.

        Args:
            coro: Coroutine to run

        Returns:
            Result of the coroutine

        Raises:
            Exception: Any exception raised by the coroutine
        """
        # Just use run_async - it handles everything
        return run_async(coro)

    def shutdown(self) -> None:
        """Gracefully stop (no-op since we use main loop)."""
        # No cleanup needed - we don't own the loop
        pass

