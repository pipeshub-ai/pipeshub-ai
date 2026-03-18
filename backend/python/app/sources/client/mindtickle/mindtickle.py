"""Mindtickle client implementation.

This module provides a client for interacting with the Mindtickle API using
API Key (Bearer token) authentication.

Base URL: https://api.mindtickle.com/v2

Authentication Reference: https://developer.mindtickle.com/docs/authentication
API Reference: https://developer.mindtickle.com/docs/api
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


class MindtickleResponse(BaseModel):
    """Standardized Mindtickle API response wrapper.

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


class MindtickleRESTClientViaToken(HTTPClient):
    """Mindtickle REST client via API Key (Bearer token).

    Simple authentication using an API key passed as a Bearer token
    in the Authorization header.

    Args:
        token: The API key / Bearer token
        base_url: API base URL (default: https://api.mindtickle.com/v2)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.mindtickle.com/v2",
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = base_url
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class MindtickleTokenConfig(BaseModel):
    """Configuration for Mindtickle client via API Key (Bearer token).

    Args:
        token: The API key / Bearer token
        base_url: API base URL (default: https://api.mindtickle.com/v2)
    """

    token: str
    base_url: str = "https://api.mindtickle.com/v2"

    def create_client(self) -> MindtickleRESTClientViaToken:
        return MindtickleRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class MindtickleAuthConfig(BaseModel):
    """Auth section of the Mindtickle connector configuration from etcd."""

    apiKey: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class MindtickleCredentialsConfig(BaseModel):
    """Credentials section of the Mindtickle connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class MindtickleConnectorConfig(BaseModel):
    """Top-level Mindtickle connector configuration from etcd."""

    auth: MindtickleAuthConfig = Field(default_factory=MindtickleAuthConfig)
    credentials: MindtickleCredentialsConfig = Field(
        default_factory=MindtickleCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class MindtickleClient(IClient):
    """Builder class for Mindtickle clients.

    Supports:
    - API Key (Bearer token) authentication
    """

    def __init__(
        self,
        client: MindtickleRESTClientViaToken,
    ) -> None:
        """Initialize with a Mindtickle client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> MindtickleRESTClientViaToken:
        """Return the Mindtickle client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: MindtickleTokenConfig,
    ) -> "MindtickleClient":
        """Build MindtickleClient with configuration.

        Args:
            config: MindtickleTokenConfig instance

        Returns:
            MindtickleClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "MindtickleClient":
        """Build MindtickleClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            MindtickleClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Mindtickle connector configuration"
                )

            connector_config = MindtickleConnectorConfig.model_validate(
                raw_config
            )

            token = (
                connector_config.auth.apiKey
                or connector_config.auth.token
                or connector_config.credentials.access_token
                or ""
            )
            if not token:
                raise ValueError(
                    "API key or token required for Mindtickle"
                )

            token_config = MindtickleTokenConfig(token=token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Mindtickle client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "MindtickleClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused)

        Returns:
            MindtickleClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            access_token: str = str(
                credentials.get("access_token", "")
                or auth_config.get("apiKey", "")
                or auth_config.get("token", "")
            )
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = MindtickleTokenConfig(token=access_token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Mindtickle client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Mindtickle."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Mindtickle connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Mindtickle connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Mindtickle connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
