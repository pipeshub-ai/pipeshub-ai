"""Egnyte client implementation.

This module provides clients for interacting with the Egnyte API using either:
1. OAuth 2.0 access token authentication
2. Pre-generated Access Token (Bearer)

Authentication Reference: https://developers.egnyte.com/docs
API Base URL: https://{domain}.egnyte.com/pubapi/v1
OAuth Token Endpoint: https://{domain}.egnyte.com/puboauth/token
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


class EgnyteAuthType(str, Enum):
    """Authentication types supported by the Egnyte connector."""

    OAUTH = "OAUTH"
    ACCESS_TOKEN = "ACCESS_TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class EgnyteResponse(BaseModel):
    """Standardized Egnyte API response wrapper.

    The data field supports JSON responses (dict/list) and binary file
    downloads (bytes). When serializing to dict/JSON, binary data is
    automatically base64-encoded.
    """

    success: bool = Field(
        ..., description="Whether the request was successful"
    )
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None,
        description="Response data (JSON) or file content (bytes)",
    )
    error: str | None = Field(
        default=None, description="Error message if failed"
    )
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


class EgnyteRESTClientViaToken(HTTPClient):
    """Egnyte REST client via pre-generated Access Token.

    Args:
        token: The access token (Bearer)
        domain: Egnyte domain (e.g. 'mycompany' for mycompany.egnyte.com)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        token: str,
        domain: str,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(token, token_type="Bearer", timeout=timeout)
        self.domain = domain
        self.base_url = f"https://{domain}.egnyte.com/pubapi/v1"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_domain(self) -> str:
        """Get the Egnyte domain."""
        return self.domain


class EgnyteRESTClientViaOAuth(HTTPClient):
    """Egnyte REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        domain: Egnyte domain (e.g. 'mycompany' for mycompany.egnyte.com)
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        access_token: str,
        domain: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(access_token, "Bearer", timeout=timeout)
        self.domain = domain
        self.base_url = f"https://{domain}.egnyte.com/pubapi/v1"
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_domain(self) -> str:
        """Get the Egnyte domain."""
        return self.domain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class EgnyteTokenConfig(BaseModel):
    """Configuration for Egnyte client via Access Token.

    Args:
        token: The access token
        domain: Egnyte domain name (e.g. 'mycompany')
    """

    token: str
    domain: str

    def create_client(self) -> EgnyteRESTClientViaToken:
        return EgnyteRESTClientViaToken(self.token, self.domain)


class EgnyteOAuthConfig(BaseModel):
    """Configuration for Egnyte client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        domain: Egnyte domain name (e.g. 'mycompany')
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    domain: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> EgnyteRESTClientViaOAuth:
        return EgnyteRESTClientViaOAuth(
            self.access_token,
            self.domain,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class EgnyteAuthConfig(BaseModel):
    """Auth section of the Egnyte connector configuration from etcd."""

    authType: EgnyteAuthType = EgnyteAuthType.ACCESS_TOKEN
    apiToken: str | None = None
    token: str | None = None
    domain: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class EgnyteCredentialsConfig(BaseModel):
    """Credentials section of the Egnyte connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class EgnyteConnectorConfig(BaseModel):
    """Top-level Egnyte connector configuration from etcd."""

    auth: EgnyteAuthConfig = Field(default_factory=EgnyteAuthConfig)
    credentials: EgnyteCredentialsConfig = Field(
        default_factory=EgnyteCredentialsConfig
    )
    domain: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class EgnyteClient(IClient):
    """Builder class for Egnyte clients with different authentication methods.

    Supports:
    - Access Token authentication
    - OAuth 2.0 access token authentication
    """

    def __init__(
        self,
        client: EgnyteRESTClientViaToken | EgnyteRESTClientViaOAuth,
    ) -> None:
        """Initialize with an Egnyte client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> EgnyteRESTClientViaToken | EgnyteRESTClientViaOAuth:
        """Return the Egnyte client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @property
    def domain(self) -> str:
        """Return the Egnyte domain."""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: EgnyteTokenConfig | EgnyteOAuthConfig,
    ) -> "EgnyteClient":
        """Build EgnyteClient with configuration.

        Args:
            config: EgnyteTokenConfig or EgnyteOAuthConfig instance

        Returns:
            EgnyteClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "EgnyteClient":
        """Build EgnyteClient using configuration service.

        Supports two authentication strategies:
        1. ACCESS_TOKEN: For pre-generated access tokens
        2. OAUTH: For OAuth 2.0 access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            EgnyteClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Egnyte connector configuration"
                )

            connector_config = EgnyteConnectorConfig.model_validate(
                raw_config
            )

            domain = (
                connector_config.domain
                or connector_config.auth.domain
                or ""
            )
            if not domain:
                raise ValueError(
                    "Egnyte domain is required in configuration"
                )

            if connector_config.auth.authType == EgnyteAuthType.OAUTH:
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
                            "/services/oauth/egnyte", default=[]
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

                oauth_cfg = EgnyteOAuthConfig(
                    access_token=access_token,
                    domain=domain,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif (
                connector_config.auth.authType
                == EgnyteAuthType.ACCESS_TOKEN
            ):
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Access token required for ACCESS_TOKEN auth type"
                    )

                token_config = EgnyteTokenConfig(
                    token=token, domain=domain
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Egnyte client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Egnyte."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Egnyte connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Egnyte connector config: {e}")
            raise ValueError(
                f"Failed to get Egnyte connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
