"""
Client factories for Slack.
"""

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.slack.slack import SlackClient

# ============================================================================
# Slack Client Factory
# ============================================================================

class SlackClientFactory(ClientFactory):
    """Factory for creating Slack clients"""

    async def create_client(self, config_service, logger, state=None) -> SlackClient:
        """Create Slack client instance"""

        return await SlackClient.build_from_services(
            logger=logger,
            config_service=config_service
        )

