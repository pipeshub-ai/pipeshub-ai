"""
Client factories for Box.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.box.box import BoxClient

# ============================================================================
# Box Client Factory
# ============================================================================

class BoxClientFactory(ClientFactory):
    """Factory for creating Box clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> BoxClient:
        """Create Box client instance"""

        return await BoxClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
