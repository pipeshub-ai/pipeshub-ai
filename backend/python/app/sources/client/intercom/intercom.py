"""Intercom client implementation.

This module provides clients for interacting with the Intercom API using either:
1. OAuth 2.0 access token authentication
2. Access Token (Bearer) authentication

Authentication Reference: https://developers.intercom.com/docs/build-an-integration/learn-more/authentication/
API Reference: https://developers.intercom.com/docs/references/rest-api/api.intercom.io/
"""

import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)


class IntercomResponse(BaseModel):
    """Standardized Intercom API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | None = Field(
        default=None, description="Response data"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.model_dump_json(exclude_none=True)


class IntercomRESTClientViaOAuth(HTTPClient):
    """Intercom REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = "https://api.intercom.io"
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class IntercomRESTClientViaToken(HTTPClient):
    """Intercom REST client via Access Token.

    Access tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The access token
    """

    def __init__(self, access_token: str) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = "https://api.intercom.io"
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class IntercomOAuthConfig(BaseModel):
    """Configuration for Intercom client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> IntercomRESTClientViaOAuth:
        """Create an Intercom OAuth REST client."""
        return IntercomRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


class IntercomTokenConfig(BaseModel):
    """Configuration for Intercom client via Access Token.

    Args:
        access_token: The access token
    """

    access_token: str

    def create_client(self) -> IntercomRESTClientViaToken:
        """Create an Intercom Token REST client."""
        return IntercomRESTClientViaToken(self.access_token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class IntercomAuthConfig(BaseModel):
    """Auth section of the Intercom connector configuration from etcd."""

    authType: str = "OAUTH"
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class IntercomCredentialsConfig(BaseModel):
    """Credentials section of the Intercom connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class IntercomConnectorConfig(BaseModel):
    """Top-level Intercom connector configuration from etcd."""

    auth: IntercomAuthConfig = Field(default_factory=IntercomAuthConfig)
    credentials: IntercomCredentialsConfig = Field(
        default_factory=IntercomCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class IntercomClient(IClient):
    """Builder class for Intercom clients with different authentication methods.

    Supports:
    - OAuth 2.0 access token authentication
    - Access Token (Bearer) authentication
    """

    def __init__(
        self,
        client: IntercomRESTClientViaOAuth | IntercomRESTClientViaToken,
    ) -> None:
        """Initialize with an Intercom client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> IntercomRESTClientViaOAuth | IntercomRESTClientViaToken:
        """Return the Intercom client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: IntercomOAuthConfig | IntercomTokenConfig,
    ) -> "IntercomClient":
        """Build IntercomClient with configuration.

        Args:
            config: IntercomOAuthConfig or IntercomTokenConfig instance

        Returns:
            IntercomClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "IntercomClient":
        """Build IntercomClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: For OAuth 2.0 access tokens
        2. ACCESS_TOKEN: For direct access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            IntercomClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Intercom connector configuration"
                )

            connector_config = IntercomConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == "OAUTH":
                access_token = (
                    connector_config.credentials.access_token or ""
                )
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/intercom", default=[]
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

                oauth_cfg = IntercomOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == "ACCESS_TOKEN":
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Access token required for ACCESS_TOKEN auth type"
                    )

                token_config = IntercomTokenConfig(access_token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Intercom client from services: {e!s}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "IntercomClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            IntercomClient instance
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
            if (
                oauth_config_id
                and config_service
                and not (client_id and client_secret)
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/intercom", default=[]
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

            oauth_cfg = IntercomOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Intercom client from toolset: {e!s}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Intercom."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Intercom connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Intercom connector config: {e}")
            raise ValueError(
                f"Failed to get Intercom connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
