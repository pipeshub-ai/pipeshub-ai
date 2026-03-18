"""Mattermost client implementation.

This module provides clients for interacting with the Mattermost API using either:
1. Personal Access Token authentication (Bearer token)
2. Login-based session token authentication (username + password)

The base URL is constructed from the server domain: https://{server}/api/v4

Authentication Reference: https://api.mattermost.com/#tag/authentication
API Reference: https://api.mattermost.com/
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


class MattermostAuthType(str, Enum):
    """Authentication types supported by the Mattermost connector."""

    TOKEN = "TOKEN"
    LOGIN = "LOGIN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class MattermostResponse(BaseModel):
    """Standardized Mattermost API response wrapper.

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


class MattermostRESTClientViaToken(HTTPClient):
    """Mattermost REST client via Personal Access Token (Bearer).

    Personal access tokens are passed as Bearer tokens in the Authorization header.

    Args:
        token: The personal access token
        server: Mattermost server domain (e.g. "mattermost.example.com")
    """

    def __init__(self, token: str, server: str) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = f"https://{server}/api/v4"
        self.server = server
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_server(self) -> str:
        """Get the server domain."""
        return self.server


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class MattermostTokenConfig(BaseModel):
    """Configuration for Mattermost client via Personal Access Token.

    Args:
        token: The personal access token
        server: Mattermost server domain (e.g. "mattermost.example.com")
    """

    token: str
    server: str

    def create_client(self) -> MattermostRESTClientViaToken:
        return MattermostRESTClientViaToken(self.token, self.server)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class MattermostAuthConfig(BaseModel):
    """Auth section of the Mattermost connector configuration from etcd."""

    authType: MattermostAuthType = MattermostAuthType.TOKEN
    server: str | None = None
    apiToken: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class MattermostCredentialsConfig(BaseModel):
    """Credentials section of the Mattermost connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class MattermostConnectorConfig(BaseModel):
    """Top-level Mattermost connector configuration from etcd."""

    auth: MattermostAuthConfig = Field(default_factory=MattermostAuthConfig)
    credentials: MattermostCredentialsConfig = Field(
        default_factory=MattermostCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class MattermostClient(IClient):
    """Builder class for Mattermost clients with different authentication methods.

    Supports:
    - Personal Access Token (Bearer) authentication
    """

    def __init__(
        self,
        client: MattermostRESTClientViaToken,
    ) -> None:
        """Initialize with a Mattermost client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> MattermostRESTClientViaToken:
        """Return the Mattermost client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: MattermostTokenConfig,
    ) -> "MattermostClient":
        """Build MattermostClient with configuration.

        Args:
            config: MattermostTokenConfig instance

        Returns:
            MattermostClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "MattermostClient":
        """Build MattermostClient using configuration service.

        Supports Personal Access Token authentication.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            MattermostClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Mattermost connector configuration"
                )

            connector_config = MattermostConnectorConfig.model_validate(
                raw_config
            )

            server = connector_config.auth.server or ""
            if not server:
                raise ValueError("Server domain is required")

            token = (
                connector_config.auth.apiToken
                or connector_config.auth.token
                or connector_config.credentials.access_token
                or ""
            )
            if not token:
                raise ValueError(
                    "Personal access token required for TOKEN auth type"
                )

            token_config = MattermostTokenConfig(token=token, server=server)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Mattermost client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "MattermostClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            MattermostClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            server: str = str(auth_config.get("server", ""))
            if not server:
                raise ValueError("Server domain not found in toolset config")

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = MattermostTokenConfig(
                token=access_token, server=server
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Mattermost client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Mattermost."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Mattermost connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Mattermost connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Mattermost connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
