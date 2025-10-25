"""
Client factories for Linear.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.linear.linear import LinearClient

# ============================================================================
# Linear Client Factory
# ============================================================================

class LinearClientFactory(ClientFactory):
    """Factory for creating Linear clients"""

    async def create_client(self, config_service, logger, state=None) -> LinearClient:
        """Create Linear client instance"""

        return await LinearClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
