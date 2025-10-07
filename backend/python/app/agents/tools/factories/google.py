"""
Google client factory for creating Google service clients.
"""

from typing import Any

from app.agents.tools.factories.base import ClientFactory


class GoogleClientFactory(ClientFactory):
    """
    Factory for creating Google service clients.

    Attributes:
        service_name: Name of Google service (gmail, calendar, drive, etc.)
        version: API version (v1, v3, etc.)
    """

    def __init__(self, service_name: str, version: str = "v3") -> None:
        """
        Initialize Google client factory.

        Args:
            service_name: Name of Google service
            version: API version
        """
        self.service_name = service_name
        self.version = version

    async def create_client(self, config_service, logger) -> Any:
        """
        Create Google client instance.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            Google client instance
        """
        from app.sources.client.google.google import GoogleClient

        client = await GoogleClient.build_from_services(
            service_name=self.service_name,
            logger=logger,
            config_service=config_service,
            is_individual=True,
            version=self.version
        )

        return client.get_client()
