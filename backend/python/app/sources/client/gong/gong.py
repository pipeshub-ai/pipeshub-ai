"""Gong API Client

This module provides a client for interacting with the Gong API.
Gong is a revenue intelligence platform that captures and analyzes sales conversations.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class GongRESTClientViaApiKey(HTTPClient):
    """Gong REST client via API key
    Args:
        access_key: The access key to use for authentication
        access_key_secret: The access key secret to use for authentication
    """

    def __init__(self, access_key: str, access_key_secret: str) -> None:
        # Gong uses Basic Auth with access key and secret
        import base64
        credentials = f"{access_key}:{access_key_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        super().__init__(encoded_credentials, "Basic")
        self.base_url = "https://api.gong.io/v2"

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


@dataclass
class GongApiKeyConfig:
    """Configuration for Gong REST client via API key
    Args:
        access_key: The access key to use for authentication
        access_key_secret: The access key secret to use for authentication
        ssl: Whether to use SSL
    """

    access_key: str
    access_key_secret: str
    ssl: bool = True

    def create_client(self) -> GongRESTClientViaApiKey:
        return GongRESTClientViaApiKey(self.access_key, self.access_key_secret)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class GongClient(IClient):
    """Builder class for Gong clients with different construction methods"""

    def __init__(self, client: GongRESTClientViaApiKey) -> None:
        """Initialize with a Gong client object"""
        self.client = client

    def get_client(self) -> GongRESTClientViaApiKey:
        """Return the Gong client object"""
        return self.client

    @classmethod
    def build_with_config(cls, config: GongApiKeyConfig) -> "GongClient":
        """Build GongClient with configuration

        Args:
            config: GongApiKeyConfig instance
        Returns:
            GongClient instance

        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "GongClient":
        """Build GongClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            GongClient instance
        """
        try:
            # Get Gong configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Gong connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Gong connector configuration")

            credentials_config = config.get("credentials", {}) or {}
            if not credentials_config:
                raise ValueError("Credentials configuration not found in Gong connector configuration")

            # Extract configuration values
            auth_type = auth_config.get("authType", "API_KEY")

            if auth_type == "API_KEY":
                access_key = auth_config.get("accessKey", "")
                access_key_secret = auth_config.get("accessKeySecret", "")
                if not access_key or not access_key_secret:
                    raise ValueError("Access key and secret required for API key auth type")
                client = GongRESTClientViaApiKey(access_key, access_key_secret)
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Gong client from services: {e!s}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> dict[str, Any]:
        """Fetch connector config from etcd for Gong."""
        try:
            config = await config_service.get_config("/services/connectors/gong/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Gong connector config: {e}")
            return {}
