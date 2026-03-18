"""Canva client implementation.

This module provides clients for interacting with the Canva Connect API using either:
1. OAuth 2.0 access token authentication (authorization code flow with PKCE)
2. Pre-generated Bearer token authentication

Canva Connect API uses OAuth 2.0 with PKCE for authorization. The API does not
require a client_secret; instead, PKCE code_verifier/code_challenge is used.

Authentication Reference: https://www.canva.dev/docs/connect/authentication/
API Reference: https://www.canva.dev/docs/connect/
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


class CanvaAuthType(str, Enum):
    """Authentication types supported by the Canva connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class CanvaResponse(BaseModel):
    """Standardized Canva API response wrapper.

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


class CanvaRESTClientViaOAuth(HTTPClient):
    """Canva REST client via OAuth 2.0 authorization code flow with PKCE.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Canva uses PKCE (no client_secret required), but client_id is sent
    in the POST body during token exchange.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        base_url: API base URL (default: https://api.canva.com/rest/v1)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        base_url: str = "https://api.canva.com/rest/v1",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.client_id = client_id
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class CanvaRESTClientViaToken(HTTPClient):
    """Canva REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.canva.com/rest/v1)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.canva.com/rest/v1",
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = base_url
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class CanvaOAuthConfig(BaseModel):
    """Configuration for Canva client via OAuth 2.0 (PKCE).

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        base_url: API base URL (default: https://api.canva.com/rest/v1)
    """

    access_token: str
    client_id: str | None = None
    base_url: str = "https://api.canva.com/rest/v1"

    def create_client(self) -> CanvaRESTClientViaOAuth:
        return CanvaRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.base_url,
        )


class CanvaTokenConfig(BaseModel):
    """Configuration for Canva client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.canva.com/rest/v1)
    """

    token: str
    base_url: str = "https://api.canva.com/rest/v1"

    def create_client(self) -> CanvaRESTClientViaToken:
        return CanvaRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class CanvaAuthConfig(BaseModel):
    """Auth section of the Canva connector configuration from etcd."""

    authType: CanvaAuthType = CanvaAuthType.OAUTH
    clientId: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class CanvaCredentialsConfig(BaseModel):
    """Credentials section of the Canva connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class CanvaConnectorConfig(BaseModel):
    """Top-level Canva connector configuration from etcd."""

    auth: CanvaAuthConfig = Field(default_factory=CanvaAuthConfig)
    credentials: CanvaCredentialsConfig = Field(
        default_factory=CanvaCredentialsConfig
    )

    class Config:
        extra = "allow"


class CanvaSharedOAuthConfigEntry(BaseModel):
    """A single entry from the shared OAuth config list in etcd.

    Handles both camelCase and snake_case key variants from the config store.
    """

    entry_id: str | None = Field(default=None, alias="_id")
    clientId: str | None = None
    client_id: str | None = None
    redirectUri: str | None = None
    redirect_uri: str | None = None

    class Config:
        extra = "allow"
        populate_by_name = True

    def resolved_client_id(self, fallback: str = "") -> str:
        return self.clientId or self.client_id or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        return self.redirectUri or self.redirect_uri or fallback


class CanvaSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: CanvaSharedOAuthConfigEntry = Field(
        default_factory=CanvaSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class CanvaClient(IClient):
    """Builder class for Canva clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow with PKCE
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: CanvaRESTClientViaOAuth | CanvaRESTClientViaToken,
    ) -> None:
        """Initialize with a Canva client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> CanvaRESTClientViaOAuth | CanvaRESTClientViaToken:
        """Return the Canva client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: CanvaOAuthConfig | CanvaTokenConfig,
    ) -> "CanvaClient":
        """Build CanvaClient with configuration.

        Args:
            config: CanvaOAuthConfig or CanvaTokenConfig instance

        Returns:
            CanvaClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "CanvaClient":
        """Build CanvaClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with PKCE
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            CanvaClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Canva connector configuration")

            connector_config = CanvaConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == CanvaAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not client_id:
                    shared_cfg = await cls._find_shared_oauth_config(
                        config_service, oauth_config_id, logger
                    )
                    if shared_cfg:
                        client_id = shared_cfg.resolved_client_id(client_id)

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_cfg = CanvaOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == CanvaAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = CanvaTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Canva client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "CanvaClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            CanvaClient instance
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

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not client_id:
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    client_id = shared_cfg.resolved_client_id(client_id)

            oauth_cfg = CanvaOAuthConfig(
                access_token=access_token,
                client_id=client_id,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Canva client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> CanvaSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched CanvaSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/canva", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = CanvaSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Canva."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Canva connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Canva connector config: {e}")
            raise ValueError(
                f"Failed to get Canva connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
