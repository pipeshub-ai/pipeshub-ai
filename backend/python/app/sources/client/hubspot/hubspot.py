"""HubSpot client implementation using the official hubspot-api-client SDK.

This module provides clients for interacting with the HubSpot API using either:
1. OAuth 2.0 authorization code flow
2. Private App Access Token (Bearer token)

The underlying SDK is ``hubspot-api-client`` (PyPI).  All API access is routed
through the ``HubSpot`` object which is created via
``HubSpot(access_token=token)``.

Authentication Reference: https://developers.hubspot.com/docs/api/working-with-oauth
API Reference: https://developers.hubspot.com/docs/api/overview
"""

import logging
from enum import Enum
from typing import Any, cast

from hubspot import HubSpot as HubSpotSDK  # type: ignore[import-untyped]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class HubSpotAuthType(str, Enum):
    """Authentication types supported by the HubSpot connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class HubSpotResponse(BaseModel):
    """Standardized HubSpot API response wrapper.

    The data field holds deserialized SDK response objects (dicts, lists,
    or any SDK model that has been converted to a plain structure).
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None, description="Response data from the HubSpot SDK"
    )
    error: str | None = Field(default=None, description="Error message if failed")
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


class HubSpotClientViaOAuth:
    """HubSpot SDK wrapper using OAuth 2.0 access token.

    Creates a ``HubSpot`` SDK instance authenticated with an OAuth access
    token.  Stores client_id / client_secret for potential token-refresh
    flows handled at a higher layer.

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
        self._sdk: HubSpotSDK = HubSpotSDK(access_token=access_token)  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> HubSpotSDK:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``HubSpot`` SDK instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


class HubSpotClientViaToken:
    """HubSpot SDK wrapper using a Private App access token.

    Private-app tokens do not expire and do not need refresh.

    Args:
        token: The Private App access token.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._sdk: HubSpotSDK = HubSpotSDK(access_token=token)  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> HubSpotSDK:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``HubSpot`` SDK instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class HubSpotOAuthConfig(BaseModel):
    """Configuration for HubSpot client via OAuth 2.0.

    Args:
        access_token: The OAuth access token.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> HubSpotClientViaOAuth:
        return HubSpotClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


class HubSpotTokenConfig(BaseModel):
    """Configuration for HubSpot client via Private App Access Token.

    Args:
        token: The Private App access token.
    """

    token: str

    def create_client(self) -> HubSpotClientViaToken:
        return HubSpotClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class HubSpotAuthConfig(BaseModel):
    """Auth section of the HubSpot connector configuration from etcd."""

    authType: HubSpotAuthType = HubSpotAuthType.TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class HubSpotCredentialsConfig(BaseModel):
    """Credentials section of the HubSpot connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class HubSpotConnectorConfig(BaseModel):
    """Top-level HubSpot connector configuration from etcd."""

    auth: HubSpotAuthConfig = Field(default_factory=HubSpotAuthConfig)
    credentials: HubSpotCredentialsConfig = Field(
        default_factory=HubSpotCredentialsConfig
    )

    class Config:
        extra = "allow"


class HubSpotSharedOAuthConfigEntry(BaseModel):
    """A single entry from the shared OAuth config list in etcd.

    Handles both camelCase and snake_case key variants from the config store.
    """

    entry_id: str | None = Field(default=None, alias="_id")
    clientId: str | None = None
    client_id: str | None = None
    clientSecret: str | None = None
    client_secret: str | None = None
    redirectUri: str | None = None
    redirect_uri: str | None = None

    class Config:
        extra = "allow"
        populate_by_name = True

    def resolved_client_id(self, fallback: str = "") -> str:
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        return self.clientSecret or self.client_secret or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        return self.redirectUri or self.redirect_uri or fallback


class HubSpotSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: HubSpotSharedOAuthConfigEntry = Field(
        default_factory=HubSpotSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class HubSpotClient(IClient):
    """Builder class for HubSpot clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Private App Access Token (Bearer token)
    """

    def __init__(
        self,
        client: HubSpotClientViaOAuth | HubSpotClientViaToken,
    ) -> None:
        """Initialize with a HubSpot SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> HubSpotClientViaOAuth | HubSpotClientViaToken:
        """Return the HubSpot SDK wrapper."""
        return self.client

    def get_sdk(self) -> HubSpotSDK:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``HubSpot`` SDK instance."""
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]

    @classmethod
    def build_with_config(
        cls,
        config: HubSpotOAuthConfig | HubSpotTokenConfig,
    ) -> "HubSpotClient":
        """Build HubSpotClient with configuration.

        Args:
            config: HubSpotOAuthConfig or HubSpotTokenConfig instance

        Returns:
            HubSpotClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "HubSpotClient":
        """Build HubSpotClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Private App access token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            HubSpotClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get HubSpot connector configuration")

            connector_config = HubSpotConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == HubSpotAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    shared_cfg = await cls._find_shared_oauth_config(
                        config_service, oauth_config_id, logger
                    )
                    if shared_cfg:
                        client_id = shared_cfg.resolved_client_id(client_id)
                        client_secret = shared_cfg.resolved_client_secret(
                            client_secret
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_cfg = HubSpotOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == HubSpotAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = HubSpotTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build HubSpot client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "HubSpotClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            HubSpotClient instance
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
                raise ValueError("Access token not found in toolset config")

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    client_id = shared_cfg.resolved_client_id(client_id)
                    client_secret = shared_cfg.resolved_client_secret(
                        client_secret
                    )

            oauth_cfg = HubSpotOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build HubSpot client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> HubSpotSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched HubSpotSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/hubspot", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = HubSpotSharedOAuthWrapper.model_validate(entry)
                if wrapper.entry_id == oauth_config_id:
                    return wrapper.config
        except Exception as e:
            logger.warning(f"Failed to fetch shared OAuth config: {e}")
        return None

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for HubSpot."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get HubSpot connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get HubSpot connector config: {e}")
            raise ValueError(
                f"Failed to get HubSpot connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
