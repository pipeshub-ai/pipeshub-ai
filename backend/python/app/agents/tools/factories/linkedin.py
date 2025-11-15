"""
Client factories for LinkedIn.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.linkedin.linkedin import LinkedInClient

# ============================================================================
# LinkedIn Client Factory
# ============================================================================

class LinkedInClientFactory(ClientFactory):
    """Factory for creating LinkedIn clients"""

    async def create_client(self, config_service, logger, connector_instance_id: Optional[str] = None) -> LinkedInClient:
        """Create LinkedIn client instance"""

        return await LinkedInClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
