"""
Client factories for Azure Blob.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.azure.azure_blob import AzureBlobClient

# ============================================================================
# Azure Blob Client Factory
# ============================================================================

class AzureBlobClientFactory(ClientFactory):
    """Factory for creating Azure Blob clients"""

    async def create_client(self, config_service, logger) -> AzureBlobClient:
        """Create Azure Blob client instance"""

        return await AzureBlobClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
