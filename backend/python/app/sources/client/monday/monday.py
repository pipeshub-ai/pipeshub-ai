import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class MondayResponse(BaseModel):
    """Standardized Monday API response wrapper"""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None


@dataclass
class MondayConfig:
    """Configuration for Monday client.

    Args:
        base_url: Base URL for Monday API
        token: API token / access token

    """

    base_url: str
    token: str

    def create_client(self) -> HTTPClient:
        return HTTPClient(self.token, "Bearer")


class MondayClient(IClient):
    """Client wrapper for Monday"""

    def __init__(self, client: HTTPClient, base_url: str) -> None:
        if not base_url:
            raise ValueError("Monday base_url cannot be empty")

        self.client = client
        self.base_url = base_url.rstrip("/")

    def get_client(self) -> HTTPClient:
        return self.client

    def get_base_url(self) -> str:
        return self.base_url

    @classmethod
    def build_with_config(
        cls,
        config: MondayConfig,
    ) -> "MondayClient":
        return cls(
            client=config.create_client(),
            base_url=config.base_url,
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "MondayClient":
        """Build MondayClient using configuration service"""
        try:
            config = await config_service.get_config(
                "/services/connectors/monday/config",
            )

            if not config:
                raise ValueError("Failed to get Monday connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found for Monday")

            base_url = config.get("base_url") or config.get("baseUrl")
            if not base_url:
                raise ValueError("Base URL not found in Monday configuration")

            token = (
                auth_config.get("token")
                or auth_config.get("accessToken")
                or auth_config.get("access_token")
            )

            if not token:
                raise ValueError("Token/access token required for Monday")

            client = HTTPClient(token, "Bearer")

            logger.info("Successfully created Monday client")
            return cls(client=client, base_url=base_url)

        except Exception as e:
            logger.error(f"Failed to build Monday client from services: {e}")
            raise
