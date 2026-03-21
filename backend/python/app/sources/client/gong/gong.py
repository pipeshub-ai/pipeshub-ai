"""Gong client implementation.

This module provides clients for interacting with the Gong API using either:
1. OAuth2 authorization code flow
2. Basic Auth (access_key:access_key_secret)

Authentication Reference: https://gong.app.gong.io/settings/api/documentation
OAuth Reference: https://gong.app.gong.io/settings/api/documentation#overview
API Reference: https://gong.app.gong.io/settings/api/documentation#tag/Calls
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GongAuthType(str, Enum):
    """Authentication types supported by the Gong connector."""

    OAUTH = "OAUTH"
    BASIC_AUTH = "BASIC_AUTH"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class GongResponse(BaseModel):
    """Standardized Gong API response wrapper.

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


class GongRESTClientViaOAuth(HTTPClient):
    """Gong REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (default: https://api.gong.io/v2)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://api.gong.io/v2",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class GongRESTClientViaBasicAuth(HTTPClient):
    """Gong REST client via Basic Auth (access_key:access_key_secret).

    Credentials are base64-encoded and passed in the Authorization header.

    Args:
        access_key: The Gong API access key
        access_key_secret: The Gong API access key secret
        base_url: API base URL (default: https://api.gong.io/v2)
    """

    def __init__(
        self,
        access_key: str,
        access_key_secret: str,
        base_url: str = "https://api.gong.io/v2",
    ) -> None:
        super().__init__("", token_type="Basic")
        self.base_url = base_url
        self.access_key = access_key
        self.access_key_secret = access_key_secret
        credentials = base64.b64encode(
            f"{access_key}:{access_key_secret}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class GongOAuthConfig(BaseModel):
    """Configuration for Gong client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.gong.io/v2)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = "https://api.gong.io/v2"

    def create_client(self) -> GongRESTClientViaOAuth:
        return GongRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class GongBasicAuthConfig(BaseModel):
    """Configuration for Gong client via Basic Auth.

    Args:
        access_key: The Gong API access key
        access_key_secret: The Gong API access key secret
        base_url: API base URL (default: https://api.gong.io/v2)
    """

    access_key: str
    access_key_secret: str
    base_url: str = "https://api.gong.io/v2"

    def create_client(self) -> GongRESTClientViaBasicAuth:
        return GongRESTClientViaBasicAuth(
            self.access_key,
            self.access_key_secret,
            self.base_url,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class GongAuthConfig(BaseModel):
    """Auth section of the Gong connector configuration from etcd."""

    authType: GongAuthType = GongAuthType.BASIC_AUTH
    accessKey: str | None = None
    accessKeySecret: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class GongCredentialsConfig(BaseModel):
    """Credentials section of the Gong connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None
    access_key: str | None = None
    access_key_secret: str | None = None

    class Config:
        extra = "allow"


class GongConnectorConfig(BaseModel):
    """Top-level Gong connector configuration from etcd."""

    auth: GongAuthConfig = Field(default_factory=GongAuthConfig)
    credentials: GongCredentialsConfig = Field(
        default_factory=GongCredentialsConfig
    )
    base_url: str = "https://api.gong.io/v2"

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class GongClient(IClient):
    """Builder class for Gong clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Basic Auth (access_key:access_key_secret)
    """

    def __init__(
        self,
        client: GongRESTClientViaOAuth | GongRESTClientViaBasicAuth,
    ) -> None:
        """Initialize with a Gong client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> GongRESTClientViaOAuth | GongRESTClientViaBasicAuth:
        """Return the Gong client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: GongOAuthConfig | GongBasicAuthConfig,
    ) -> "GongClient":
        """Build GongClient with configuration.

        Args:
            config: GongOAuthConfig or GongBasicAuthConfig instance

        Returns:
            GongClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GongClient":
        """Build GongClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: For OAuth 2.0 access tokens
        2. BASIC_AUTH: For access_key:access_key_secret authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            GongClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Gong connector configuration"
                )

            connector_config = GongConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == GongAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(
                            "/services/oauth/gong", default=[]
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
                                    shared.get("clientId", shared.get("client_id", client_id))
                                )
                                client_secret = str(
                                    shared.get("clientSecret", shared.get("client_secret", client_secret))
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

                oauth_cfg = GongOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    base_url=connector_config.base_url,
                )
                return cls(oauth_cfg.create_client())

            else:
                # Default: BASIC_AUTH
                access_key = (
                    connector_config.credentials.access_key
                    or connector_config.auth.accessKey
                    or ""
                )
                access_key_secret = (
                    connector_config.credentials.access_key_secret
                    or connector_config.auth.accessKeySecret
                    or ""
                )

                if not access_key or not access_key_secret:
                    raise ValueError(
                        "Access key and secret required for "
                        "BASIC_AUTH auth type"
                    )

                basic_cfg = GongBasicAuthConfig(
                    access_key=access_key,
                    access_key_secret=access_key_secret,
                    base_url=connector_config.base_url,
                )
                return cls(basic_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Gong client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "GongClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            GongClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            base_url: str = str(
                toolset_config.get("base_url", "https://api.gong.io/v2")
            )
            auth_type = auth_config.get("authType", "BASIC_AUTH")

            if auth_type == "OAUTH":
                access_token = str(credentials.get("access_token", ""))
                if not access_token:
                    raise ValueError(
                        "Access token not found in toolset config"
                    )

                client_id = str(auth_config.get("clientId", ""))
                client_secret = str(auth_config.get("clientSecret", ""))

                # Try shared OAuth config
                oauth_config_id: str | None = cast(
                    str | None, auth_config.get("oauthConfigId")
                )
                if oauth_config_id and config_service and not (
                    client_id and client_secret
                ):
                    try:
                        oauth_configs_raw = await config_service.get_config(
                            "/services/oauth/gong", default=[]
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
                                    shared.get("clientId", shared.get("client_id", client_id))
                                )
                                client_secret = str(
                                    shared.get("clientSecret", shared.get("client_secret", client_secret))
                                )
                                break
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch shared OAuth config: {e}"
                        )

                oauth_cfg = GongOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    base_url=base_url,
                )
                return cls(oauth_cfg.create_client())

            else:
                # Default: BASIC_AUTH
                access_key = str(
                    credentials.get("access_key", "")
                    or auth_config.get("accessKey", "")
                )
                access_key_secret = str(
                    credentials.get("access_key_secret", "")
                    or auth_config.get("accessKeySecret", "")
                )
                if not access_key or not access_key_secret:
                    raise ValueError(
                        "Access key and secret not found in toolset config"
                    )
                basic_cfg = GongBasicAuthConfig(
                    access_key=access_key,
                    access_key_secret=access_key_secret,
                    base_url=base_url,
                )
                return cls(basic_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Gong client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Gong."""
        try:
            raw = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Gong connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Gong connector config: {e}")
            raise ValueError(
                f"Failed to get Gong connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
