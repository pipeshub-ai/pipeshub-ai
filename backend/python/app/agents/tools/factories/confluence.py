"""
Client factories for Confluence.
"""

from typing import Any, Optional

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.confluence.confluence import ConfluenceClient

# ============================================================================
# Confluence Client Factory
# ============================================================================

class ConfluenceClientFactory(ClientFactory):
    """
    Factory for creating Confluence clients.
    """

    async def create_client(
        self,
        config_service: object,
        logger: Optional[object],
        toolset_config: dict[str, Any],
        state: Optional[ChatState] = None
    ) -> ConfluenceClient:
        """
        Create Confluence client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ConfluenceClient instance
        """
        return await ConfluenceClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
            config_service=config_service,
        )
