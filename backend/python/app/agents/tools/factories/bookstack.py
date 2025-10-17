"""
Client factories for BookStack.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.bookstack.bookstack import BookStackClient

# ============================================================================
# BookStack Client Factory
# ============================================================================

class BookStackClientFactory(ClientFactory):
    """Factory for creating BookStack clients"""

    async def create_client(self, config_service, logger) -> BookStackClient:
        """Create BookStack client instance"""

        return await BookStackClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
