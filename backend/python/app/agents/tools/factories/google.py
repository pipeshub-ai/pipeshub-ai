"""
Google client factory for creating Google service clients.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.google.google import GoogleClient


class GoogleClientFactory(ClientFactory):
    """
    Factory for creating Google service clients.

    - Toolset-based authentication (new architecture): Uses toolset config from etcd

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

    async def create_client(
        self,
        config_service,
        logger,
        toolset_config: Dict[str, Any],
        state: ChatState | None = None
    ) -> GoogleClient:
        """
        Create Google client instance from toolset configuration.
        Args:
            config_service: Configuration service instance
            logger: Logger instance
            state: Chat state (optional)
            toolset_config: Toolset configuration from etcd (REQUIRED)

        Returns:
            Google client instance
        """
        client = await GoogleClient.build_from_toolset(
            toolset_config=toolset_config,
            service_name=self.service_name,
            logger=logger,
            config_service=config_service,
            version=self.version,
        )
        return client.get_client()
