"""
Client factories for Jira, Confluence, Slack, Microsoft, and Notion.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.jira.jira import JiraClient

# ============================================================================
# Jira Client Factory
# ============================================================================

class JiraClientFactory(ClientFactory):
    """Factory for creating Jira clients"""

    async def create_client(self, config_service, logger) -> JiraClient:
        """Create Jira client instance"""

        return await JiraClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
