"""
Client factories for Azure Blob.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.azure.azure_blob import AzureBlobClient

# ============================================================================
# Azure Blob Client Factory
# ============================================================================

class AzureBlobClientFactory(ClientFactory):
    """Factory for creating Azure Blob clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> AzureBlobClient:
        """Create Azure Blob client instance"""

        return await AzureBlobClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
