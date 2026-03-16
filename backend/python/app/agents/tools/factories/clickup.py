"""
Client factory for ClickUp.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.clickup.clickup import ClickUpClient


class ClickUpClientFactory(ClientFactory):
    """
    Factory for creating ClickUp clients.

    Supports toolset-based OAuth authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> ClickUpClient:
        """
        Create ClickUp client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ClickUpClient instance
        """
        return await ClickUpClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
            config_service=config_service,
        )
