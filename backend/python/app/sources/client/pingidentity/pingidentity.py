"""Ping Identity (PingOne) client implementation.

This module provides clients for interacting with the PingOne API using either:
1. OAuth2 (client_credentials grant)
2. Pre-generated Bearer token

Authentication Reference: https://apidocs.pingidentity.com/pingone/platform/v1/api/
Token Endpoint: https://auth.pingone.com/{environmentId}/as/token
API Base URL: https://api.pingone.com/v1/environments/{environmentId}
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
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PingIdentityAuthType(str, Enum):
    """Authentication types supported by the PingIdentity connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PingIdentityResponse(BaseModel):
    """Standardized PingIdentity API response wrapper.

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


class PingIdentityRESTClientViaOAuth(HTTPClient):
    """PingOne REST client via OAuth2 client_credentials grant.

    Fetches an access token from the PingOne token endpoint using
    client_credentials grant. The token is obtained automatically on
    first use via ensure_authenticated().

    Args:
        environment_id: PingOne environment ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    def __init__(
        self,
        environment_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.environment_id = environment_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            f"https://api.pingone.com/v1/environments/{self.environment_id}"
        )
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via client_credentials grant if needed.

        Uses HTTP Basic Auth (client_id:client_secret) and posts to the
        PingOne token endpoint with grant_type=client_credentials.
        """
        if self._authenticated:
            return

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode("utf-8")

        token_url = (
            f"https://auth.pingone.com/{self.environment_id}/as/token"
        )

        token_request = HTTPRequest(
            url=token_url,
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body={
                "grant_type": "client_credentials",
            },
        )

        response = await self.execute(token_request)  # type: ignore[reportUnknownMemberType]
        response_data = response.json()

        access_token = response_data.get("access_token")
        if not access_token:
            raise ValueError(
                "Failed to obtain access token from PingOne: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class PingIdentityRESTClientViaToken(HTTPClient):
    """PingOne REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        environment_id: PingOne environment ID
    """

    def __init__(
        self,
        token: str,
        environment_id: str,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.environment_id = environment_id
        self.base_url = (
            f"https://api.pingone.com/v1/environments/{self.environment_id}"
        )
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class PingIdentityOAuthConfig(BaseModel):
    """Configuration for PingIdentity client via OAuth2.

    Args:
        environment_id: PingOne environment ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    environment_id: str
    client_id: str
    client_secret: str

    def create_client(self) -> PingIdentityRESTClientViaOAuth:
        return PingIdentityRESTClientViaOAuth(
            self.environment_id,
            self.client_id,
            self.client_secret,
        )


class PingIdentityTokenConfig(BaseModel):
    """Configuration for PingIdentity client via Bearer token.

    Args:
        token: The pre-generated Bearer token
        environment_id: PingOne environment ID
    """

    token: str
    environment_id: str

    def create_client(self) -> PingIdentityRESTClientViaToken:
        return PingIdentityRESTClientViaToken(
            self.token, self.environment_id
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class PingIdentityAuthConfig(BaseModel):
    """Auth section of the PingIdentity connector configuration from etcd."""

    authType: PingIdentityAuthType = PingIdentityAuthType.OAUTH
    environmentId: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class PingIdentityCredentialsConfig(BaseModel):
    """Credentials section of the PingIdentity connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class PingIdentityConnectorConfig(BaseModel):
    """Top-level PingIdentity connector configuration from etcd."""

    auth: PingIdentityAuthConfig = Field(
        default_factory=PingIdentityAuthConfig
    )
    credentials: PingIdentityCredentialsConfig = Field(
        default_factory=PingIdentityCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class PingIdentityClient(IClient):
    """Builder class for PingIdentity clients with different auth methods.

    Supports:
    - OAuth2 (client_credentials grant) authentication
    - Pre-generated Bearer token authentication
    """

    def __init__(
        self,
        client: (
            PingIdentityRESTClientViaOAuth | PingIdentityRESTClientViaToken
        ),
    ) -> None:
        """Initialize with a PingIdentity client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> PingIdentityRESTClientViaOAuth | PingIdentityRESTClientViaToken:
        """Return the PingIdentity client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: PingIdentityOAuthConfig | PingIdentityTokenConfig,
    ) -> "PingIdentityClient":
        """Build PingIdentityClient with configuration.

        Args:
            config: PingIdentityOAuthConfig or PingIdentityTokenConfig

        Returns:
            PingIdentityClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PingIdentityClient":
        """Build PingIdentityClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: Client credentials grant
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            PingIdentityClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get PingIdentity connector configuration"
                )

            connector_config = PingIdentityConnectorConfig.model_validate(
                raw_config
            )

            environment_id = connector_config.auth.environmentId or ""
            if not environment_id:
                raise ValueError(
                    "environmentId is required for PingIdentity"
                )

            if connector_config.auth.authType == PingIdentityAuthType.OAUTH:
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/pingidentity", default=[]
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

                if not (client_id and client_secret):
                    raise ValueError(
                        "client_id and client_secret are required "
                        "for OAuth auth type"
                    )

                oauth_cfg = PingIdentityOAuthConfig(
                    environment_id=environment_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == PingIdentityAuthType.TOKEN:
                token = (
                    connector_config.auth.token
                    or connector_config.credentials.access_token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = PingIdentityTokenConfig(
                    token=token, environment_id=environment_id
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                "Failed to build PingIdentity client from services: "
                f"{str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "PingIdentityClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            PingIdentityClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            environment_id: str = str(
                auth_config.get("environmentId", "")
            )
            if not environment_id:
                raise ValueError(
                    "environmentId not found in toolset config"
                )

            access_token: str = str(credentials.get("access_token", ""))
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
                        "/services/oauth/pingidentity", default=[]
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

            # If we have client credentials, use OAuth flow
            if client_id and client_secret:
                oauth_cfg = PingIdentityOAuthConfig(
                    environment_id=environment_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            # Otherwise use the access token directly
            if not access_token:
                raise ValueError(
                    "Access token or client credentials required"
                )

            token_config = PingIdentityTokenConfig(
                token=access_token, environment_id=environment_id
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                "Failed to build PingIdentity client from toolset: "
                f"{str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for PingIdentity."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get PingIdentity connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get PingIdentity connector config: {e}"
            )
            raise ValueError(
                f"Failed to get PingIdentity connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
