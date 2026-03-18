"""JumpCloud client implementation.

This module provides a client for interacting with the JumpCloud API
using API Key authentication via the ``x-api-key`` header.

Authentication Reference: https://docs.jumpcloud.com/api/1.0/index.html#section/Authentication
API v2 Reference: https://docs.jumpcloud.com/api/2.0/index.html
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


class JumpCloudAuthType(str, Enum):
    """Authentication types supported by the JumpCloud connector."""

    API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class JumpCloudResponse(BaseModel):
    """Standardized JumpCloud API response wrapper.

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


class JumpCloudRESTClientViaApiKey(HTTPClient):
    """JumpCloud REST client via API Key.

    Uses the ``x-api-key`` header for authentication instead of the
    standard ``Authorization`` header.

    Args:
        api_key: The JumpCloud API key
    """

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, token_type="Bearer")
        self.base_url = "https://console.jumpcloud.com/api/v2"
        # Replace Authorization with x-api-key
        _ = self.headers.pop("Authorization", None)
        self.headers["x-api-key"] = api_key
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class JumpCloudApiKeyConfig(BaseModel):
    """Configuration for JumpCloud client via API Key.

    Args:
        api_key: The JumpCloud API key
    """

    api_key: str

    def create_client(self) -> JumpCloudRESTClientViaApiKey:
        return JumpCloudRESTClientViaApiKey(self.api_key)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class JumpCloudAuthConfig(BaseModel):
    """Auth section of the JumpCloud connector configuration from etcd."""

    authType: JumpCloudAuthType = JumpCloudAuthType.API_KEY
    apiKey: str | None = None
    api_key: str | None = None

    class Config:
        extra = "allow"


class JumpCloudCredentialsConfig(BaseModel):
    """Credentials section of the JumpCloud connector configuration."""

    api_key: str | None = None

    class Config:
        extra = "allow"


class JumpCloudConnectorConfig(BaseModel):
    """Top-level JumpCloud connector configuration from etcd."""

    auth: JumpCloudAuthConfig = Field(default_factory=JumpCloudAuthConfig)
    credentials: JumpCloudCredentialsConfig = Field(
        default_factory=JumpCloudCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class JumpCloudClient(IClient):
    """Builder class for JumpCloud clients.

    Supports:
    - API Key authentication (x-api-key header)
    """

    def __init__(
        self,
        client: JumpCloudRESTClientViaApiKey,
    ) -> None:
        """Initialize with a JumpCloud client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> JumpCloudRESTClientViaApiKey:
        """Return the JumpCloud client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: JumpCloudApiKeyConfig,
    ) -> "JumpCloudClient":
        """Build JumpCloudClient with configuration.

        Args:
            config: JumpCloudApiKeyConfig instance

        Returns:
            JumpCloudClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "JumpCloudClient":
        """Build JumpCloudClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            JumpCloudClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get JumpCloud connector configuration"
                )

            connector_config = JumpCloudConnectorConfig.model_validate(
                raw_config
            )

            api_key = (
                connector_config.auth.apiKey
                or connector_config.auth.api_key
                or connector_config.credentials.api_key
                or ""
            )
            if not api_key:
                raise ValueError(
                    "API key required for JumpCloud auth"
                )

            key_config = JumpCloudApiKeyConfig(api_key=api_key)
            return cls(key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build JumpCloud client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "JumpCloudClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            JumpCloudClient instance
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
                or auth_config.get("api_key", "")
            )
            if not api_key:
                raise ValueError(
                    "API key not found in toolset config"
                )

            key_config = JumpCloudApiKeyConfig(api_key=api_key)
            return cls(key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build JumpCloud client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for JumpCloud."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get JumpCloud connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get JumpCloud connector config: {e}"
            )
            raise ValueError(
                f"Failed to get JumpCloud connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
