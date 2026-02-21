"""
Client factories for FreshDesk.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.freshdesk.freshdesk import FreshDeskClient

# ============================================================================
# FreshDesk Client Factory
# ============================================================================

class FreshDeskClientFactory(ClientFactory):
    """
    Factory for creating FreshDesk clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> FreshDeskClient:
        """
        Create FreshDesk client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            FreshDeskClient instance
        """
        return await FreshDeskClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
