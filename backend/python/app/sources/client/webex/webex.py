"""Webex client implementation.

This module provides a client for interacting with the Webex API using the
official wxc_sdk package (WebexSimpleApi).

Authentication Reference: https://developer.webex.com/docs/getting-started
SDK Reference: https://github.com/jeokrohn/wxc_sdk

Supports:
1. Direct token authentication (pre-generated access token)
2. OAuth 2.0 access token authentication
"""

import logging
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

# ---------------------------------------------------------------------------
# Webex SDK import (untyped third-party package)
# ---------------------------------------------------------------------------
from wxc_sdk import WebexSimpleApi  # type: ignore[import-untyped]

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WebexAuthType(str, Enum):
    """Authentication types supported by the Webex connector."""

    TOKEN = "TOKEN"
    OAUTH = "OAUTH"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class WebexResponse(BaseModel):
    """Standardized Webex API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | None = Field(
        default=None, description="Response data"
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
# SDK client wrappers
# ---------------------------------------------------------------------------


class WebexClientViaToken:
    """Webex client via pre-generated access token.

    Wraps the wxc_sdk WebexSimpleApi with a direct access token.

    Args:
        access_token: Webex access token
    """

    def __init__(self, access_token: str) -> None:
        access_token = access_token.strip() if access_token else ""
        if not access_token:
            raise ValueError("Webex access token cannot be empty")

        self.access_token = access_token
        self._api: Any = None  # WebexSimpleApi

    def create_client(self) -> Any:  # WebexSimpleApi
        """Create and return the WebexSimpleApi instance.

        Returns:
            WebexSimpleApi instance
        """
        self._api = WebexSimpleApi(tokens=self.access_token)  # type: ignore[no-untyped-call]
        return self._api  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # WebexSimpleApi
        """Get the WebexSimpleApi instance, creating it if needed.

        Returns:
            WebexSimpleApi instance
        """
        if self._api is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._api  # type: ignore[reportUnknownVariableType]

    def get_token(self) -> str:
        """Get the access token."""
        return self.access_token


class WebexClientViaOAuth:
    """Webex client via OAuth 2.0 access token.

    Uses an OAuth-obtained access token to create the WebexSimpleApi instance.
    Supports token refresh via client_id and client_secret.

    Args:
        access_token: OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        redirect_uri: OAuth redirect URI
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        access_token = access_token.strip() if access_token else ""
        if not access_token:
            raise ValueError("Webex OAuth access token cannot be empty")

        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._api: Any = None  # WebexSimpleApi

    def create_client(self) -> Any:  # WebexSimpleApi
        """Create and return the WebexSimpleApi instance.

        Returns:
            WebexSimpleApi instance
        """
        self._api = WebexSimpleApi(tokens=self.access_token)  # type: ignore[no-untyped-call]
        return self._api  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # WebexSimpleApi
        """Get the WebexSimpleApi instance, creating it if needed.

        Returns:
            WebexSimpleApi instance
        """
        if self._api is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._api  # type: ignore[reportUnknownVariableType]

    def get_token(self) -> str:
        """Get the access token."""
        return self.access_token


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class WebexTokenConfig(BaseModel):
    """Configuration for Webex client via pre-generated access token.

    Args:
        access_token: Webex access token
    """

    access_token: str = Field(..., description="Webex access token")

    def create_client(self) -> WebexClientViaToken:
        """Create a Webex client."""
        return WebexClientViaToken(access_token=self.access_token)


class WebexOAuthConfig(BaseModel):
    """Configuration for Webex client via OAuth 2.0.

    Args:
        access_token: OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        redirect_uri: OAuth redirect URI
    """

    access_token: str = Field(..., description="OAuth access token")
    client_id: str | None = Field(default=None, description="OAuth client ID")
    client_secret: str | None = Field(
        default=None, description="OAuth client secret"
    )
    redirect_uri: str | None = Field(
        default=None, description="OAuth redirect URI"
    )

    def create_client(self) -> WebexClientViaOAuth:
        """Create a Webex OAuth client."""
        return WebexClientViaOAuth(
            access_token=self.access_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class WebexAuthConfig(BaseModel):
    """Auth section of the Webex connector configuration from etcd."""

    authType: WebexAuthType = WebexAuthType.TOKEN
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class WebexCredentialsConfig(BaseModel):
    """Credentials section of the Webex connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class WebexConnectorConfig(BaseModel):
    """Top-level Webex connector configuration from etcd."""

    auth: WebexAuthConfig = Field(default_factory=WebexAuthConfig)
    credentials: WebexCredentialsConfig = Field(
        default_factory=WebexCredentialsConfig
    )

    class Config:
        extra = "allow"


class WebexSharedOAuthConfigEntry(BaseModel):
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


class WebexSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: WebexSharedOAuthConfigEntry = Field(
        default_factory=WebexSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class WebexClient(IClient):
    """Builder class for Webex clients with different authentication methods.

    Supports:
    - Direct token authentication
    - OAuth 2.0 access token authentication
    """

    def __init__(
        self,
        client: WebexClientViaToken | WebexClientViaOAuth,
    ) -> None:
        """Initialize with a Webex client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> WebexClientViaToken | WebexClientViaOAuth:
        """Return the Webex client object."""
        return self.client

    def get_sdk(self) -> Any:  # WebexSimpleApi
        """Return the underlying WebexSimpleApi SDK instance."""
        return self.client.get_sdk()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]

    @classmethod
    def build_with_config(
        cls,
        config: WebexTokenConfig | WebexOAuthConfig,
    ) -> "WebexClient":
        """Build WebexClient with configuration.

        Args:
            config: WebexTokenConfig or WebexOAuthConfig instance

        Returns:
            WebexClient instance
        """
        client = config.create_client()
        _ = client.get_sdk()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]  # Eagerly initialize
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "WebexClient":
        """Build WebexClient using configuration service.

        Supports token and OAuth authentication strategies.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            WebexClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Webex connector configuration"
                )

            connector_config = WebexConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == WebexAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )
                webex_client = WebexClientViaToken(access_token=token)
                _ = webex_client.get_sdk()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]
                return cls(webex_client)

            elif connector_config.auth.authType == WebexAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""
                redirect_uri = connector_config.auth.redirectUri or ""

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
                        redirect_uri = shared_cfg.resolved_redirect_uri(
                            redirect_uri
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                webex_client = WebexClientViaOAuth(  # type: ignore[assignment]
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                _ = webex_client.get_sdk()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]
                return cls(webex_client)

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Webex client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> WebexSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched WebexSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/webex", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[arg-type]
            for entry in entries:
                wrapper = WebexSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Webex."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Webex connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Webex connector config: {e}")
            raise ValueError(
                f"Failed to get Webex connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
