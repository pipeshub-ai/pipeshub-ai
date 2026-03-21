"""ClickUp client implementation.

This module provides clients for interacting with the ClickUp API using either:
1. Personal API Token authentication (pk_* tokens)
2. OAuth 2.0 access token authentication

ClickUp API supports both v2 and v3 versions. The version is configurable
via the client, and the base URL is constructed accordingly.

Authentication Reference: https://developer.clickup.com/docs/authentication
API v2 Reference: https://clickup.com/api/developer-portal/clickup20api/
API v3 Reference: https://clickup.com/api/developer-portal/clickupapi/
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


class ClickUpAuthType(str, Enum):
    """Authentication types supported by the ClickUp connector."""

    OAUTH = "OAUTH"
    PERSONAL_TOKEN = "PERSONAL_TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ClickUpResponse(BaseModel):
    """Standardized ClickUp API response wrapper.

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


class ClickUpRESTClientViaPersonalToken(HTTPClient):
    """ClickUp REST client via Personal API Token.

    Personal tokens begin with pk_ and are passed directly in the
    Authorization header without a Bearer prefix.

    Args:
        token: The personal API token (pk_*)
        version: API version to use ("v2" or "v3", default: "v2")
    """

    def __init__(self, token: str, version: str = "v2") -> None:
        # Initialize with empty token_type; we override the header below
        super().__init__(token, token_type="Bearer")
        self.base_url = f"https://api.clickup.com/api/{version}"
        self.version = version
        # ClickUp personal tokens: Authorization: {personal_token}
        self.headers["Authorization"] = token
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including API version."""
        return self.base_url

    def get_version(self) -> str:
        """Get the API version."""
        return self.version


class ClickUpRESTClientViaOAuth(HTTPClient):
    """ClickUp REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        version: API version to use ("v2" or "v3", default: "v2")
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
    """

    def __init__(
        self,
        access_token: str,
        version: str = "v2",
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = f"https://api.clickup.com/api/{version}"
        self.version = version
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including API version."""
        return self.base_url

    def get_version(self) -> str:
        """Get the API version."""
        return self.version


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class ClickUpPersonalTokenConfig(BaseModel):
    """Configuration for ClickUp client via Personal API Token.

    Args:
        token: The personal API token (pk_*)
        version: API version ("v2" or "v3", default: "v2")
    """

    token: str
    version: str = "v2"

    def create_client(self) -> ClickUpRESTClientViaPersonalToken:
        return ClickUpRESTClientViaPersonalToken(self.token, self.version)


class ClickUpOAuthConfig(BaseModel):
    """Configuration for ClickUp client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        version: API version ("v2" or "v3", default: "v2")
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    version: str = "v2"
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> ClickUpRESTClientViaOAuth:
        return ClickUpRESTClientViaOAuth(
            self.access_token,
            self.version,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class ClickUpAuthConfig(BaseModel):
    """Auth section of the ClickUp connector configuration from etcd."""

    authType: ClickUpAuthType = ClickUpAuthType.PERSONAL_TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class ClickUpCredentialsConfig(BaseModel):
    """Credentials section of the ClickUp connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class ClickUpConnectorConfig(BaseModel):
    """Top-level ClickUp connector configuration from etcd."""

    auth: ClickUpAuthConfig = Field(default_factory=ClickUpAuthConfig)
    credentials: ClickUpCredentialsConfig = Field(
        default_factory=ClickUpCredentialsConfig
    )
    version: str = "v2"

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class ClickUpClient(IClient):
    """Builder class for ClickUp clients with different authentication methods.

    Supports:
    - Personal API Token (pk_*) authentication
    - OAuth 2.0 access token authentication
    - API version selection (v2 or v3)
    """

    def __init__(
        self,
        client: ClickUpRESTClientViaPersonalToken | ClickUpRESTClientViaOAuth,
    ) -> None:
        """Initialize with a ClickUp client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> ClickUpRESTClientViaPersonalToken | ClickUpRESTClientViaOAuth:
        """Return the ClickUp client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @property
    def version(self) -> str:
        """Return the API version."""
        return self.client.get_version()

    @classmethod
    def build_with_config(
        cls,
        config: ClickUpPersonalTokenConfig | ClickUpOAuthConfig,
    ) -> "ClickUpClient":
        """Build ClickUpClient with configuration.

        Args:
            config: ClickUpPersonalTokenConfig or ClickUpOAuthConfig instance

        Returns:
            ClickUpClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ClickUpClient":
        """Build ClickUpClient using configuration service.

        Supports two authentication strategies:
        1. PERSONAL_TOKEN: For personal API tokens (pk_*)
        2. OAUTH: For OAuth 2.0 access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ClickUpClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get ClickUp connector configuration")

            connector_config = ClickUpConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == ClickUpAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/clickup", default=[]
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

                oauth_cfg = ClickUpOAuthConfig(
                    access_token=access_token,
                    version=connector_config.version,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == ClickUpAuthType.PERSONAL_TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Personal token required for PERSONAL_TOKEN auth type"
                    )

                token_config = ClickUpPersonalTokenConfig(
                    token=token, version=connector_config.version
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build ClickUp client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "ClickUpClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            ClickUpClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            version: str = str(toolset_config.get("version", "v2"))

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
                        "/services/oauth/clickup", default=[]
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

            oauth_cfg = ClickUpOAuthConfig(
                access_token=access_token,
                version=version,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build ClickUp client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for ClickUp."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get ClickUp connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get ClickUp connector config: {e}")
            raise ValueError(
                f"Failed to get ClickUp connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
