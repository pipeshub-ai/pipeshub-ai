"""Haystack (HaystackApp) client implementation.

This module provides a client for interacting with the Haystack API using
API Key (Bearer token) authentication.

API Reference: https://developer.haystackapp.io/
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


class HaystackResponse(BaseModel):
    """Standardized Haystack API response wrapper.

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


class HaystackRESTClientViaToken(HTTPClient):
    """Haystack REST client via API Key (Bearer token).

    Args:
        token: API key used as Bearer token
    """

    def __init__(self, token: str) -> None:
        super().__init__(token, "Bearer")
        self.base_url = "https://api.haystackapp.io/v1"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class HaystackTokenConfig(BaseModel):
    """Configuration for Haystack client via API Key.

    Args:
        token: API key (Bearer token)
    """

    token: str

    def create_client(self) -> HaystackRESTClientViaToken:
        return HaystackRESTClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class HaystackAuthConfig(BaseModel):
    """Auth section of the Haystack connector configuration from etcd."""

    apiToken: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class HaystackCredentialsConfig(BaseModel):
    """Credentials section of the Haystack connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class HaystackConnectorConfig(BaseModel):
    """Top-level Haystack connector configuration from etcd."""

    auth: HaystackAuthConfig = Field(default_factory=HaystackAuthConfig)
    credentials: HaystackCredentialsConfig = Field(
        default_factory=HaystackCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class HaystackClient(IClient):
    """Builder class for Haystack clients.

    Supports:
    - API Key (Bearer token) authentication
    """

    def __init__(
        self,
        client: HaystackRESTClientViaToken,
    ) -> None:
        """Initialize with a Haystack client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> HaystackRESTClientViaToken:
        """Return the Haystack client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: HaystackTokenConfig,
    ) -> "HaystackClient":
        """Build HaystackClient with configuration.

        Args:
            config: HaystackTokenConfig instance

        Returns:
            HaystackClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "HaystackClient":
        """Build HaystackClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            HaystackClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Haystack connector configuration"
                )

            connector_config = HaystackConnectorConfig.model_validate(
                raw_config
            )

            token = (
                connector_config.auth.apiToken
                or connector_config.auth.token
                or connector_config.credentials.access_token
                or ""
            )
            if not token:
                raise ValueError(
                    "API token required for Haystack authentication"
                )

            token_config = HaystackTokenConfig(token=token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Haystack client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "HaystackClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            HaystackClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            access_token: str = str(
                credentials.get("access_token", "")
                or auth_config.get("apiToken", "")
                or auth_config.get("token", "")
            )
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = HaystackTokenConfig(token=access_token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Haystack client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Haystack."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Haystack connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Haystack connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Haystack connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
