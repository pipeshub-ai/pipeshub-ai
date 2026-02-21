"""
Client factories for Linear.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.linear.linear import LinearClient

# ============================================================================
# Linear Client Factory
# ============================================================================

class LinearClientFactory(ClientFactory):
    """
    Factory for creating Linear clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> LinearClient:
        """
        Create Linear client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            LinearClient instance
        """
        return await LinearClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
