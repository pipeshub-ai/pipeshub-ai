"""
Client factories for PostHog.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.posthog.posthog import PostHogClient

# ============================================================================
# PostHog Client Factory
# ============================================================================

class PostHogClientFactory(ClientFactory):
    """Factory for creating PostHog clients"""

    async def create_client(self, config_service, logger) -> PostHogClient:
        """Create PostHog client instance"""

        return await PostHogClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
