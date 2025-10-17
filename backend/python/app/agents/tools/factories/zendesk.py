"""
Client factories for Zendesk.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.zendesk.zendesk import ZendeskClient

# ============================================================================
# Zendesk Client Factory
# ============================================================================

class ZendeskClientFactory(ClientFactory):
    """Factory for creating Zendesk clients"""

    async def create_client(self, config_service, logger) -> ZendeskClient:
        """Create Zendesk client instance"""

        return await ZendeskClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
