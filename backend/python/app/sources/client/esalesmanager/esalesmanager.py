"""eSalesManager client implementation.

This module provides a client for interacting with the eSalesManager API
using API Key (X-API-Key header) authentication.

API Reference: https://api.esalesmanager.jp/v1
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ESalesManagerResponse(BaseModel):
    """Standardized eSalesManager API response wrapper.

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


class ESalesManagerRESTClientViaApiKey(HTTPClient):
    """eSalesManager REST client via API Key (X-API-Key header).

    The API key is sent in the X-API-Key header instead of the standard
    Authorization header.

    Args:
        api_key: The API key
        base_url: API base URL (default: https://api.esalesmanager.jp/v1)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.esalesmanager.jp/v1",
    ) -> None:
        # Initialize with empty token; we set X-API-Key header instead
        super().__init__("", token_type="Bearer")
        self.base_url = base_url
        self.api_key = api_key
        # Remove the default Authorization header and set X-API-Key
        _ = self.headers.pop("Authorization", None)
        self.headers["X-API-Key"] = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class ESalesManagerApiKeyConfig(BaseModel):
    """Configuration for eSalesManager client via API Key.

    Args:
        api_key: The API key
        base_url: API base URL (default: https://api.esalesmanager.jp/v1)
    """

    api_key: str
    base_url: str = "https://api.esalesmanager.jp/v1"

    def create_client(self) -> ESalesManagerRESTClientViaApiKey:
        return ESalesManagerRESTClientViaApiKey(self.api_key, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class ESalesManagerAuthConfig(BaseModel):
    """Auth section of the eSalesManager connector configuration from etcd."""

    apiKey: str | None = None
    apiToken: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class ESalesManagerCredentialsConfig(BaseModel):
    """Credentials section of the eSalesManager connector configuration."""

    api_key: str | None = None

    class Config:
        extra = "allow"


class ESalesManagerConnectorConfig(BaseModel):
    """Top-level eSalesManager connector configuration from etcd."""

    auth: ESalesManagerAuthConfig = Field(
        default_factory=ESalesManagerAuthConfig
    )
    credentials: ESalesManagerCredentialsConfig = Field(
        default_factory=ESalesManagerCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class ESalesManagerClient(IClient):
    """Builder class for eSalesManager clients.

    Supports:
    - API Key (X-API-Key header) authentication
    """

    def __init__(
        self,
        client: ESalesManagerRESTClientViaApiKey,
    ) -> None:
        """Initialize with an eSalesManager client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> ESalesManagerRESTClientViaApiKey:
        """Return the eSalesManager client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: ESalesManagerApiKeyConfig,
    ) -> "ESalesManagerClient":
        """Build ESalesManagerClient with configuration.

        Args:
            config: ESalesManagerApiKeyConfig instance

        Returns:
            ESalesManagerClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ESalesManagerClient":
        """Build ESalesManagerClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ESalesManagerClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get eSalesManager connector configuration"
                )

            connector_config = ESalesManagerConnectorConfig.model_validate(
                raw_config
            )

            api_key = (
                connector_config.auth.apiKey
                or connector_config.auth.apiToken
                or connector_config.auth.token
                or connector_config.credentials.api_key
                or ""
            )
            if not api_key:
                raise ValueError(
                    "API key required for eSalesManager authentication"
                )

            api_key_config = ESalesManagerApiKeyConfig(api_key=api_key)
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build eSalesManager client from services: "
                f"{str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "ESalesManagerClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            ESalesManagerClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(
                credentials.get("api_key", "")
                or auth_config.get("apiKey", "")
                or auth_config.get("apiToken", "")
                or auth_config.get("token", "")
            )
            if not api_key:
                raise ValueError(
                    "API key not found in toolset config"
                )

            api_key_config = ESalesManagerApiKeyConfig(api_key=api_key)
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build eSalesManager client from toolset: "
                f"{str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for eSalesManager."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get eSalesManager connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get eSalesManager connector config: {e}"
            )
            raise ValueError(
                f"Failed to get eSalesManager connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
