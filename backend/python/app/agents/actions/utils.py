"""
Shared utilities for agent actions.

This module provides common utilities for running async operations
from synchronous tool contexts, reducing code duplication across
agent action classes.
"""

import asyncio
import concurrent.futures
import logging
import threading
from typing import Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Coroutine[None, None, T]) -> T:
    """Run an async coroutine from a synchronous context.

    This utility handles both sync and async contexts automatically.
    For tools that make frequent async calls, consider using
    AsyncRunnerMixin with a background event loop instead for better performance.

    Args:
        coro: Coroutine to run

    Returns:
        Result of the coroutine

    Raises:
        Exception: Any exception raised by the coroutine
    """
    try:
        # Try to get or create an event loop for this thread
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context - cannot use run_until_complete
            # This shouldn't happen since tools are sync, but handle it
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop - we can safely use get_event_loop or create one
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    # Loop is closed, create a new one
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                # No event loop at all - create a new one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            return loop.run_until_complete(coro)
    except Exception as e:
        logger.error(f"Error running async operation: {e}")
        raise


class AsyncRunnerMixin:
    """Mixin class for running async operations via a dedicated background event loop.

    This mixin provides a more efficient way to run async operations for tools
    that make frequent async calls. It creates a dedicated background event loop
    in a separate thread.

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
        """Initialize the background event loop."""
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, T]) -> T:
        """Run a coroutine safely from sync context via a dedicated loop.

        Args:
            coro: Coroutine to run

        Returns:
            Result of the coroutine

        Raises:
            Exception: Any exception raised by the coroutine
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"{self.__class__.__name__} shutdown encountered an issue: {exc}")

