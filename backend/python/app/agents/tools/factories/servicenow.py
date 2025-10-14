"""
Client factories for ServiceNow.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.servicenow.servicenow import ServiceNowClient

# ============================================================================
# ServiceNow Client Factory
# ============================================================================

class ServiceNowClientFactory(ClientFactory):
    """Factory for creating ServiceNow clients"""

    async def create_client(self, config_service, logger) -> ServiceNowClient:
        """Create ServiceNow client instance"""

        return await ServiceNowClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
