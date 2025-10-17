"""
Client factories for Discord.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.discord.discord import DiscordClient

# ============================================================================
# Discord Client Factory
# ============================================================================

class DiscordClientFactory(ClientFactory):
    """Factory for creating Discord clients"""

    async def create_client(self, config_service, logger) -> DiscordClient:
        """Create Discord client instance"""

        return await DiscordClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
