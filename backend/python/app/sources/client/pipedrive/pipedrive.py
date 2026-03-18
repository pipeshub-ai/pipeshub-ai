"""Pipedrive client implementation.

This module provides clients for interacting with the Pipedrive API using either:
1. OAuth 2.0 authorization code flow
2. API Token authentication (Bearer token)

Pipedrive supports both authentication methods. OAuth is recommended for
third-party integrations, while API tokens are suitable for personal use.

Authentication Reference: https://pipedrive.readme.io/docs/core-api-concepts-authentication
OAuth Reference: https://pipedrive.readme.io/docs/marketplace-oauth-authorization
API Reference: https://developers.pipedrive.com/docs/api/v1
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


class PipedriveAuthType(str, Enum):
    """Authentication types supported by the Pipedrive connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class PipedriveResponse(BaseModel):
    """Standardized Pipedrive API response wrapper.

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


class PipedriveRESTClientViaOAuth(HTTPClient):
    """Pipedrive REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Supports token refresh via client_id and client_secret.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (default: https://api.pipedrive.com/v1)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://api.pipedrive.com/v1",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class PipedriveRESTClientViaToken(HTTPClient):
    """Pipedrive REST client via API Token.

    API tokens are passed as Bearer tokens in the Authorization header.

    Args:
        token: The Pipedrive API token
        base_url: API base URL (default: https://api.pipedrive.com/v1)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.pipedrive.com/v1",
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


class PipedriveOAuthConfig(BaseModel):
    """Configuration for Pipedrive client via OAuth 2.0 authorization code flow.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.pipedrive.com/v1)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = "https://api.pipedrive.com/v1"

    def create_client(self) -> PipedriveRESTClientViaOAuth:
        return PipedriveRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class PipedriveTokenConfig(BaseModel):
    """Configuration for Pipedrive client via API Token.

    Args:
        token: The Pipedrive API token
        base_url: API base URL (default: https://api.pipedrive.com/v1)
    """

    token: str
    base_url: str = "https://api.pipedrive.com/v1"

    def create_client(self) -> PipedriveRESTClientViaToken:
        return PipedriveRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class PipedriveAuthConfig(BaseModel):
    """Auth section of the Pipedrive connector configuration from etcd."""

    authType: PipedriveAuthType = PipedriveAuthType.OAUTH
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class PipedriveCredentialsConfig(BaseModel):
    """Credentials section of the Pipedrive connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class PipedriveConnectorConfig(BaseModel):
    """Top-level Pipedrive connector configuration from etcd."""

    auth: PipedriveAuthConfig = Field(default_factory=PipedriveAuthConfig)
    credentials: PipedriveCredentialsConfig = Field(
        default_factory=PipedriveCredentialsConfig
    )

    class Config:
        extra = "allow"


class PipedriveSharedOAuthConfigEntry(BaseModel):
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


class PipedriveSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: PipedriveSharedOAuthConfigEntry = Field(
        default_factory=PipedriveSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class PipedriveClient(IClient):
    """Builder class for Pipedrive clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - API Token authentication
    """

    def __init__(
        self,
        client: PipedriveRESTClientViaOAuth | PipedriveRESTClientViaToken,
    ) -> None:
        """Initialize with a Pipedrive client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> PipedriveRESTClientViaOAuth | PipedriveRESTClientViaToken:
        """Return the Pipedrive client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: PipedriveOAuthConfig | PipedriveTokenConfig,
    ) -> "PipedriveClient":
        """Build PipedriveClient with configuration.

        Args:
            config: PipedriveOAuthConfig or PipedriveTokenConfig instance

        Returns:
            PipedriveClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PipedriveClient":
        """Build PipedriveClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: API Token authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            PipedriveClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Pipedrive connector configuration"
                )

            connector_config = PipedriveConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == PipedriveAuthType.OAUTH:
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

                oauth_cfg = PipedriveOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == PipedriveAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "API token required for TOKEN auth type"
                    )

                token_config = PipedriveTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Pipedrive client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "PipedriveClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            PipedriveClient instance
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

            oauth_cfg = PipedriveOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Pipedrive client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> PipedriveSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched PipedriveSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/pipedrive", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = PipedriveSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Pipedrive."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Pipedrive connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Pipedrive connector config: {e}")
            raise ValueError(
                f"Failed to get Pipedrive connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
