"""
Client factories for GitHub.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.github.github import GitHubClient

# ============================================================================
# GitHub Client Factory
# ============================================================================

class GitHubClientFactory(ClientFactory):
    """
    Factory for creating GitHub clients.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> GitHubClient:
        """
        Create GitHub client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            GitHubClient instance
        """
        return await GitHubClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
