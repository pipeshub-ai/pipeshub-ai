"""InVision client implementation.

This module provides a client for interacting with the InVision API using
API Key authentication (Bearer token).

InVision uses API keys passed as Bearer tokens in the Authorization header.

API Reference: https://developers.invisionapp.com/
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class InVisionResponse(BaseModel):
    """Standardized InVision API response wrapper.

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


class InVisionRESTClientViaToken(HTTPClient):
    """InVision REST client via API Key (Bearer token).

    InVision uses API keys passed as Bearer tokens.

    Args:
        api_key: The API key for authentication
    """

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key, token_type="Bearer")
        self.base_url = "https://api.invisionapp.com/v2"
        self.api_key = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration model (Pydantic)
# ---------------------------------------------------------------------------


class InVisionTokenConfig(BaseModel):
    """Configuration for InVision client via API Key.

    Args:
        api_key: The API key for authentication
    """

    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate api_key field."""
        if not v or not v.strip():
            raise ValueError("api_key cannot be empty or None")
        return v

    def create_client(self) -> InVisionRESTClientViaToken:
        return InVisionRESTClientViaToken(self.api_key)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class InVisionAuthConfig(BaseModel):
    """Auth section of the InVision connector configuration from etcd."""

    authType: str = "API_KEY"
    apiKey: str | None = None
    apiToken: str | None = None

    class Config:
        extra = "allow"


class InVisionConnectorConfig(BaseModel):
    """Top-level InVision connector configuration from etcd."""

    auth: InVisionAuthConfig = Field(default_factory=InVisionAuthConfig)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class InVisionClient(IClient):
    """Builder class for InVision clients.

    Supports API Key (Bearer token) authentication only.
    """

    def __init__(self, client: InVisionRESTClientViaToken) -> None:
        """Initialize with an InVision client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> InVisionRESTClientViaToken:
        """Return the InVision client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: InVisionTokenConfig,
    ) -> "InVisionClient":
        """Build InVisionClient with configuration.

        Args:
            config: InVisionTokenConfig instance

        Returns:
            InVisionClient instance
        """
        return cls(config.create_client())

    @classmethod
    def build_with_api_key(
        cls,
        api_key: str,
    ) -> "InVisionClient":
        """Build InVisionClient with API key directly.

        Args:
            api_key: The API key for authentication

        Returns:
            InVisionClient instance
        """
        config = InVisionTokenConfig(api_key=api_key)
        return cls.build_with_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "InVisionClient":
        """Build InVisionClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            InVisionClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get InVision connector configuration"
                )

            connector_config = InVisionConnectorConfig.model_validate(
                raw_config
            )

            api_key = (
                connector_config.auth.apiKey
                or connector_config.auth.apiToken
                or ""
            )
            if not api_key:
                raise ValueError(
                    "API key required for InVision authentication"
                )

            token_config = InVisionTokenConfig(api_key=api_key)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build InVision client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "InVisionClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused)

        Returns:
            InVisionClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(
                auth_config.get("apiKey")
                or auth_config.get("apiToken")
                or ""
            )
            if not api_key:
                raise ValueError(
                    "API key not found in toolset config"
                )

            token_config = InVisionTokenConfig(api_key=api_key)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build InVision client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for InVision."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get InVision connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get InVision connector config: {e}")
            raise ValueError(
                f"Failed to get InVision connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
