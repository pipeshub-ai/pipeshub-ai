"""
Client factories for GitHub.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.github.github import GitHubClient

# ============================================================================
# GitHub Client Factory
# ============================================================================

class GitHubClientFactory(ClientFactory):
    """Factory for creating GitHub clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> GitHubClient:
        """Create GitHub client instance"""

        return await GitHubClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
