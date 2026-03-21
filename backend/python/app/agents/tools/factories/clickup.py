"""
Client factory for ClickUp.
"""

from logging import Logger
from typing import Any

from app.agents.tools.factories.base import ClientFactory
from app.config.configuration_service import ConfigurationService
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.clickup.clickup import ClickUpClient


class ClickUpClientFactory(ClientFactory):
    """
    Factory for creating ClickUp clients.

    Supports toolset-based OAuth authentication.
    """

    async def create_client(
        self,
        config_service: ConfigurationService,
        logger: Logger,
        toolset_config: dict[str, Any],
        state: ChatState | None = None,
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
