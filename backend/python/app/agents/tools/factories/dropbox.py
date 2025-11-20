"""
Client factories for Dropbox.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.dropbox.dropbox_ import DropboxClient

# ============================================================================
# Dropbox Client Factory
# ============================================================================

class DropboxClientFactory(ClientFactory):
    """Factory for creating Dropbox clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> DropboxClient:
        """Create Dropbox client instance"""

        return await DropboxClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
