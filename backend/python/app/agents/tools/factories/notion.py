"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from typing import Any

from app.agents.tools.factories.base import ClientFactory

# ============================================================================
# Notion Client Factory
# ============================================================================

class NotionClientFactory(ClientFactory):
    """Factory for creating Notion clients"""

    async def create_client(self, config_service, logger) -> Any:
        """Create Notion client instance"""
        from app.sources.client.notion.notion import NotionClient

        return await NotionClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
