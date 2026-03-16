"""
Client factories for Zoom.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.zoom.zoom import ZoomClient

# ============================================================================
# Zoom Client Factory
# ============================================================================

class ZoomClientFactory(ClientFactory):
    """
    Factory for creating Zoom clients.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
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
