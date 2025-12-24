"""
Client factories for Confluence.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.confluence.confluence import ConfluenceClient

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """Factory for creating Confluence clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> ConfluenceClient:
        """Create Confluence client instance"""

        return await ConfluenceClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
