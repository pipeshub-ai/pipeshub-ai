"""
Client factories for GitLab.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.gitlab.gitlab import GitLabClient

# ============================================================================
# GitLab Client Factory
# ============================================================================

class GitLabClientFactory(ClientFactory):
    """
    Factory for creating GitLab clients.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> GitLabClient:
        """
        Create GitLab client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            GitLabClient instance
        """
        return await GitLabClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
