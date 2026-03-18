"""Greenhouse client implementation.

This module provides clients for interacting with the Greenhouse Harvest API
using API Key authentication via HTTP Basic Auth.

Auth Reference: https://developers.greenhouse.io/harvest.html#authentication
API Reference: https://developers.greenhouse.io/harvest.html
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GreenhouseAuthType(str, Enum):
    """Authentication types supported by the Greenhouse connector."""

    API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class GreenhouseResponse(BaseModel):
    """Standardized Greenhouse API response wrapper.

    The data field supports JSON responses (dict/list) and binary file
    downloads (bytes). When serializing to dict/JSON, binary data is
    automatically base64-encoded.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None, description="Response data (JSON) or file content (bytes)"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> dict[str, object]:
        """Convert response to dictionary.

        Binary data is base64-encoded for safe serialization.
        """
        result = self.model_dump(exclude_none=True)
        if isinstance(result.get("data"), bytes):
            result["data"] = base64.b64encode(result["data"]).decode("utf-8")
        return result

    def to_json(self) -> str:
        """Convert response to JSON string.

        Binary data is base64-encoded for safe serialization.
        """
        if isinstance(self.data, bytes):
            result = self.model_dump(exclude_none=True)
            result["data"] = base64.b64encode(self.data).decode("utf-8")
            return json.dumps(result)
        return self.model_dump_json(exclude_none=True)


# ---------------------------------------------------------------------------
# REST client class
# ---------------------------------------------------------------------------


class GreenhouseRESTClientViaApiKey(HTTPClient):
    """Greenhouse REST client via API Key.

    Greenhouse uses HTTP Basic Auth with the API key as the username
    and an empty string as the password.

    Args:
        api_key: The Greenhouse Harvest API key
    """

    def __init__(self, api_key: str) -> None:
        # Greenhouse uses Basic auth with API key as username, empty password
        credentials = f"{api_key}:"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize HTTPClient with Basic token
        super().__init__(encoded_credentials, "Basic")
        self.base_url = "https://harvest.greenhouse.io/v1"
        self.api_key = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class GreenhouseApiKeyConfig(BaseModel):
    """Configuration for Greenhouse client via API Key.

    Args:
        api_key: The Greenhouse Harvest API key for authentication
    """

    api_key: str

    def create_client(self) -> GreenhouseRESTClientViaApiKey:
        """Create Greenhouse REST client."""
        return GreenhouseRESTClientViaApiKey(self.api_key)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class GreenhouseAuthConfig(BaseModel):
    """Auth section of the Greenhouse connector configuration from etcd."""

    authType: GreenhouseAuthType = GreenhouseAuthType.API_KEY
    apiKey: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class GreenhouseCredentialsConfig(BaseModel):
    """Credentials section of the Greenhouse connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class GreenhouseConnectorConfig(BaseModel):
    """Top-level Greenhouse connector configuration from etcd."""

    auth: GreenhouseAuthConfig = Field(default_factory=GreenhouseAuthConfig)
    credentials: GreenhouseCredentialsConfig = Field(
        default_factory=GreenhouseCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class GreenhouseClient(IClient):
    """Builder class for Greenhouse clients.

    Supports:
    - API Key authentication via HTTP Basic Auth
    """

    def __init__(
        self,
        client: GreenhouseRESTClientViaApiKey,
    ) -> None:
        """Initialize with a Greenhouse client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> GreenhouseRESTClientViaApiKey:
        """Return the Greenhouse client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: GreenhouseApiKeyConfig,
    ) -> "GreenhouseClient":
        """Build GreenhouseClient with configuration.

        Args:
            config: GreenhouseApiKeyConfig instance

        Returns:
            GreenhouseClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GreenhouseClient":
        """Build GreenhouseClient using configuration service.

        Supports API Key authentication strategy:
        1. API_KEY: API key passed via HTTP Basic Auth

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            GreenhouseClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Greenhouse connector configuration"
                )

            connector_config = GreenhouseConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == GreenhouseAuthType.API_KEY:
                api_key = connector_config.auth.apiKey or ""
                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )

                api_key_config = GreenhouseApiKeyConfig(api_key=api_key)
                return cls(api_key_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Greenhouse client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Greenhouse."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Greenhouse connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Greenhouse connector config: {e}")
            raise ValueError(
                f"Failed to get Greenhouse connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
