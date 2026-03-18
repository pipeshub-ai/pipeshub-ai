"""OneTrust client implementation.

This module provides clients for interacting with the OneTrust API using either:
1. OAuth 2.0 client_credentials flow
2. Bearer Token authentication

Token Endpoint: https://{hostname}/api/access/v1/oauth/token
API Base URL: https://{hostname}/api
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

import httpx  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OneTrustAuthType(str, Enum):
    """Authentication types supported by the OneTrust connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class OneTrustResponse(BaseModel):
    """Standardized OneTrust API response wrapper.

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


class OneTrustRESTClientViaOAuth(HTTPClient):
    """OneTrust REST client via OAuth 2.0 client_credentials.

    Automatically fetches an access token from the OneTrust token endpoint.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        hostname: The OneTrust hostname (e.g. 'mycompany.onetrust.com')
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        hostname: str,
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.base_url = f"https://{hostname}/api"
        self.hostname = hostname
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = (
            f"https://{hostname}/api/access/v1/oauth/token"
        )
        self._access_token: str | None = None
        self.headers["Content-Type"] = "application/json"

    async def _fetch_token(self) -> str:
        """Fetch an access token using client_credentials grant.

        Returns:
            Access token string.
        """
        async with httpx.AsyncClient() as client:  # type: ignore[reportUnknownMemberType]
            response = await client.post(  # type: ignore[reportUnknownMemberType]
                self.token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()  # type: ignore[reportUnknownMemberType]
            token_data: dict[str, Any] = response.json()  # type: ignore[reportUnknownMemberType]
            access_token: str = str(token_data.get("access_token", ""))  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            if not access_token:
                raise ValueError("No access_token in token response")
            return access_token

    async def ensure_token(self) -> None:
        """Ensure a valid access token is set in headers."""
        if not self._access_token:
            self._access_token = await self._fetch_token()
            self.headers["Authorization"] = f"Bearer {self._access_token}"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class OneTrustRESTClientViaToken(HTTPClient):
    """OneTrust REST client via Bearer Token.

    Args:
        token: The bearer token
        hostname: The OneTrust hostname (e.g. 'mycompany.onetrust.com')
    """

    def __init__(self, token: str, hostname: str) -> None:
        super().__init__(token, "Bearer")
        self.base_url = f"https://{hostname}/api"
        self.hostname = hostname
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class OneTrustOAuthConfig(BaseModel):
    """Configuration for OneTrust client via OAuth 2.0 client_credentials.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        hostname: The OneTrust hostname
    """

    client_id: str
    client_secret: str
    hostname: str

    def create_client(self) -> OneTrustRESTClientViaOAuth:
        return OneTrustRESTClientViaOAuth(
            self.client_id,
            self.client_secret,
            self.hostname,
        )


class OneTrustTokenConfig(BaseModel):
    """Configuration for OneTrust client via Bearer Token.

    Args:
        token: The bearer token
        hostname: The OneTrust hostname
    """

    token: str
    hostname: str

    def create_client(self) -> OneTrustRESTClientViaToken:
        return OneTrustRESTClientViaToken(self.token, self.hostname)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class OneTrustAuthConfigModel(BaseModel):
    """Auth section of the OneTrust connector configuration from etcd."""

    authType: OneTrustAuthType = OneTrustAuthType.OAUTH
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    hostname: str | None = None

    class Config:
        extra = "allow"


class OneTrustConnectorConfig(BaseModel):
    """Top-level OneTrust connector configuration from etcd."""

    auth: OneTrustAuthConfigModel = Field(
        default_factory=OneTrustAuthConfigModel
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class OneTrustClient(IClient):
    """Builder class for OneTrust clients with different authentication methods.

    Supports:
    - OAuth 2.0 client_credentials flow
    - Bearer Token authentication
    """

    def __init__(
        self,
        client: OneTrustRESTClientViaOAuth | OneTrustRESTClientViaToken,
    ) -> None:
        """Initialize with a OneTrust client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> OneTrustRESTClientViaOAuth | OneTrustRESTClientViaToken:
        """Return the OneTrust client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: OneTrustOAuthConfig | OneTrustTokenConfig,
    ) -> "OneTrustClient":
        """Build OneTrustClient with configuration.

        Args:
            config: OneTrustOAuthConfig or OneTrustTokenConfig instance

        Returns:
            OneTrustClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "OneTrustClient":
        """Build OneTrustClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 client_credentials
        2. TOKEN: Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            OneTrustClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get OneTrust connector configuration"
                )

            connector_config = OneTrustConnectorConfig.model_validate(
                raw_config
            )

            hostname = connector_config.auth.hostname or ""
            if not hostname:
                raise ValueError("OneTrust hostname is required")

            if connector_config.auth.authType == OneTrustAuthType.OAUTH:
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                if not (client_id and client_secret):
                    raise ValueError(
                        "client_id and client_secret required for OAuth auth type"
                    )

                oauth_config = OneTrustOAuthConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    hostname=hostname,
                )
                return cls(oauth_config.create_client())

            elif connector_config.auth.authType == OneTrustAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = OneTrustTokenConfig(
                    token=token,
                    hostname=hostname,
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build OneTrust client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "OneTrustClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused)

        Returns:
            OneTrustClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            hostname: str = str(auth_config.get("hostname", ""))
            if not hostname:
                raise ValueError(
                    "OneTrust hostname not found in toolset config"
                )

            auth_type = str(auth_config.get("authType", "OAUTH"))

            if auth_type == "TOKEN":
                token: str = str(auth_config.get("token", ""))
                if not token:
                    raise ValueError(
                        "Token not found in toolset config"
                    )
                config: OneTrustOAuthConfig | OneTrustTokenConfig = (
                    OneTrustTokenConfig(token=token, hostname=hostname)
                )
            else:
                client_id: str = str(auth_config.get("clientId", ""))
                client_secret: str = str(
                    auth_config.get("clientSecret", "")
                )
                if not (client_id and client_secret):
                    raise ValueError(
                        "client_id and client_secret not found in toolset config"
                    )
                config = OneTrustOAuthConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    hostname=hostname,
                )

            return cls(config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build OneTrust client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for OneTrust."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get OneTrust connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get OneTrust connector config: {e}")
            raise ValueError(
                f"Failed to get OneTrust connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
