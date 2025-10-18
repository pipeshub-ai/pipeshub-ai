"""
Abstract base class for client factories.
"""

import asyncio
import concurrent.futures
from abc import ABC, abstractmethod
from typing import Optional

from app.modules.agents.qna.chat_state import ChatState


class ClientFactory(ABC):
    """Abstract factory for creating tool clients.

    Subclasses must implement create_client() to handle specific client creation.
    """

    @abstractmethod
    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        state: Optional[ChatState] = None
    ) -> object:
        """Create and return a client instance asynchronously.
        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)
        Returns:
            Client instance
        """
        pass

    def create_client_sync(
        self,
        config_service: object,
        logger: Optional[object],
        state: Optional[ChatState] = None
    ) -> object:
        """Synchronous wrapper for client creation.

        Handles both sync and async contexts automatically.

        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)
        Returns:
            Client instance
        """
        try:
            # Check if we're in an async context
            asyncio.get_running_loop()

            # We're in an async context, use thread pool to run async code
            return self._run_in_thread_pool(config_service, logger, state)

        except RuntimeError:
            # No running loop, we can use asyncio.run directly
            return asyncio.run(self.create_client(config_service, logger, state))

    def _run_in_thread_pool(
        self,
        config_service: object,
        logger: Optional[object],
        state: Optional[ChatState] = None
    ) -> object:
        """Run async client creation in a thread pool.

        Args:
            config_service: Configuration service instance
            logger: Logger instance (optional)

        Returns:
            Client instance
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                self.create_client(config_service, logger, state)
            )
            return future.result()
