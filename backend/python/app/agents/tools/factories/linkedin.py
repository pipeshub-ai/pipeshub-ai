"""
Client factories for LinkedIn.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.linkedin.linkedin import LinkedInClient

# ============================================================================
# LinkedIn Client Factory
# ============================================================================

class LinkedInClientFactory(ClientFactory):
    """
    Factory for creating LinkedIn clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> LinkedInClient:
        """
        Create LinkedIn client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            LinkedInClient instance
        """
        return await LinkedInClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
