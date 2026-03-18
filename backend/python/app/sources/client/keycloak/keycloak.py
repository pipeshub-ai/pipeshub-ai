"""Keycloak client implementation.

This module provides clients for interacting with the Keycloak Admin REST API
using either:
1. OAuth2 (client_credentials or password grant)
2. Pre-generated Bearer token

Authentication Reference: https://www.keycloak.org/docs-api/latest/rest-api/
Token Endpoint: https://{hostname}/realms/{realm}/protocol/openid-connect/token
Admin API Base: https://{hostname}/admin/realms/{realm}
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


class KeycloakAuthType(str, Enum):
    """Authentication types supported by the Keycloak connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class KeycloakResponse(BaseModel):
    """Standardized Keycloak API response wrapper.

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


class KeycloakRESTClientViaOAuth(HTTPClient):
    """Keycloak REST client via OAuth2 (client_credentials or password grant).

    Fetches an access token from the Keycloak token endpoint using
    client_credentials grant. The token is obtained automatically on
    first use via ensure_authenticated().

    Args:
        hostname: Keycloak server hostname (e.g. keycloak.example.com)
        realm: Keycloak realm name
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    def __init__(
        self,
        hostname: str,
        realm: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.hostname = hostname.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = f"https://{self.hostname}/admin/realms/{self.realm}"
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via client_credentials grant if needed."""
        if self._authenticated:
            return

        token_url = (
            f"https://{self.hostname}/realms/{self.realm}"
            f"/protocol/openid-connect/token"
        )

        token_request = HTTPRequest(
            url=token_url,
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
                "Failed to obtain access token from Keycloak: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class KeycloakRESTClientViaToken(HTTPClient):
    """Keycloak REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        hostname: Keycloak server hostname
        realm: Keycloak realm name
    """

    def __init__(
        self,
        token: str,
        hostname: str,
        realm: str,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.hostname = hostname.rstrip("/")
        self.realm = realm
        self.base_url = f"https://{self.hostname}/admin/realms/{self.realm}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class KeycloakOAuthConfig(BaseModel):
    """Configuration for Keycloak client via OAuth2.

    Args:
        hostname: Keycloak server hostname
        realm: Keycloak realm name
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    hostname: str
    realm: str
    client_id: str
    client_secret: str

    def create_client(self) -> KeycloakRESTClientViaOAuth:
        return KeycloakRESTClientViaOAuth(
            self.hostname,
            self.realm,
            self.client_id,
            self.client_secret,
        )


class KeycloakTokenConfig(BaseModel):
    """Configuration for Keycloak client via Bearer token.

    Args:
        token: The pre-generated Bearer token
        hostname: Keycloak server hostname
        realm: Keycloak realm name
    """

    token: str
    hostname: str
    realm: str

    def create_client(self) -> KeycloakRESTClientViaToken:
        return KeycloakRESTClientViaToken(
            self.token, self.hostname, self.realm
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class KeycloakAuthConfig(BaseModel):
    """Auth section of the Keycloak connector configuration from etcd."""

    authType: KeycloakAuthType = KeycloakAuthType.OAUTH
    hostname: str | None = None
    realm: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class KeycloakCredentialsConfig(BaseModel):
    """Credentials section of the Keycloak connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class KeycloakConnectorConfig(BaseModel):
    """Top-level Keycloak connector configuration from etcd."""

    auth: KeycloakAuthConfig = Field(default_factory=KeycloakAuthConfig)
    credentials: KeycloakCredentialsConfig = Field(
        default_factory=KeycloakCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class KeycloakClient(IClient):
    """Builder class for Keycloak clients with different authentication methods.

    Supports:
    - OAuth2 (client_credentials grant) authentication
    - Pre-generated Bearer token authentication
    """

    def __init__(
        self,
        client: KeycloakRESTClientViaOAuth | KeycloakRESTClientViaToken,
    ) -> None:
        """Initialize with a Keycloak client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> KeycloakRESTClientViaOAuth | KeycloakRESTClientViaToken:
        """Return the Keycloak client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: KeycloakOAuthConfig | KeycloakTokenConfig,
    ) -> "KeycloakClient":
        """Build KeycloakClient with configuration.

        Args:
            config: KeycloakOAuthConfig or KeycloakTokenConfig instance

        Returns:
            KeycloakClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "KeycloakClient":
        """Build KeycloakClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: Client credentials grant with client_id and client_secret
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            KeycloakClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Keycloak connector configuration"
                )

            connector_config = KeycloakConnectorConfig.model_validate(
                raw_config
            )

            hostname = connector_config.auth.hostname or ""
            realm = connector_config.auth.realm or ""

            if not (hostname and realm):
                raise ValueError(
                    "hostname and realm are required for Keycloak"
                )

            if connector_config.auth.authType == KeycloakAuthType.OAUTH:
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/keycloak", default=[]
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

                oauth_cfg = KeycloakOAuthConfig(
                    hostname=hostname,
                    realm=realm,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == KeycloakAuthType.TOKEN:
                token = (
                    connector_config.auth.token
                    or connector_config.credentials.access_token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = KeycloakTokenConfig(
                    token=token, hostname=hostname, realm=realm
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Keycloak client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "KeycloakClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            KeycloakClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            hostname: str = str(auth_config.get("hostname", ""))
            realm: str = str(auth_config.get("realm", ""))
            if not (hostname and realm):
                raise ValueError(
                    "hostname and realm not found in toolset config"
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
                        "/services/oauth/keycloak", default=[]
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
                oauth_cfg = KeycloakOAuthConfig(
                    hostname=hostname,
                    realm=realm,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            # Otherwise use the access token directly
            token_config = KeycloakTokenConfig(
                token=access_token, hostname=hostname, realm=realm
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Keycloak client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Keycloak."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Keycloak connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Keycloak connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Keycloak connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
