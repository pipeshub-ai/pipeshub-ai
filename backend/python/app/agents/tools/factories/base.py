"""
Abstract base class for client factories.
"""

import asyncio
import concurrent.futures
from abc import ABC, abstractmethod
from typing import Any


class ClientFactory(ABC):
    """
    Abstract factory for creating tool clients.

    Subclasses must implement create_client() to handle specific client creation.
    """

    @abstractmethod
    async def create_client(self, config_service, logger) -> Any:
        """
        Create and return a client instance asynchronously.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            Client instance
        """
        pass

    def create_client_sync(self, config_service, logger) -> Any:
        """
        Synchronous wrapper for client creation.
        Handles both sync and async contexts automatically.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            Client instance
        """
        try:
            # Check if we're in an async context
            asyncio.get_running_loop()

            # We're in an async context, use thread pool to run async code
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    self.create_client(config_service, logger)
                )
                return future.result()

        except RuntimeError:
            # No running loop, we can use asyncio.run directly
            return asyncio.run(self.create_client(config_service, logger))
