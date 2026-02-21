"""
Client factories for Box.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.box.box import BoxClient

# ============================================================================
# Box Client Factory
# ============================================================================

class BoxClientFactory(ClientFactory):
    """
    Factory for creating Box clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> BoxClient:
        """
        Create Box client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            BoxClient instance
        """
        return await BoxClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
