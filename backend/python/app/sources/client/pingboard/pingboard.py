"""Pingboard client implementation.

This module provides clients for interacting with the Pingboard API using either:
1. OAuth2 client_credentials grant (server-to-server)
2. Pre-generated Bearer token

Pingboard is an employee directory and org chart platform.

API Reference: https://app.pingboard.com/api/v2
Authentication:
  - OAuth2 client_credentials: POST to https://app.pingboard.com/oauth/token
  - Bearer token: Authorization: Bearer {token}
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PingboardResponse(BaseModel):
    """Standardized Pingboard API response wrapper.

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


class PingboardRESTClientViaClientCredentials(HTTPClient):
    """Pingboard REST client via OAuth2 client_credentials grant.

    Uses client_credentials grant type to obtain an access token from the
    Pingboard OAuth token endpoint. The token is fetched automatically on
    first use via ensure_authenticated().

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://app.pingboard.com/api/v2)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://app.pingboard.com/api/v2",
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch access token via client_credentials grant if not authenticated.

        Posts to the Pingboard token endpoint with grant_type=client_credentials.
        """
        if self._authenticated:
            return

        token_request = HTTPRequest(
            url="https://app.pingboard.com/oauth/token",
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )

        response = await self.execute(token_request)  # type: ignore[reportUnknownMemberType]
        response_data = response.json()

        access_token = response_data.get("access_token")
        if not access_token:
            raise ValueError(
                "Failed to obtain access token from Pingboard OAuth: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class PingboardRESTClientViaToken(HTTPClient):
    """Pingboard REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://app.pingboard.com/api/v2)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://app.pingboard.com/api/v2",
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


class PingboardClientCredentialsConfig(BaseModel):
    """Configuration for Pingboard client via client_credentials grant.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://app.pingboard.com/api/v2)
    """

    client_id: str
    client_secret: str
    base_url: str = "https://app.pingboard.com/api/v2"

    def create_client(self) -> PingboardRESTClientViaClientCredentials:
        return PingboardRESTClientViaClientCredentials(
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class PingboardTokenConfig(BaseModel):
    """Configuration for Pingboard client via Bearer token.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://app.pingboard.com/api/v2)
    """

    token: str
    base_url: str = "https://app.pingboard.com/api/v2"

    def create_client(self) -> PingboardRESTClientViaToken:
        return PingboardRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class PingboardAuthConfig(BaseModel):
    """Auth section of the Pingboard connector configuration from etcd."""

    clientId: str | None = None
    clientSecret: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class PingboardCredentialsConfig(BaseModel):
    """Credentials section of the Pingboard connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class PingboardConnectorConfig(BaseModel):
    """Top-level Pingboard connector configuration from etcd."""

    auth: PingboardAuthConfig = Field(default_factory=PingboardAuthConfig)
    credentials: PingboardCredentialsConfig = Field(
        default_factory=PingboardCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class PingboardClient(IClient):
    """Builder class for Pingboard clients with different auth methods.

    Supports:
    - OAuth2 client_credentials grant
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: (
            PingboardRESTClientViaClientCredentials
            | PingboardRESTClientViaToken
        ),
    ) -> None:
        """Initialize with a Pingboard client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> PingboardRESTClientViaClientCredentials | PingboardRESTClientViaToken:
        """Return the Pingboard client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: PingboardClientCredentialsConfig | PingboardTokenConfig,
    ) -> "PingboardClient":
        """Build PingboardClient with configuration.

        Args:
            config: PingboardClientCredentialsConfig or PingboardTokenConfig

        Returns:
            PingboardClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PingboardClient":
        """Build PingboardClient using configuration service.

        Supports two authentication strategies:
        1. CLIENT_CREDENTIALS: client_id and client_secret for S2S OAuth
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            PingboardClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Pingboard connector configuration"
                )

            connector_config = PingboardConnectorConfig.model_validate(
                raw_config
            )

            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""

            # Try shared OAuth config if credentials are missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not (client_id and client_secret):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/pingboard", default=[]
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

            # Prefer client_credentials if both are available
            if client_id and client_secret:
                cc_config = PingboardClientCredentialsConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(cc_config.create_client())

            # Fall back to token
            token = (
                connector_config.auth.token
                or connector_config.credentials.access_token
                or ""
            )
            if token:
                token_config = PingboardTokenConfig(token=token)
                return cls(token_config.create_client())

            raise ValueError(
                "Either client_id/client_secret or token required "
                "for Pingboard authentication"
            )

        except Exception as e:
            logger.error(
                f"Failed to build Pingboard client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "PingboardClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            PingboardClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            if client_id and client_secret:
                cc_config = PingboardClientCredentialsConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(cc_config.create_client())

            token: str = str(
                credentials.get("access_token", "")
                or auth_config.get("token", "")
            )
            if not token:
                raise ValueError(
                    "Client credentials or token not found in toolset config"
                )

            token_config = PingboardTokenConfig(token=token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Pingboard client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Pingboard."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Pingboard connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Pingboard connector config: {e}")
            raise ValueError(
                f"Failed to get Pingboard connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
