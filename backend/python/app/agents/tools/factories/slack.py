"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from typing import Any

from app.agents.tools.factories.base import ClientFactory

# ============================================================================
# Slack Client Factory
# ============================================================================

class SlackClientFactory(ClientFactory):
    """Factory for creating Slack clients"""

    async def create_client(self, config_service, logger) -> Any:
        """Create Slack client instance"""
        from app.sources.client.slack.slack import SlackClient

        return await SlackClient.build_from_services(
            logger=logger,
            config_service=config_service
        )

