"""
Client factories for Confluence.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.confluence.confluence import ConfluenceClient

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """Factory for creating Confluence clients"""

    async def create_client(self, config_service, logger, state=None) -> ConfluenceClient:
        """Create Confluence client instance"""

        return await ConfluenceClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
