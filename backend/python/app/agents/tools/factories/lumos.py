"""
Client factory for Lumos.
"""

from logging import Logger
from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.lumos.lumos import LumosClient


class LumosClientFactory(ClientFactory):
    """
    Factory for creating Lumos clients.

    - Toolset-based authentication (new architecture): Uses toolset config from etcd
    """

    async def create_client(  # type: ignore[override]
        self,
        config_service: object,
        logger: Logger,
        toolset_config: Dict[str, Any],
        state: object = None,
    ) -> LumosClient:
        """
        Create Lumos client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            toolset_config: Toolset configuration from etcd (REQUIRED)
            state: Chat state (optional)

        Returns:
            LumosClient instance
        """
        return await LumosClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
