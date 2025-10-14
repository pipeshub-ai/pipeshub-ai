"""
Client factories for Evernote.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.evernote.evernote import EvernoteClient

# ============================================================================
# Evernote Client Factory
# ============================================================================

class EvernoteClientFactory(ClientFactory):
    """Factory for creating Evernote clients"""

    async def create_client(self, config_service, logger) -> EvernoteClient:
        """Create Evernote client instance"""

        return await EvernoteClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
