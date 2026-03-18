"""Aha! client implementation.

This module provides clients for interacting with the Aha! API using either:
1. OAuth 2.0 access token authentication
2. API Key (Bearer token) authentication

Aha! API uses a subdomain-based base URL pattern:
https://{subdomain}.aha.io/api/v1

API Reference: https://www.aha.io/api
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AhaAuthType(str, Enum):
    """Authentication types supported by the Aha! connector."""

    OAUTH = "OAUTH"
    API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class AhaResponse(BaseModel):
    """Standardized Aha! API response wrapper.

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


class AhaRESTClientViaToken(HTTPClient):
    """Aha! REST client via API Key (Bearer token).

    Uses an API key passed as a Bearer token.

    Args:
        subdomain: The Aha! account subdomain
        api_key: The API key for authentication
    """

    def __init__(self, subdomain: str, api_key: str) -> None:
        super().__init__(api_key, token_type="Bearer")
        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.aha.io/api/v1"
        self.api_key = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_subdomain(self) -> str:
        """Get the Aha! subdomain."""
        return self.subdomain


class AhaRESTClientViaOAuth(HTTPClient):
    """Aha! REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        subdomain: The Aha! account subdomain
        access_token: The OAuth access token
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
    """

    def __init__(
        self,
        subdomain: str,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.aha.io/api/v1"
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_subdomain(self) -> str:
        """Get the Aha! subdomain."""
        return self.subdomain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class AhaTokenConfig(BaseModel):
    """Configuration for Aha! client via API Key.

    Args:
        subdomain: The Aha! account subdomain
        api_key: The API key for authentication
    """

    subdomain: str
    api_key: str

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        """Validate subdomain field."""
        if not v or not v.strip():
            raise ValueError("subdomain cannot be empty or None")
        if v.startswith(("http://", "https://")):
            raise ValueError(
                "subdomain should not include protocol (http:// or https://)"
            )
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate api_key field."""
        if not v or not v.strip():
            raise ValueError("api_key cannot be empty or None")
        return v

    def create_client(self) -> AhaRESTClientViaToken:
        return AhaRESTClientViaToken(self.subdomain, self.api_key)


class AhaOAuthConfig(BaseModel):
    """Configuration for Aha! client via OAuth 2.0.

    Args:
        subdomain: The Aha! account subdomain
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    subdomain: str
    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        """Validate subdomain field."""
        if not v or not v.strip():
            raise ValueError("subdomain cannot be empty or None")
        if v.startswith(("http://", "https://")):
            raise ValueError(
                "subdomain should not include protocol (http:// or https://)"
            )
        return v

    def create_client(self) -> AhaRESTClientViaOAuth:
        return AhaRESTClientViaOAuth(
            self.subdomain,
            self.access_token,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class AhaAuthConfigModel(BaseModel):
    """Auth section of the Aha! connector configuration from etcd."""

    authType: AhaAuthType = AhaAuthType.API_KEY
    apiKey: str | None = None
    apiToken: str | None = None
    subdomain: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class AhaCredentialsConfig(BaseModel):
    """Credentials section of the Aha! connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class AhaConnectorConfig(BaseModel):
    """Top-level Aha! connector configuration from etcd."""

    auth: AhaAuthConfigModel = Field(default_factory=AhaAuthConfigModel)
    credentials: AhaCredentialsConfig = Field(
        default_factory=AhaCredentialsConfig
    )
    subdomain: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Shared OAuth configuration model
# ---------------------------------------------------------------------------


class AhaSharedOAuthConfig(BaseModel):
    """Shared OAuth configuration for Aha! (from etcd /services/oauth/aha)."""

    _id: str | None = None
    clientId: str | None = None
    client_id: str | None = None
    clientSecret: str | None = None
    client_secret: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class AhaClient(IClient):
    """Builder class for Aha! clients with different authentication methods.

    Supports:
    - API Key (Bearer token) authentication
    - OAuth 2.0 access token authentication
    - Subdomain-based base URL
    """

    def __init__(
        self,
        client: AhaRESTClientViaToken | AhaRESTClientViaOAuth,
    ) -> None:
        """Initialize with an Aha! client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> AhaRESTClientViaToken | AhaRESTClientViaOAuth:
        """Return the Aha! client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    def get_subdomain(self) -> str:
        """Return the Aha! subdomain."""
        return self.client.get_subdomain()

    @classmethod
    def build_with_config(
        cls,
        config: AhaTokenConfig | AhaOAuthConfig,
    ) -> "AhaClient":
        """Build AhaClient with configuration.

        Args:
            config: AhaTokenConfig or AhaOAuthConfig instance

        Returns:
            AhaClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "AhaClient":
        """Build AhaClient using configuration service.

        Supports two authentication strategies:
        1. API_KEY: For API key authentication
        2. OAUTH: For OAuth 2.0 access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            AhaClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Aha! connector configuration")

            connector_config = AhaConnectorConfig.model_validate(raw_config)

            subdomain = (
                connector_config.auth.subdomain
                or connector_config.subdomain
                or ""
            )
            if not subdomain:
                raise ValueError("Subdomain required for Aha! API")

            if connector_config.auth.authType == AhaAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/aha", default=[]
                        )
                        oauth_configs: list[Any] = (
                            cast(list[Any], oauth_configs_raw)
                            if isinstance(oauth_configs_raw, list)
                            else []
                        )
                        for cfg in oauth_configs:
                            c: dict[str, Any] = cast(dict[str, Any], cfg)
                            if c.get("_id") == oauth_config_id:
                                shared: dict[str, Any] = cast(
                                    dict[str, Any], c.get("config", {})
                                )
                                client_id = str(
                                    shared.get("clientId")
                                    or shared.get("client_id")
                                    or client_id
                                )
                                client_secret = str(
                                    shared.get("clientSecret")
                                    or shared.get("client_secret")
                                    or client_secret
                                )
                                break
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch shared OAuth config: {e}"
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_cfg = AhaOAuthConfig(
                    subdomain=subdomain,
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == AhaAuthType.API_KEY:
                api_key = (
                    connector_config.auth.apiKey
                    or connector_config.auth.apiToken
                    or ""
                )
                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )

                token_config = AhaTokenConfig(
                    subdomain=subdomain, api_key=api_key
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Aha! client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "AhaClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            AhaClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            subdomain: str = str(
                auth_config.get("subdomain")
                or toolset_config.get("subdomain", "")
            )
            if not subdomain:
                raise ValueError("Subdomain not found in toolset config")

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError("Access token not found in toolset config")

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/aha", default=[]
                    )
                    oauth_configs: list[Any] = (
                        cast(list[Any], oauth_configs_raw)
                        if isinstance(oauth_configs_raw, list)
                        else []
                    )
                    for cfg in oauth_configs:
                        c: dict[str, Any] = cast(dict[str, Any], cfg)
                        if c.get("_id") == oauth_config_id:
                            shared: dict[str, Any] = cast(
                                dict[str, Any], c.get("config", {})
                            )
                            client_id = str(
                                shared.get("clientId")
                                or shared.get("client_id")
                                or client_id
                            )
                            client_secret = str(
                                shared.get("clientSecret")
                                or shared.get("client_secret")
                                or client_secret
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            oauth_cfg = AhaOAuthConfig(
                subdomain=subdomain,
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Aha! client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Aha!."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Aha! connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Aha! connector config: {e}")
            raise ValueError(
                f"Failed to get Aha! connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
