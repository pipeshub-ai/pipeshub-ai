"""
Client factories for FreshDesk.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.freshdesk.freshdesk import FreshDeskClient

# ============================================================================
# FreshDesk Client Factory
# ============================================================================

class FreshDeskClientFactory(ClientFactory):
    """Factory for creating FreshDesk clients"""

    async def create_client(self, config_service, logger) -> FreshDeskClient:
        """Create FreshDesk client instance"""

        return await FreshDeskClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
