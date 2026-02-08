"""
Client factories for Airtable.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.airtable.airtable import AirtableClient

# ============================================================================
# Airtable Client Factory
# ============================================================================

class AirtableClientFactory(ClientFactory):
    """
    Factory for creating Airtable clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> AirtableClient:
        """
        Create Airtable client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            AirtableClient instance
        """
        return await AirtableClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
