"""
Client factories for Slack.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.slack.slack import SlackClient

# ============================================================================
# Slack Client Factory
# ============================================================================

class SlackClientFactory(ClientFactory):
    """
    Factory for creating Slack clients.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> SlackClient:
        """
        Create Slack client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            SlackClient instance
        """
        return await SlackClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )

