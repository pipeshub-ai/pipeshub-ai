"""
Client factories for Freshdesk.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.freshdesk.freshdesk import FreshdeskClient

# ============================================================================
# Freshdesk Client Factory
# ============================================================================

class FreshdeskClientFactory(ClientFactory):
    """Factory for creating Freshdesk clients"""

    async def create_client(self, config_service, logger) -> FreshdeskClient:
        """Create Freshdesk client instance"""

        return await FreshdeskClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
