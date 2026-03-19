"""PagerDuty client implementation using the official pagerduty SDK.

This module provides clients for interacting with the PagerDuty API using either:
1. OAuth 2.0 authorization code flow (Bearer token)
2. API Token authentication (Token token=<value>)

The underlying SDK is ``pagerduty`` (PyPI, >=5.0.0).  All API access is routed
through ``RestApiV2Client`` which exposes ``rget``, ``rpost``, ``rput``,
``rdelete``, ``list_all``, ``iter_all``, and ``find`` methods.

Authentication Reference: https://developer.pagerduty.com/docs/authentication
API Reference: https://developer.pagerduty.com/api-reference/
"""

import logging
from enum import Enum
from typing import Any, cast

from pagerduty import RestApiV2Client  # type: ignore[import-untyped]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PagerDutyAuthType(str, Enum):
    """Authentication types supported by the PagerDuty connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PagerDutyResponse(BaseModel):
    """Standardized PagerDuty API response wrapper.

    The data field holds deserialized SDK response objects (dicts or lists).
    The SDK returns dicts for single-resource calls (rget/rpost/rput) and
    lists for collection calls (list_all).
    """

    success: bool = Field(
        ..., description="Whether the request was successful"
    )
    data: dict[str, object] | list[object] | None = Field(
        default=None,
        description="Response data from the PagerDuty SDK",
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
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK wrapper classes
# ---------------------------------------------------------------------------


class PagerDutyClientViaOAuth:
    """PagerDuty SDK wrapper using OAuth 2.0 Bearer token.

    Creates a ``RestApiV2Client`` authenticated with a Bearer access token.
    Stores client_id / client_secret for potential token-refresh flows
    handled at a higher layer.

    Args:
        access_token: The OAuth access token.
        client_id: OAuth client ID (used for token refresh externally).
        client_secret: OAuth client secret (used for token refresh externally).
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self._sdk: RestApiV2Client = RestApiV2Client(  # type: ignore[reportUnknownVariableType]
            api_key=access_token,
            auth_type="bearer",
        )  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> RestApiV2Client:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``RestApiV2Client`` SDK instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


class PagerDutyClientViaToken:
    """PagerDuty SDK wrapper using an API token.

    API tokens use ``Token token=<value>`` authentication. The SDK handles
    this when ``auth_type='token'`` is specified.

    Args:
        token: The PagerDuty API token.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._sdk: RestApiV2Client = RestApiV2Client(  # type: ignore[reportUnknownVariableType]
            api_key=token,
            auth_type="token",
        )  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> RestApiV2Client:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``RestApiV2Client`` SDK instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class PagerDutyOAuthConfig(BaseModel):
    """Configuration for PagerDuty client via OAuth 2.0.

    Args:
        access_token: The OAuth access token.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> PagerDutyClientViaOAuth:
        return PagerDutyClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


class PagerDutyTokenConfig(BaseModel):
    """Configuration for PagerDuty client via API Token.

    Args:
        token: The PagerDuty API token.
    """

    token: str

    def create_client(self) -> PagerDutyClientViaToken:
        return PagerDutyClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class PagerDutyAuthConfig(BaseModel):
    """Auth section of the PagerDuty connector configuration from etcd."""

    authType: PagerDutyAuthType = PagerDutyAuthType.TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class PagerDutyCredentialsConfig(BaseModel):
    """Credentials section of the PagerDuty connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class PagerDutyConnectorConfig(BaseModel):
    """Top-level PagerDuty connector configuration from etcd."""

    auth: PagerDutyAuthConfig = Field(
        default_factory=PagerDutyAuthConfig
    )
    credentials: PagerDutyCredentialsConfig = Field(
        default_factory=PagerDutyCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class PagerDutyClient(IClient):
    """Builder class for PagerDuty clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow (Bearer token)
    - API Token authentication (Token token=<value>)
    """

    def __init__(
        self,
        client: PagerDutyClientViaOAuth | PagerDutyClientViaToken,
    ) -> None:
        """Initialize with a PagerDuty SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> PagerDutyClientViaOAuth | PagerDutyClientViaToken:
        """Return the PagerDuty SDK wrapper."""
        return self.client

    def get_sdk(self) -> RestApiV2Client:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``RestApiV2Client`` SDK instance."""
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]

    @classmethod
    def build_with_config(
        cls,
        config: PagerDutyOAuthConfig | PagerDutyTokenConfig,
    ) -> "PagerDutyClient":
        """Build PagerDutyClient with configuration.

        Args:
            config: PagerDutyOAuthConfig or PagerDutyTokenConfig instance

        Returns:
            PagerDutyClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PagerDutyClient":
        """Build PagerDutyClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 Bearer token
        2. TOKEN: PagerDuty API token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            PagerDutyClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get PagerDuty connector configuration"
                )

            connector_config = PagerDutyConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == PagerDutyAuthType.OAUTH:
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
                            "/services/oauth/pagerduty", default=[]
                        )
                        oauth_configs: list[Any] = (
                            cast(list[Any], oauth_configs_raw)
                            if isinstance(oauth_configs_raw, list)
                            else []
                        )
                        for cfg in oauth_configs:
                            c: dict[str, Any] = cast(
                                dict[str, Any], cfg
                            )
                            if c.get("_id") == oauth_config_id:
                                shared: dict[str, Any] = cast(
                                    dict[str, Any],
                                    c.get("config", {}),
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

                oauth_cfg = PagerDutyOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            else:
                # Default: TOKEN
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = PagerDutyTokenConfig(token=token)
                return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build PagerDuty client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "PagerDutyClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            PagerDutyClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("auth", {}) or {},
            )
            auth_type = auth_config.get("authType", "TOKEN")

            if auth_type == "OAUTH":
                access_token = str(
                    credentials.get("access_token", "")
                )
                if not access_token:
                    raise ValueError(
                        "Access token not found in toolset config"
                    )

                client_id = str(auth_config.get("clientId", ""))
                client_secret = str(
                    auth_config.get("clientSecret", "")
                )

                # Try shared OAuth config
                oauth_config_id: str | None = cast(
                    str | None, auth_config.get("oauthConfigId")
                )
                if oauth_config_id and config_service and not (
                    client_id and client_secret
                ):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/pagerduty", default=[]
                        )
                        oauth_configs: list[Any] = (
                            cast(list[Any], oauth_configs_raw)
                            if isinstance(oauth_configs_raw, list)
                            else []
                        )
                        for cfg in oauth_configs:
                            c: dict[str, Any] = cast(
                                dict[str, Any], cfg
                            )
                            if c.get("_id") == oauth_config_id:
                                shared: dict[str, Any] = cast(
                                    dict[str, Any],
                                    c.get("config", {}),
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

                oauth_cfg = PagerDutyOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            else:
                # Default: TOKEN
                token = str(
                    credentials.get("token", "")
                    or credentials.get("api_token", "")
                    or auth_config.get("apiToken", "")
                    or auth_config.get("token", "")
                )
                if not token:
                    raise ValueError(
                        "Token not found in toolset config"
                    )
                token_config = PagerDutyTokenConfig(token=token)
                return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build PagerDuty client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for PagerDuty."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get PagerDuty connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get PagerDuty connector config: {e}"
            )
            raise ValueError(
                f"Failed to get PagerDuty connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
