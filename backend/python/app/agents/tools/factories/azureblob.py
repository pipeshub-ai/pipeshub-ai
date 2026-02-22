"""
Client factories for Azure Blob.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.azure.azure_blob import AzureBlobClient

# ============================================================================
# Azure Blob Client Factory
# ============================================================================

class AzureBlobClientFactory(ClientFactory):
    """
    Factory for creating Azure Blob clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> AzureBlobClient:
        """
        Create Azure Blob client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            AzureBlobClient instance
        """
        return await AzureBlobClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
