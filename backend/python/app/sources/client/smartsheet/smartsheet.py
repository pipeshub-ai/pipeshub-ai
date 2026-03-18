# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Smartsheet client implementation using the official smartsheet-python-sdk.

This module provides clients for interacting with the Smartsheet API using either:
1. OAuth 2.0 authorization code flow
2. API Access Token

SDK Reference: https://github.com/smartsheet/smartsheet-python-sdk
API Reference: https://smartsheet.redoc.ly/
"""

import logging
from typing import cast

import smartsheet as smartsheet_sdk  # type: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class SmartsheetResponse(BaseModel):
    """Standardized Smartsheet API response wrapper.

    Wraps SDK responses into a uniform shape for the data-source layer.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: object = Field(
        default=None, description="Response data from the SDK"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        arbitrary_types_allowed = True
        extra = "allow"

    def to_dict(self) -> dict[str, object]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK client classes
# ---------------------------------------------------------------------------


class SmartsheetClientViaOAuth:
    """Smartsheet SDK client via OAuth 2.0 authorization code flow.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
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
        self._sdk: smartsheet_sdk.Smartsheet | None = None  # type: ignore[reportUnknownMemberType]

    def create_client(self) -> "smartsheet_sdk.Smartsheet":  # type: ignore[reportUnknownMemberType]
        """Create and return the SDK client."""
        self._sdk = smartsheet_sdk.Smartsheet(self.access_token)  # type: ignore[reportUnknownMemberType]
        self._sdk.errors_as_exceptions(True)  # type: ignore[reportUnknownMemberType]
        return self._sdk  # type: ignore[reportReturnType]

    def get_sdk(self) -> "smartsheet_sdk.Smartsheet":  # type: ignore[reportUnknownMemberType]
        """Get the SDK client, creating it if necessary."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk  # type: ignore[reportReturnType]


class SmartsheetClientViaToken:
    """Smartsheet SDK client via API Access Token.

    Args:
        token: The API access token
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._sdk: smartsheet_sdk.Smartsheet | None = None  # type: ignore[reportUnknownMemberType]

    def create_client(self) -> "smartsheet_sdk.Smartsheet":  # type: ignore[reportUnknownMemberType]
        """Create and return the SDK client."""
        self._sdk = smartsheet_sdk.Smartsheet(self.token)  # type: ignore[reportUnknownMemberType]
        self._sdk.errors_as_exceptions(True)  # type: ignore[reportUnknownMemberType]
        return self._sdk  # type: ignore[reportReturnType]

    def get_sdk(self) -> "smartsheet_sdk.Smartsheet":  # type: ignore[reportUnknownMemberType]
        """Get the SDK client, creating it if necessary."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk  # type: ignore[reportReturnType]


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class SmartsheetOAuthConfig(BaseModel):
    """Configuration for Smartsheet client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> SmartsheetClientViaOAuth:
        """Create an OAuth SDK client from this config."""
        return SmartsheetClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


class SmartsheetTokenConfig(BaseModel):
    """Configuration for Smartsheet client via API Access Token.

    Args:
        token: The API access token
    """

    token: str

    def create_client(self) -> SmartsheetClientViaToken:
        """Create a token SDK client from this config."""
        return SmartsheetClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class SmartsheetAuthConfig(BaseModel):
    """Auth section of the Smartsheet connector configuration from etcd."""

    authType: str = "TOKEN"
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class SmartsheetCredentialsConfig(BaseModel):
    """Credentials section of the Smartsheet connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class SmartsheetConnectorConfig(BaseModel):
    """Top-level Smartsheet connector configuration from etcd."""

    auth: SmartsheetAuthConfig = Field(default_factory=SmartsheetAuthConfig)
    credentials: SmartsheetCredentialsConfig = Field(
        default_factory=SmartsheetCredentialsConfig
    )

    class Config:
        extra = "allow"


class SmartsheetSharedOAuthConfigEntry(BaseModel):
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
        """Return resolved client_id preferring camelCase."""
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        """Return resolved client_secret preferring camelCase."""
        return self.clientSecret or self.client_secret or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        """Return resolved redirect_uri preferring camelCase."""
        return self.redirectUri or self.redirect_uri or fallback


class SmartsheetSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: SmartsheetSharedOAuthConfigEntry = Field(
        default_factory=SmartsheetSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class SmartsheetClient(IClient):
    """Builder class for Smartsheet SDK clients with different auth methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - API Access Token
    """

    def __init__(
        self,
        client: SmartsheetClientViaOAuth | SmartsheetClientViaToken,
    ) -> None:
        """Initialize with a Smartsheet SDK client wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> SmartsheetClientViaOAuth | SmartsheetClientViaToken:
        """Return the Smartsheet client wrapper."""
        return self.client

    def get_sdk(self) -> "smartsheet_sdk.Smartsheet":  # type: ignore[reportUnknownMemberType]
        """Return the underlying smartsheet SDK instance."""
        return self.client.get_sdk()  # type: ignore[reportReturnType]

    @classmethod
    def build_with_config(
        cls,
        config: SmartsheetOAuthConfig | SmartsheetTokenConfig,
    ) -> "SmartsheetClient":
        """Build SmartsheetClient with configuration.

        Args:
            config: SmartsheetOAuthConfig or SmartsheetTokenConfig instance

        Returns:
            SmartsheetClient instance
        """
        client = config.create_client()
        client.get_sdk()  # eagerly initialize the SDK
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "SmartsheetClient":
        """Build SmartsheetClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Pre-generated API access token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            SmartsheetClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Smartsheet connector configuration"
                )

            connector_config = SmartsheetConnectorConfig.model_validate(
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

                oauth_cfg = SmartsheetOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                wrapper = oauth_cfg.create_client()
                wrapper.get_sdk()
                return cls(wrapper)

            if connector_config.auth.authType == "TOKEN":
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "API token required for TOKEN auth type"
                    )

                token_config = SmartsheetTokenConfig(token=token)
                wrapper = token_config.create_client()
                wrapper.get_sdk()
                return cls(wrapper)

            raise ValueError(
                f"Invalid auth type: {connector_config.auth.authType}"
            )

        except Exception as e:
            logger.error(
                f"Failed to build Smartsheet client from services: {e!s}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, object],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "SmartsheetClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            SmartsheetClient instance
        """
        try:
            credentials: dict[str, object] = cast(
                dict[str, object], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, object] = cast(
                dict[str, object], toolset_config.get("auth", {}) or {}
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
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    client_id = shared_cfg.resolved_client_id(client_id)
                    client_secret = shared_cfg.resolved_client_secret(
                        client_secret
                    )

            oauth_cfg = SmartsheetOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            wrapper = oauth_cfg.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build Smartsheet client from toolset: {e!s}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> SmartsheetSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched SmartsheetSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/smartsheet", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = SmartsheetSharedOAuthWrapper.model_validate(entry)
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
    ) -> dict[str, object]:
        """Fetch connector config from etcd for Smartsheet.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            Configuration dictionary
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Smartsheet connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, object], raw)
        except Exception as e:
            logger.error(f"Failed to get Smartsheet connector config: {e}")
            raise ValueError(
                f"Failed to get Smartsheet connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
