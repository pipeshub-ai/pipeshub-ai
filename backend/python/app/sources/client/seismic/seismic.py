"""Seismic client implementation.

This module provides clients for interacting with the Seismic API using either:
1. OAuth 2.0 (authorization code flow)
2. Bearer Token authentication

OAuth Auth Endpoint: https://auth.seismic.com/tenants/{tenant_id}/connect/authorize
OAuth Token Endpoint: https://auth.seismic.com/tenants/{tenant_id}/connect/token
Auth Method: body (credentials sent in POST body)
API Reference: https://api.seismic.com/v2
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


class SeismicAuthType(str, Enum):
    """Authentication types supported by the Seismic connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class SeismicResponse(BaseModel):
    """Standardized Seismic API response wrapper.

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


class SeismicRESTClientViaOAuth(HTTPClient):
    """Seismic REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Token exchange uses the "body" method (client credentials in POST body).

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        tenant_id: Seismic tenant ID (for OAuth endpoints)
        redirect_uri: OAuth redirect URI
        base_url: API base URL (default: https://api.seismic.com/v2)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
        redirect_uri: str | None = None,
        base_url: str = "https://api.seismic.com/v2",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.redirect_uri = redirect_uri
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class SeismicRESTClientViaToken(HTTPClient):
    """Seismic REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.seismic.com/v2)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.seismic.com/v2",
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


class SeismicOAuthConfig(BaseModel):
    """Configuration for Seismic client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        tenant_id: Seismic tenant ID (for OAuth endpoints)
        redirect_uri: OAuth redirect URI
        base_url: API base URL (default: https://api.seismic.com/v2)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    tenant_id: str | None = None
    redirect_uri: str | None = None
    base_url: str = "https://api.seismic.com/v2"

    def create_client(self) -> SeismicRESTClientViaOAuth:
        return SeismicRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.tenant_id,
            self.redirect_uri,
            self.base_url,
        )


class SeismicTokenConfig(BaseModel):
    """Configuration for Seismic client via Bearer token.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.seismic.com/v2)
    """

    token: str
    base_url: str = "https://api.seismic.com/v2"

    def create_client(self) -> SeismicRESTClientViaToken:
        return SeismicRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class SeismicAuthConfigModel(BaseModel):
    """Auth section of the Seismic connector configuration from etcd."""

    authType: SeismicAuthType = SeismicAuthType.TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    tenantId: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class SeismicCredentialsConfig(BaseModel):
    """Credentials section of the Seismic connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class SeismicConnectorConfig(BaseModel):
    """Top-level Seismic connector configuration from etcd."""

    auth: SeismicAuthConfigModel = Field(
        default_factory=SeismicAuthConfigModel
    )
    credentials: SeismicCredentialsConfig = Field(
        default_factory=SeismicCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class SeismicClient(IClient):
    """Builder class for Seismic clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: SeismicRESTClientViaOAuth | SeismicRESTClientViaToken,
    ) -> None:
        """Initialize with a Seismic client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> SeismicRESTClientViaOAuth | SeismicRESTClientViaToken:
        """Return the Seismic client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: SeismicOAuthConfig | SeismicTokenConfig,
    ) -> "SeismicClient":
        """Build SeismicClient with configuration.

        Args:
            config: SeismicOAuthConfig or SeismicTokenConfig instance

        Returns:
            SeismicClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "SeismicClient":
        """Build SeismicClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 access token
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            SeismicClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Seismic connector configuration"
                )

            connector_config = SeismicConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == SeismicAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""
                tenant_id = connector_config.auth.tenantId or ""
                redirect_uri = connector_config.auth.redirectUri or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/seismic", default=[]
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
                                tenant_id = str(
                                    shared.get("tenantId")
                                    or shared.get("tenant_id")
                                    or tenant_id
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

                oauth_cfg = SeismicOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    tenant_id=tenant_id,
                    redirect_uri=redirect_uri,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == SeismicAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = SeismicTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Seismic client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "SeismicClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            SeismicClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))
            tenant_id: str = str(auth_config.get("tenantId", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/seismic", default=[]
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
                            tenant_id = str(
                                shared.get("tenantId")
                                or shared.get("tenant_id")
                                or tenant_id
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            oauth_cfg = SeismicOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
                tenant_id=tenant_id,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Seismic client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Seismic."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Seismic connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Seismic connector config: {e}")
            raise ValueError(
                f"Failed to get Seismic connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
