"""
Client factories for Salesforce.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.salesforce.salesforce import SalesforceClient


class SalesforceClientFactory(ClientFactory):
    """Factory for creating Salesforce clients."""

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> SalesforceClient:
        """
        Create Salesforce client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            SalesforceClient instance
        """
        return await SalesforceClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
            config_service=config_service,
        )
