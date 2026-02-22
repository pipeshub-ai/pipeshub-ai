"""
Client factories for Discord.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.discord.discord import DiscordClient

# ============================================================================
# Discord Client Factory
# ============================================================================

class DiscordClientFactory(ClientFactory):
    """
    Factory for creating Discord clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> DiscordClient:
        """
        Create Discord client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            DiscordClient instance
        """
        return await DiscordClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
