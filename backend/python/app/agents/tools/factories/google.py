"""
Google client factory for creating Google service clients.
"""

from app.agents.tools.factories.base import ClientFactory
from app.modules.agents.qna.chat_state import ChatState
from app.sources.client.google.google import GoogleClient


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

    async def create_client(self, config_service, logger, state: ChatState | None = None) -> GoogleClient:
        """
        Create Google client instance.

        Args:
            config_service: Configuration service instance
            logger: Logger instance

        Returns:
            Google client instance
        """

        # Determine impersonation based on chat state (org account type)
        is_individual = True
        user_email = None
        if state and isinstance(state, dict):
            org_info = state.get("org_info") or {}
            account_type = str((org_info or {}).get("accountType", "")).lower()
            is_individual = account_type != "enterprise"
            if not is_individual:
                user_email = str(state.get("user_info", {}).get("userEmail", "")).strip() or None

        client = await GoogleClient.build_from_services(
            service_name=self.service_name,
            logger=logger,
            config_service=config_service,
            is_individual=is_individual,
            version=self.version,
            user_email=user_email,
        )
        logger.info(f"Created Google client for service {self.service_name} with user email {user_email}")

        return client.get_client()
