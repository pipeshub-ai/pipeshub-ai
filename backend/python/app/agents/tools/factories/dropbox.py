"""
Client factories for Dropbox.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.dropbox.dropbox_ import DropboxClient

# ============================================================================
# Dropbox Client Factory
# ============================================================================

class DropboxClientFactory(ClientFactory):
    """Factory for creating Dropbox clients"""

    async def create_client(self, config_service, logger, state=None) -> DropboxClient:
        """Create Dropbox client instance"""

        return await DropboxClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
