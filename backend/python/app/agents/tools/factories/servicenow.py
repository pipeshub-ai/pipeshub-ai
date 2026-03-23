"""
Client factories for ServiceNow.
"""


from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.servicenow.servicenow import ServiceNowClient

# ============================================================================
# ServiceNow Client Factory
# ============================================================================

class ServiceNowClientFactory(ClientFactory):
    """Factory for creating ServiceNow clients"""

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> ServiceNowClient:
        """
        Create ServiceNow client instance from toolset configuration.
        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            ServiceNowClient instance
        """
        return await ServiceNowClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
