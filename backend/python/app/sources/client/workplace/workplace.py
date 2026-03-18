"""Facebook Workplace (Meta Workplace) client implementation.

This module provides a client for interacting with the Facebook Workplace API
using Access Token (Bearer) authentication generated from the Workplace admin
panel.

Base URL: https://graph.facebook.com/v18.0

Authentication: Access tokens are generated from the Workplace admin panel
and passed as Bearer tokens.

API Reference: https://developers.facebook.com/docs/workplace/reference
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


class WorkplaceResponse(BaseModel):
    """Standardized Workplace API response wrapper.

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


class WorkplaceRESTClientViaToken(HTTPClient):
    """Workplace REST client via Access Token (Bearer).

    Simple authentication using an access token generated from the
    Workplace admin panel, passed as a Bearer token in the
    Authorization header.

    Args:
        token: The access token from Workplace admin panel
        base_url: API base URL (default: https://graph.facebook.com/v18.0)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://graph.facebook.com/v18.0",
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


class WorkplaceTokenConfig(BaseModel):
    """Configuration for Workplace client via Access Token.

    Args:
        token: The access token from Workplace admin panel
        base_url: API base URL (default: https://graph.facebook.com/v18.0)
    """

    token: str
    base_url: str = "https://graph.facebook.com/v18.0"

    def create_client(self) -> WorkplaceRESTClientViaToken:
        return WorkplaceRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class WorkplaceAuthConfig(BaseModel):
    """Auth section of the Workplace connector configuration from etcd."""

    accessToken: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class WorkplaceCredentialsConfig(BaseModel):
    """Credentials section of the Workplace connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class WorkplaceConnectorConfig(BaseModel):
    """Top-level Workplace connector configuration from etcd."""

    auth: WorkplaceAuthConfig = Field(default_factory=WorkplaceAuthConfig)
    credentials: WorkplaceCredentialsConfig = Field(
        default_factory=WorkplaceCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class WorkplaceClient(IClient):
    """Builder class for Workplace clients.

    Supports:
    - Access Token (Bearer) authentication from Workplace admin panel
    """

    def __init__(
        self,
        client: WorkplaceRESTClientViaToken,
    ) -> None:
        """Initialize with a Workplace client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> WorkplaceRESTClientViaToken:
        """Return the Workplace client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: WorkplaceTokenConfig,
    ) -> "WorkplaceClient":
        """Build WorkplaceClient with configuration.

        Args:
            config: WorkplaceTokenConfig instance

        Returns:
            WorkplaceClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "WorkplaceClient":
        """Build WorkplaceClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            WorkplaceClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Workplace connector configuration"
                )

            connector_config = WorkplaceConnectorConfig.model_validate(
                raw_config
            )

            token = (
                connector_config.auth.accessToken
                or connector_config.auth.token
                or connector_config.credentials.access_token
                or ""
            )
            if not token:
                raise ValueError(
                    "Access token required for Workplace"
                )

            token_config = WorkplaceTokenConfig(token=token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Workplace client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "WorkplaceClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused)

        Returns:
            WorkplaceClient instance
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
                or auth_config.get("accessToken", "")
                or auth_config.get("token", "")
            )
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = WorkplaceTokenConfig(token=access_token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Workplace client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Workplace."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Workplace connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Workplace connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Workplace connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
