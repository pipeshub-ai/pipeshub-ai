"""
Client factories for Zendesk.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.zendesk.zendesk import ZendeskClient

# ============================================================================
# Zendesk Client Factory
# ============================================================================

class ZendeskClientFactory(ClientFactory):
    """
    Factory for creating Zendesk clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> ZendeskClient:
        """
        Create Zendesk client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ZendeskClient instance
        """
        return await ZendeskClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
