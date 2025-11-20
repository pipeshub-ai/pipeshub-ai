"""
Client factories for Notion.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.notion.notion import NotionClient

# ============================================================================
# Notion Client Factory
# ============================================================================

class NotionClientFactory(ClientFactory):
    """Factory for creating Notion clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> NotionClient:
        """Create Notion client instance"""

        return await NotionClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
