"""
Client factories for Airtable.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.airtable.airtable import AirtableClient

# ============================================================================
# Airtable Client Factory
# ============================================================================

class AirtableClientFactory(ClientFactory):
    """Factory for creating Airtable clients"""

    async def create_client(self, config_service, logger) -> AirtableClient:
        """Create Airtable client instance"""

        return await AirtableClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
