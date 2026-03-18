"""Guru client implementation.

This module provides clients for interacting with the Guru API using either:
1. Basic Auth (username:api_token)
2. OAuth 2.0 access token authentication

OAuth Auth Endpoint: https://api.getguru.com/oauth/authorize
OAuth Token Endpoint: https://api.getguru.com/oauth/token
API Reference: https://api.getguru.com/api/v1
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


class GuruAuthType(str, Enum):
    """Authentication types supported by the Guru connector."""

    BASIC = "BASIC"
    OAUTH = "OAUTH"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class GuruResponse(BaseModel):
    """Standardized Guru API response wrapper.

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


class GuruRESTClientViaBasicAuth(HTTPClient):
    """Guru REST client via Basic Auth (username:api_token).

    Args:
        username: Guru account email / username
        api_token: Guru API token
        base_url: API base URL (default: https://api.getguru.com/api/v1)
    """

    def __init__(
        self,
        username: str,
        api_token: str,
        base_url: str = "https://api.getguru.com/api/v1",
    ) -> None:
        # Initialize with empty token; override the header below
        super().__init__("", token_type="Basic")
        self.base_url = base_url
        self.username = username
        self.api_token = api_token
        credentials = base64.b64encode(
            f"{username}:{api_token}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class GuruRESTClientViaOAuth(HTTPClient):
    """Guru REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (default: https://api.getguru.com/api/v1)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://api.getguru.com/api/v1",
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


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class GuruBasicAuthConfig(BaseModel):
    """Configuration for Guru client via Basic Auth.

    Args:
        username: Guru account email / username
        api_token: Guru API token
        base_url: API base URL (default: https://api.getguru.com/api/v1)
    """

    username: str
    api_token: str
    base_url: str = "https://api.getguru.com/api/v1"

    def create_client(self) -> GuruRESTClientViaBasicAuth:
        return GuruRESTClientViaBasicAuth(
            self.username, self.api_token, self.base_url
        )


class GuruOAuthConfig(BaseModel):
    """Configuration for Guru client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.getguru.com/api/v1)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = "https://api.getguru.com/api/v1"

    def create_client(self) -> GuruRESTClientViaOAuth:
        return GuruRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class GuruAuthConfigModel(BaseModel):
    """Auth section of the Guru connector configuration from etcd."""

    authType: GuruAuthType = GuruAuthType.BASIC
    username: str | None = None
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class GuruCredentialsConfig(BaseModel):
    """Credentials section of the Guru connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class GuruConnectorConfig(BaseModel):
    """Top-level Guru connector configuration from etcd."""

    auth: GuruAuthConfigModel = Field(default_factory=GuruAuthConfigModel)
    credentials: GuruCredentialsConfig = Field(
        default_factory=GuruCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class GuruClient(IClient):
    """Builder class for Guru clients with different authentication methods.

    Supports:
    - Basic Auth (username:api_token)
    - OAuth 2.0 access token authentication
    """

    def __init__(
        self,
        client: GuruRESTClientViaBasicAuth | GuruRESTClientViaOAuth,
    ) -> None:
        """Initialize with a Guru client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> GuruRESTClientViaBasicAuth | GuruRESTClientViaOAuth:
        """Return the Guru client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: GuruBasicAuthConfig | GuruOAuthConfig,
    ) -> "GuruClient":
        """Build GuruClient with configuration.

        Args:
            config: GuruBasicAuthConfig or GuruOAuthConfig instance

        Returns:
            GuruClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GuruClient":
        """Build GuruClient using configuration service.

        Supports two authentication strategies:
        1. BASIC: Basic Auth with username and api_token
        2. OAUTH: OAuth 2.0 access token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            GuruClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Guru connector configuration"
                )

            connector_config = GuruConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == GuruAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/guru", default=[]
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

                oauth_cfg = GuruOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == GuruAuthType.BASIC:
                username = connector_config.auth.username or ""
                api_token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )

                if not (username and api_token):
                    raise ValueError(
                        "Username and API token required for Basic auth type"
                    )

                basic_cfg = GuruBasicAuthConfig(
                    username=username,
                    api_token=api_token,
                )
                return cls(basic_cfg.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Guru client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "GuruClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            GuruClient instance
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

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/guru", default=[]
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

            oauth_cfg = GuruOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Guru client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Guru."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Guru connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Guru connector config: {e}")
            raise ValueError(
                f"Failed to get Guru connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
