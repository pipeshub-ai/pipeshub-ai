"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.microsoft.microsoft import MSGraphClient

# ============================================================================
# Microsoft Graph Client Factory
# ============================================================================

class MSGraphClientFactory(ClientFactory):
    """
    Factory for creating Microsoft Graph clients.

    Supports both toolset-based and connector-based authentication.

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

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> MSGraphClient:
        """
        Create Microsoft Graph client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            MSGraphClient instance
        """
        return await MSGraphClient.build_from_toolset(
            toolset_config=toolset_config,
            service_name=self.service_name,
            logger=logger,
            config_service=config_service,
        )

