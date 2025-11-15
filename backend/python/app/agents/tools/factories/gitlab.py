"""
Client factories for GitLab.
"""


from typing import Optional

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.gitlab.gitlab import GitLabClient

# ============================================================================
# GitLab Client Factory
# ============================================================================

class GitLabClientFactory(ClientFactory):
    """Factory for creating GitLab clients"""

    async def create_client(self, config_service, logger, state=None, connector_instance_id: Optional[str] = None) -> GitLabClient:
        """Create GitLab client instance"""

        return await GitLabClient.build_from_services(
            logger=logger,
            config_service=config_service,
            connector_instance_id=connector_instance_id
        )
