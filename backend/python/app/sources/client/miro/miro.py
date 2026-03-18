# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Miro client implementation.

This module provides clients for interacting with the Miro API using the
official ``miro_api`` Python SDK instead of raw HTTP requests.

Supported authentication strategies:
1. OAuth 2.0 authorization code flow (access token + optional client credentials)
2. Pre-generated access token

SDK Reference: https://miroapp.github.io/api-clients/python/
"""

import logging
from enum import Enum
from typing import Any, cast

from miro_api import MiroApi  # type: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MiroAuthType(str, Enum):
    """Authentication types supported by the Miro connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class MiroResponse(BaseModel):
    """Standardized Miro API response wrapper.

    Wraps SDK return values into a uniform success/error envelope so that
    callers never need to handle raw SDK types directly.
    """

    success: bool = Field(
        ..., description="Whether the request was successful"
    )
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None,
        description="Response data from the SDK",
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


class MiroClientViaOAuth:
    """Miro SDK client via OAuth 2.0 authorization code flow.

    Wraps ``MiroApi`` from the official ``miro_api`` package.
    The *access_token* is the only credential the SDK needs at runtime;
    *client_id* / *client_secret* are kept for upstream token-refresh logic.

    Args:
        access_token: The OAuth access token.
        client_id: OAuth client ID (retained for refresh flows).
        client_secret: OAuth client secret (retained for refresh flows).
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
        self._sdk: MiroApi = MiroApi(access_token)  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> MiroApi:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``MiroApi`` instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


class MiroClientViaToken:
    """Miro SDK client via a pre-generated access token.

    Args:
        token: The pre-generated access token.
    """

    def __init__(self, token: str) -> None:
        self.token = token
        self._sdk: MiroApi = MiroApi(token)  # type: ignore[reportInvalidTypeForm]

    def get_sdk(self) -> MiroApi:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``MiroApi`` instance."""
        return self._sdk  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class MiroOAuthConfig(BaseModel):
    """Configuration for Miro client via OAuth 2.0 authorization code flow.

    Args:
        access_token: The OAuth access token.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> MiroClientViaOAuth:
        """Create and return a ``MiroClientViaOAuth`` instance."""
        return MiroClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


class MiroTokenConfig(BaseModel):
    """Configuration for Miro client via pre-generated access token.

    Args:
        token: The pre-generated access token.
    """

    token: str

    def create_client(self) -> MiroClientViaToken:
        """Create and return a ``MiroClientViaToken`` instance."""
        return MiroClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class MiroAuthConfig(BaseModel):
    """Auth section of the Miro connector configuration from etcd."""

    authType: MiroAuthType = MiroAuthType.OAUTH
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class MiroCredentialsConfig(BaseModel):
    """Credentials section of the Miro connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class MiroConnectorConfig(BaseModel):
    """Top-level Miro connector configuration from etcd."""

    auth: MiroAuthConfig = Field(default_factory=MiroAuthConfig)
    credentials: MiroCredentialsConfig = Field(
        default_factory=MiroCredentialsConfig
    )

    class Config:
        extra = "allow"


class MiroSharedOAuthConfigEntry(BaseModel):
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
        """Return the best available client ID."""
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        """Return the best available client secret."""
        return self.clientSecret or self.client_secret or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        """Return the best available redirect URI."""
        return self.redirectUri or self.redirect_uri or fallback


class MiroSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: MiroSharedOAuthConfigEntry = Field(
        default_factory=MiroSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class MiroClient(IClient):
    """Builder class for Miro clients with different authentication methods.

    Wraps either ``MiroClientViaOAuth`` or ``MiroClientViaToken`` and
    exposes the underlying ``MiroApi`` SDK via ``get_sdk()``.
    """

    def __init__(
        self,
        client: MiroClientViaOAuth | MiroClientViaToken,
    ) -> None:
        """Initialize with a Miro SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> MiroClientViaOAuth | MiroClientViaToken:
        """Return the Miro SDK wrapper."""
        return self.client

    def get_sdk(self) -> MiroApi:  # type: ignore[reportInvalidTypeForm]
        """Return the underlying ``MiroApi`` SDK instance."""
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]

    @classmethod
    def build_with_config(
        cls,
        config: MiroOAuthConfig | MiroTokenConfig,
    ) -> "MiroClient":
        """Build MiroClient with configuration.

        Args:
            config: MiroOAuthConfig or MiroTokenConfig instance.

        Returns:
            MiroClient instance.
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "MiroClient":
        """Build MiroClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Pre-generated access token

        Args:
            logger: Logger instance.
            config_service: Configuration service instance.
            connector_instance_id: Optional connector instance ID.

        Returns:
            MiroClient instance.
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Miro connector configuration"
                )

            connector_config = MiroConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == MiroAuthType.OAUTH:
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
                        client_id = shared_cfg.resolved_client_id(
                            client_id
                        )
                        client_secret = (
                            shared_cfg.resolved_client_secret(
                                client_secret
                            )
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_cfg = MiroOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == MiroAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = MiroTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                "Failed to build Miro client from services: %s", str(e)
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "MiroClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict.
            logger: Logger instance.
            config_service: Optional configuration service for shared
                OAuth config.

        Returns:
            MiroClient instance.
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

            access_token: str = str(
                credentials.get("access_token", "")
            )
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(
                auth_config.get("clientSecret", "")
            )

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if (
                oauth_config_id
                and config_service
                and not (client_id and client_secret)
            ):
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    client_id = shared_cfg.resolved_client_id(client_id)
                    client_secret = shared_cfg.resolved_client_secret(
                        client_secret
                    )

            oauth_cfg = MiroOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                "Failed to build Miro client from toolset: %s", str(e)
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> MiroSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance.
            oauth_config_id: The shared OAuth config ID to match.
            logger: Logger instance.

        Returns:
            Matched MiroSharedOAuthConfigEntry or None.
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/miro", default=[]
            )
            entries: list[object] = (
                list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            )
            for entry in entries:
                wrapper = MiroSharedOAuthWrapper.model_validate(entry)
                if wrapper.entry_id == oauth_config_id:
                    return wrapper.config
        except Exception as e:
            logger.warning("Failed to fetch shared OAuth config: %s", e)
        return None

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Miro."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Miro connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                "Failed to get Miro connector config: %s", e
            )
            raise ValueError(
                f"Failed to get Miro connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
