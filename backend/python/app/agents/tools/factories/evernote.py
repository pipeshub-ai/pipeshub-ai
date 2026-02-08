"""
Client factories for Evernote.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.evernote.evernote import EvernoteClient

# ============================================================================
# Evernote Client Factory
# ============================================================================

class EvernoteClientFactory(ClientFactory):
    """
    Factory for creating Evernote clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> EvernoteClient:
        """
        Create Evernote client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            EvernoteClient instance
        """
        return await EvernoteClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
