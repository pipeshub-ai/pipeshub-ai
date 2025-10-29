"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.microsoft.microsoft import MSGraphClient

# ============================================================================
# Microsoft Graph Client Factory
# ============================================================================

class MSGraphClientFactory(ClientFactory):
    """
    Factory for creating Microsoft Graph clients.

    Attributes:
        service_name: Name of Microsoft service (one_drive, sharepoint, etc.)
    """

    def __init__(self, service_name: str) -> None:
        """
        Initialize Microsoft Graph client factory.

        Args:
            service_name: Name of Microsoft service
        """
        self.service_name = service_name

    async def create_client(self, config_service, logger, state=None) -> MSGraphClient:
        """Create Microsoft Graph client instance"""
        from app.sources.client.microsoft.microsoft import GraphMode

        return await MSGraphClient.build_from_services(
            service_name=self.service_name,
            logger=logger,
            config_service=config_service,
            mode=GraphMode.APP
        )

