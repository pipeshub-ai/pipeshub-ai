"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from typing import Any

from app.agents.tools.factories.base import ClientFactory

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """Factory for creating Confluence clients"""

    async def create_client(self, config_service, logger) -> Any:
        """Create Confluence client instance"""
        from app.sources.client.confluence.confluence import ConfluenceClient

        return await ConfluenceClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
