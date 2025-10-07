"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""

from typing import Any

from app.agents.tools.factories.base import ClientFactory

# ============================================================================
# Jira Client Factory
# ============================================================================

class JiraClientFactory(ClientFactory):
    """Factory for creating Jira clients"""

    async def create_client(self, config_service, logger) -> Any:
        """Create Jira client instance"""
        from app.sources.client.jira.jira import JiraClient

        return await JiraClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
