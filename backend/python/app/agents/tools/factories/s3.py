"""
Client factories for S3.
"""


from app.agents.tools.factories.base import ClientFactory
from app.sources.client.s3.s3 import S3Client

# ============================================================================
# S3 Client Factory
# ============================================================================

class S3ClientFactory(ClientFactory):
    """Factory for creating S3 clients"""

    async def create_client(self, config_service, logger) -> S3Client:
        """Create S3 client instance"""

        return await S3Client.build_from_services(
            logger=logger,
            config_service=config_service
        )
