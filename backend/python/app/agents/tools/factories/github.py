"""
Client factories for GitHub.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.github.github import GitHubClient

# ============================================================================
# GitHub Client Factory
# ============================================================================

class GitHubClientFactory(ClientFactory):
    """Factory for creating GitHub clients"""

    async def create_client(self, config_service, logger) -> GitHubClient:
        """Create GitHub client instance"""

        return await GitHubClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
