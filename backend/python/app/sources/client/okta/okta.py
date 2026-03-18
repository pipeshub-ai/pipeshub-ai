# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Okta client implementation using the official ``okta`` SDK.

This module provides clients for interacting with the Okta API using either:
1. API Token (SSWS token)
2. OAuth 2.0 (authorization code flow)

The SDK is initialised with the Okta org URL and a token; the underlying
``okta.client.Client`` object is exposed via ``get_sdk()``.

SDK Reference: https://github.com/okta/okta-sdk-python
API Reference: https://developer.okta.com/docs/api/
"""

import logging
from enum import Enum
from typing import Any, cast

from okta.client import Client as OktaSDKClient
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OktaAuthType(str, Enum):
    """Authentication types supported by the Okta connector."""

    OAUTH = "OAUTH"
    API_TOKEN = "API_TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class OktaResponse(BaseModel):
    """Standardised Okta API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Any = Field(default=None, description="Response data from the SDK")
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"
        arbitrary_types_allowed = True

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK wrapper client
# ---------------------------------------------------------------------------


class OktaClientViaApiToken:
    """Okta SDK client via API Token.

    Wraps the official ``okta`` SDK ``Client`` object.

    Args:
        domain: Full Okta org URL (e.g. ``https://dev-123456.okta.com``)
        api_token: The Okta API token
    """

    def __init__(self, domain: str, api_token: str) -> None:
        # Normalise: make sure domain is a full URL
        if not domain.startswith("http"):
            domain = f"https://{domain}.okta.com"
        self.domain = domain.rstrip("/")
        self.api_token = api_token

        self._sdk: OktaSDKClient | None = None

    def create_client(self) -> OktaSDKClient:
        config = {
            "orgUrl": self.domain,
            "token": self.api_token,
        }
        self._sdk = OktaSDKClient(config)
        return self._sdk

    def get_sdk(self) -> OktaSDKClient:
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return self.domain

    def get_domain(self) -> str:
        return self.domain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class OktaApiTokenConfig(BaseModel):
    """Configuration for Okta client via API Token.

    Args:
        api_token: The Okta API token
        domain: Okta domain (e.g. ``dev-123456`` or full URL)
    """

    api_token: str
    domain: str

    def create_client(self) -> OktaClientViaApiToken:
        return OktaClientViaApiToken(domain=self.domain, api_token=self.api_token)


class OktaOAuthConfig(BaseModel):
    """Configuration for Okta client via OAuth 2.0.

    For OAuth, the access_token is passed as the API token to the SDK.

    Args:
        access_token: The OAuth access token
        domain: Okta domain
        client_id: OAuth client ID
        client_secret: OAuth client secret
        redirect_uri: OAuth redirect URI
    """

    access_token: str
    domain: str
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None

    def create_client(self) -> OktaClientViaApiToken:
        # The SDK accepts an access token in the same way as an API token
        return OktaClientViaApiToken(
            domain=self.domain,
            api_token=self.access_token,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class OktaAuthConfigModel(BaseModel):
    """Auth section of the Okta connector configuration from etcd."""

    authType: OktaAuthType = OktaAuthType.API_TOKEN
    apiToken: str | None = None
    token: str | None = None
    domain: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class OktaCredentialsConfig(BaseModel):
    """Credentials section of the Okta connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class OktaConnectorConfig(BaseModel):
    """Top-level Okta connector configuration from etcd."""

    auth: OktaAuthConfigModel = Field(default_factory=OktaAuthConfigModel)
    credentials: OktaCredentialsConfig = Field(
        default_factory=OktaCredentialsConfig
    )
    domain: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class OktaClient(IClient):
    """Builder class for Okta clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - API Token (SSWS) authentication
    """

    def __init__(self, client: OktaClientViaApiToken) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> OktaClientViaApiToken:
        return self.client

    def get_sdk(self) -> OktaSDKClient:
        return self.client.get_sdk()

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @property
    def domain(self) -> str:
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: OktaOAuthConfig | OktaApiTokenConfig,
    ) -> "OktaClient":
        client = config.create_client()
        _ = client.get_sdk()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "OktaClient":
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Okta connector configuration"
                )

            connector_config = OktaConnectorConfig.model_validate(raw_config)

            okta_domain = (
                connector_config.auth.domain
                or connector_config.domain
                or ""
            )
            if not okta_domain:
                raise ValueError("Domain required for Okta")

            if connector_config.auth.authType == OktaAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""
                redirect_uri = connector_config.auth.redirectUri or ""

                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/okta", default=[]
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
                                redirect_uri = str(
                                    shared.get("redirectUri")
                                    or shared.get("redirect_uri")
                                    or redirect_uri
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

                oauth_cfg = OktaOAuthConfig(
                    access_token=access_token,
                    domain=okta_domain,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == OktaAuthType.API_TOKEN:
                api_token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not api_token:
                    raise ValueError(
                        "API token required for API_TOKEN auth type"
                    )

                token_config = OktaApiTokenConfig(
                    api_token=api_token, domain=okta_domain
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Okta client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "OktaClient":
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            okta_domain: str = str(
                auth_config.get("domain", "")
                or toolset_config.get("domain", "")
            )
            if not okta_domain:
                raise ValueError(
                    "Domain not found in toolset config"
                )

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))
            redirect_uri: str = str(auth_config.get("redirectUri", ""))

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
                        "/services/oauth/okta", default=[]
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
                            redirect_uri = str(
                                shared.get("redirectUri")
                                or shared.get("redirect_uri")
                                or redirect_uri
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            oauth_cfg = OktaOAuthConfig(
                access_token=access_token,
                domain=okta_domain,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Okta client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Okta connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Okta connector config: {e}")
            raise ValueError(
                f"Failed to get Okta connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
