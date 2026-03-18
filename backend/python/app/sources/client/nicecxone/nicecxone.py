"""NICE CXone client implementation.

This module provides clients for interacting with the NICE CXone API using either:
1. OAuth 2.0 authentication (client_credentials or authorization code)
2. Pre-generated Bearer token authentication

The base URL includes a cluster parameter, as NICE CXone APIs are cluster-specific:
https://api-{cluster}.niceincontact.com/incontactapi/services/v31.0

Authentication Reference: https://developer.niceincontact.com/Documentation/Authentication
API Reference: https://developer.niceincontact.com/API/
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


class NiceCXoneAuthType(str, Enum):
    """Authentication types supported by the NICE CXone connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class NiceCXoneResponse(BaseModel):
    """Standardized NICE CXone API response wrapper.

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


class NiceCXoneRESTClientViaOAuth(HTTPClient):
    """NICE CXone REST client via OAuth 2.0.

    Uses client_credentials grant type to obtain an access token from the
    NICE CXone OAuth token endpoint. The token is fetched automatically
    on first use via ensure_authenticated().

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        auth_domain: The authentication domain (e.g., cxone.niceincontact.com)
        cluster: The cluster identifier for the API base URL
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        auth_domain: str,
        cluster: str = "c1",
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.cluster = cluster
        self.base_url = (
            f"https://api-{cluster}.niceincontact.com"
            f"/incontactapi/services/v31.0"
        )
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_domain = auth_domain
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via client_credentials grant.

        Posts to the NICE CXone token endpoint with grant_type=client_credentials.
        """
        if self._authenticated:
            return

        token_request = HTTPRequest(
            url=f"https://{self.auth_domain}/auth/token",
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
                "Failed to obtain access token from NICE CXone OAuth: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class NiceCXoneRESTClientViaToken(HTTPClient):
    """NICE CXone REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        cluster: The cluster identifier for the API base URL
    """

    def __init__(
        self,
        token: str,
        cluster: str = "c1",
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.cluster = cluster
        self.base_url = (
            f"https://api-{cluster}.niceincontact.com"
            f"/incontactapi/services/v31.0"
        )
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class NiceCXoneOAuthConfig(BaseModel):
    """Configuration for NICE CXone client via OAuth 2.0.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        auth_domain: The authentication domain
        cluster: The cluster identifier (default: "c1")
    """

    client_id: str
    client_secret: str
    auth_domain: str
    cluster: str = "c1"

    def create_client(self) -> NiceCXoneRESTClientViaOAuth:
        return NiceCXoneRESTClientViaOAuth(
            self.client_id,
            self.client_secret,
            self.auth_domain,
            self.cluster,
        )


class NiceCXoneTokenConfig(BaseModel):
    """Configuration for NICE CXone client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        cluster: The cluster identifier (default: "c1")
    """

    token: str
    cluster: str = "c1"

    def create_client(self) -> NiceCXoneRESTClientViaToken:
        return NiceCXoneRESTClientViaToken(self.token, self.cluster)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class NiceCXoneAuthConfig(BaseModel):
    """Auth section of the NICE CXone connector configuration from etcd."""

    authType: NiceCXoneAuthType = NiceCXoneAuthType.TOKEN
    clientId: str | None = None
    clientSecret: str | None = None
    authDomain: str | None = None
    token: str | None = None
    cluster: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class NiceCXoneCredentialsConfig(BaseModel):
    """Credentials section of the NICE CXone connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class NiceCXoneConnectorConfig(BaseModel):
    """Top-level NICE CXone connector configuration from etcd."""

    auth: NiceCXoneAuthConfig = Field(default_factory=NiceCXoneAuthConfig)
    credentials: NiceCXoneCredentialsConfig = Field(
        default_factory=NiceCXoneCredentialsConfig
    )
    cluster: str = "c1"

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class NiceCXoneClient(IClient):
    """Builder class for NICE CXone clients with different auth methods.

    Supports:
    - OAuth 2.0 client_credentials grant
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: NiceCXoneRESTClientViaOAuth | NiceCXoneRESTClientViaToken,
    ) -> None:
        """Initialize with a NICE CXone client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> NiceCXoneRESTClientViaOAuth | NiceCXoneRESTClientViaToken:
        """Return the NICE CXone client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: NiceCXoneOAuthConfig | NiceCXoneTokenConfig,
    ) -> "NiceCXoneClient":
        """Build NiceCXoneClient with configuration.

        Args:
            config: NiceCXoneOAuthConfig or NiceCXoneTokenConfig instance

        Returns:
            NiceCXoneClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "NiceCXoneClient":
        """Build NiceCXoneClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: Client credentials grant with client_id, client_secret,
           and auth_domain
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            NiceCXoneClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get NICE CXone connector configuration"
                )

            connector_config = NiceCXoneConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == NiceCXoneAuthType.OAUTH:
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""
                auth_domain = connector_config.auth.authDomain or ""
                cluster = (
                    connector_config.auth.cluster
                    or connector_config.cluster
                )

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/nicecxone", default=[]
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
                                auth_domain = str(
                                    shared.get("authDomain")
                                    or shared.get("auth_domain")
                                    or auth_domain
                                )
                                break
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch shared OAuth config: {e}"
                        )

                if not (client_id and client_secret and auth_domain):
                    raise ValueError(
                        "client_id, client_secret, and auth_domain are "
                        "required for OAuth auth type"
                    )

                oauth_cfg = NiceCXoneOAuthConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    auth_domain=auth_domain,
                    cluster=cluster,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == NiceCXoneAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    # Fall back to credentials access_token
                    token = connector_config.credentials.access_token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                cluster = (
                    connector_config.auth.cluster
                    or connector_config.cluster
                )
                token_config = NiceCXoneTokenConfig(
                    token=token, cluster=cluster
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build NICE CXone client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "NiceCXoneClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            NiceCXoneClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            cluster: str = str(toolset_config.get("cluster", "c1"))

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = NiceCXoneTokenConfig(
                token=access_token, cluster=cluster
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build NICE CXone client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for NICE CXone."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get NICE CXone connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get NICE CXone connector config: {e}"
            )
            raise ValueError(
                f"Failed to get NICE CXone connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
