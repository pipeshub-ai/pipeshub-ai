"""
Client factories for Dropbox.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.dropbox.dropbox_ import DropboxClient

# ============================================================================
# Dropbox Client Factory
# ============================================================================

class DropboxClientFactory(ClientFactory):
    """
    Factory for creating Dropbox clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> DropboxClient:
        """
        Create Dropbox client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            DropboxClient instance
        """
        return await DropboxClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
