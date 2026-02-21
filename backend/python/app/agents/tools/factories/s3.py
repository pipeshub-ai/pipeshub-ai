"""
Client factories for S3.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.s3.s3 import S3Client

# ============================================================================
# S3 Client Factory
# ============================================================================

class S3ClientFactory(ClientFactory):
    """
    Factory for creating S3 clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> S3Client:
        """
        Create S3 client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            S3Client instance
        """
        return await S3Client.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
