"""Phabricator client implementation.

This module provides a client for interacting with the Phabricator Conduit API
using API Token authentication. All Phabricator API calls are POST requests
with form-encoded body including the ``api.token`` parameter.

Authentication Reference: https://secure.phabricator.com/book/phabricator/article/conduit/
API Reference: https://secure.phabricator.com/conduit/
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


class PhabricatorAuthType(str, Enum):
    """Authentication types supported by the Phabricator connector."""

    API_TOKEN = "API_TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PhabricatorResponse(BaseModel):
    """Standardized Phabricator API response wrapper.

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


class PhabricatorRESTClientViaToken(HTTPClient):
    """Phabricator REST client via Conduit API Token.

    All Phabricator Conduit API calls are POST requests with form-encoded
    body. The ``api.token`` is injected into every request body automatically
    by the DataSource layer; this client simply sets appropriate defaults.

    Args:
        token: The Conduit API token
        instance: Phabricator instance hostname (e.g. ``phabricator.example.com``)
    """

    def __init__(self, token: str, instance: str) -> None:
        # We don't use Bearer auth; token goes in POST body
        super().__init__(token, token_type="Bearer")
        self.base_url = f"https://{instance}/api"
        self.api_token = token
        # Remove the Authorization header; Phabricator uses api.token in body
        _ = self.headers.pop("Authorization", None)
        self.headers["Content-Type"] = "application/x-www-form-urlencoded"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_api_token(self) -> str:
        """Get the API token for inclusion in POST body."""
        return self.api_token


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class PhabricatorTokenConfig(BaseModel):
    """Configuration for Phabricator client via API Token.

    Args:
        token: The Conduit API token
        instance: Phabricator instance hostname
    """

    token: str
    instance: str

    def create_client(self) -> PhabricatorRESTClientViaToken:
        return PhabricatorRESTClientViaToken(self.token, self.instance)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class PhabricatorAuthConfig(BaseModel):
    """Auth section of the Phabricator connector configuration from etcd."""

    authType: PhabricatorAuthType = PhabricatorAuthType.API_TOKEN
    apiToken: str | None = None
    token: str | None = None
    instance: str | None = None

    class Config:
        extra = "allow"


class PhabricatorCredentialsConfig(BaseModel):
    """Credentials section of the Phabricator connector configuration."""

    api_token: str | None = None

    class Config:
        extra = "allow"


class PhabricatorConnectorConfig(BaseModel):
    """Top-level Phabricator connector configuration from etcd."""

    auth: PhabricatorAuthConfig = Field(default_factory=PhabricatorAuthConfig)
    credentials: PhabricatorCredentialsConfig = Field(
        default_factory=PhabricatorCredentialsConfig
    )
    instance: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class PhabricatorClient(IClient):
    """Builder class for Phabricator clients.

    Supports:
    - API Token (Conduit) authentication
    """

    def __init__(
        self,
        client: PhabricatorRESTClientViaToken,
    ) -> None:
        """Initialize with a Phabricator client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> PhabricatorRESTClientViaToken:
        """Return the Phabricator client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: PhabricatorTokenConfig,
    ) -> "PhabricatorClient":
        """Build PhabricatorClient with configuration.

        Args:
            config: PhabricatorTokenConfig instance

        Returns:
            PhabricatorClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PhabricatorClient":
        """Build PhabricatorClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            PhabricatorClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Phabricator connector configuration"
                )

            connector_config = PhabricatorConnectorConfig.model_validate(
                raw_config
            )

            token = (
                connector_config.auth.apiToken
                or connector_config.auth.token
                or connector_config.credentials.api_token
                or ""
            )
            if not token:
                raise ValueError(
                    "API token required for Phabricator auth"
                )

            instance = (
                connector_config.auth.instance
                or connector_config.instance
                or ""
            )
            if not instance:
                raise ValueError(
                    "Instance hostname required for Phabricator"
                )

            token_config = PhabricatorTokenConfig(
                token=token, instance=instance
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Phabricator client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "PhabricatorClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            PhabricatorClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            token: str = str(
                credentials.get("api_token", "")
                or auth_config.get("apiToken", "")
                or auth_config.get("token", "")
            )
            if not token:
                raise ValueError(
                    "API token not found in toolset config"
                )

            instance: str = str(
                auth_config.get("instance", "")
                or toolset_config.get("instance", "")
            )
            if not instance:
                raise ValueError(
                    "Instance hostname not found in toolset config"
                )

            token_config = PhabricatorTokenConfig(
                token=token, instance=instance
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Phabricator client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Phabricator."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Phabricator connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Phabricator connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Phabricator connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
