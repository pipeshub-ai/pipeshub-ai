"""
Client factories for Zoom.
"""

from typing_extensions import override

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.zoom.zoom import ZoomClient

# ============================================================================
# Zoom Client Factory
# ============================================================================


class ZoomClientFactory(ClientFactory):
    """
    Factory for creating Zoom clients.
    """

    @override
    async def create_client(
        self,
        config_service: object,
        logger: object | None,
        toolset_config: dict[str, object],
        state: ChatState | None = None,
    ) -> ZoomClient:
        """
        Create Zoom client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            ZoomClient instance
        """
        return await ZoomClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
