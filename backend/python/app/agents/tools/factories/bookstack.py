"""
Client factories for BookStack.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.bookstack.bookstack import BookStackClient

# ============================================================================
# BookStack Client Factory
# ============================================================================

class BookStackClientFactory(ClientFactory):
    """
    Factory for creating BookStack clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> BookStackClient:
        """
        Create BookStack client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            BookStackClient instance
        """
        return await BookStackClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
