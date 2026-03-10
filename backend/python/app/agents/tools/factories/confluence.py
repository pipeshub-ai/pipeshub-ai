"""
Client factories for Confluence.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.confluence.confluence import ConfluenceClient

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """
    Factory for creating Confluence clients.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> ConfluenceClient:
        """
        Create Confluence client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ConfluenceClient instance
        """
        return await ConfluenceClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
