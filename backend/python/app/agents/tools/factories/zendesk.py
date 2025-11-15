"""
Client factories for Zendesk.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.zendesk.zendesk import ZendeskClient

# ============================================================================
# Zendesk Client Factory
# ============================================================================

class ZendeskClientFactory(ClientFactory):
    """Factory for creating Zendesk clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> ZendeskClient:
        """Create Zendesk client instance"""

        return await ZendeskClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
