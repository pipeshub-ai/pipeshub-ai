"""
Client factories for Notion.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.notion.notion import NotionClient

# ============================================================================
# Notion Client Factory
# ============================================================================

class NotionClientFactory(ClientFactory):
    """
    Factory for creating Notion clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> NotionClient:
        """
        Create Notion client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            NotionClient instance
        """
        return await NotionClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
