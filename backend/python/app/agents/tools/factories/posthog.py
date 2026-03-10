"""
Client factories for PostHog.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.posthog.posthog import PostHogClient

# ============================================================================
# PostHog Client Factory
# ============================================================================

class PostHogClientFactory(ClientFactory):
    """
    Factory for creating PostHog clients.

    Supports both toolset-based and connector-based authentication.
    """

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state=None
    ) -> PostHogClient:
        """
        Create PostHog client instance from toolset configuration.

        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            PostHogClient instance
        """
        return await PostHogClient.build_from_toolset(
            toolset_config=toolset_config,
            logger=logger,
        )
