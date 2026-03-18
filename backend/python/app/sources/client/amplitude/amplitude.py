"""Amplitude client implementation.

This module provides clients for interacting with the Amplitude API using
API Key + Secret Key via HTTP Basic Auth (base64 of "api_key:secret_key").

Amplitude has two base URLs:
1. https://amplitude.com/api/2 (v2 HTTP API)
2. https://analytics.amplitude.com/api/3 (Dashboard REST API)

The client defaults to the v2 base URL and also exposes the v3 base URL
for endpoints that require it.

Authentication Reference: https://www.docs.developers.amplitude.com/analytics/apis/
API Reference: https://www.docs.developers.amplitude.com/analytics/apis/
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


class AmplitudeAuthType(str, Enum):
    """Authentication types supported by the Amplitude connector."""

    API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class AmplitudeResponse(BaseModel):
    """Standardized Amplitude API response wrapper.

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
# REST client classes
# ---------------------------------------------------------------------------


class AmplitudeRESTClientViaApiKey(HTTPClient):
    """Amplitude REST client via API Key + Secret Key.

    Uses HTTP Basic Auth with api_key as username and secret_key as password.
    The credentials are base64-encoded and sent in the Authorization header.

    Args:
        api_key: The Amplitude API key
        secret_key: The Amplitude secret key
    """

    # Base URLs for the two API versions
    BASE_URL_V2 = "https://amplitude.com/api/2"
    BASE_URL_V3 = "https://analytics.amplitude.com/api/3"

    def __init__(self, api_key: str, secret_key: str) -> None:
        # Encode api_key:secret_key as base64 for Basic auth
        credentials = f"{api_key}:{secret_key}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize HTTPClient with Basic token
        super().__init__(encoded_credentials, "Basic")
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = self.BASE_URL_V2
        self.base_url_v3 = self.BASE_URL_V3
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the v2 base URL."""
        return self.base_url

    def get_base_url_v3(self) -> str:
        """Get the v3 (Dashboard REST API) base URL."""
        return self.base_url_v3


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class AmplitudeApiKeyConfig(BaseModel):
    """Configuration for Amplitude REST client via API Key + Secret Key.

    Args:
        api_key: The Amplitude API key
        secret_key: The Amplitude secret key
    """

    api_key: str
    secret_key: str

    def create_client(self) -> AmplitudeRESTClientViaApiKey:
        """Create Amplitude REST client."""
        return AmplitudeRESTClientViaApiKey(self.api_key, self.secret_key)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class AmplitudeAuthConfig(BaseModel):
    """Auth section of the Amplitude connector configuration from etcd."""

    authType: AmplitudeAuthType = AmplitudeAuthType.API_KEY
    apiKey: str | None = None
    secretKey: str | None = None

    class Config:
        extra = "allow"


class AmplitudeConnectorConfig(BaseModel):
    """Top-level Amplitude connector configuration from etcd."""

    auth: AmplitudeAuthConfig = Field(default_factory=AmplitudeAuthConfig)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class AmplitudeClient(IClient):
    """Builder class for Amplitude clients.

    Supports:
    - API Key + Secret Key authentication via HTTP Basic Auth
    """

    def __init__(self, client: AmplitudeRESTClientViaApiKey) -> None:
        """Initialize with an Amplitude client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> AmplitudeRESTClientViaApiKey:
        """Return the Amplitude client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the v2 base URL."""
        return self.client.get_base_url()

    def get_base_url_v3(self) -> str:
        """Return the v3 base URL."""
        return self.client.get_base_url_v3()

    @classmethod
    def build_with_config(
        cls,
        config: AmplitudeApiKeyConfig,
    ) -> "AmplitudeClient":
        """Build AmplitudeClient with configuration.

        Args:
            config: AmplitudeApiKeyConfig instance

        Returns:
            AmplitudeClient instance
        """
        return cls(config.create_client())

    @classmethod
    def build_with_api_key(
        cls,
        api_key: str,
        secret_key: str,
    ) -> "AmplitudeClient":
        """Build AmplitudeClient with API key and secret key directly.

        Args:
            api_key: The Amplitude API key
            secret_key: The Amplitude secret key

        Returns:
            AmplitudeClient instance
        """
        config = AmplitudeApiKeyConfig(api_key=api_key, secret_key=secret_key)
        return cls.build_with_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "AmplitudeClient":
        """Build AmplitudeClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            AmplitudeClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Amplitude connector configuration"
                )

            connector_config = AmplitudeConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == AmplitudeAuthType.API_KEY:
                api_key = connector_config.auth.apiKey or ""
                secret_key = connector_config.auth.secretKey or ""

                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )
                if not secret_key:
                    raise ValueError(
                        "Secret key required for API_KEY auth type"
                    )

                config = AmplitudeApiKeyConfig(
                    api_key=api_key,
                    secret_key=secret_key,
                )
                return cls(config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Amplitude client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "AmplitudeClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            AmplitudeClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(auth_config.get("apiKey", ""))
            secret_key: str = str(auth_config.get("secretKey", ""))

            if not api_key:
                raise ValueError("API key not found in toolset config")
            if not secret_key:
                raise ValueError("Secret key not found in toolset config")

            config = AmplitudeApiKeyConfig(
                api_key=api_key,
                secret_key=secret_key,
            )
            return cls(config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Amplitude client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Amplitude."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Amplitude connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Amplitude connector config: {e}")
            raise ValueError(
                f"Failed to get Amplitude connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
