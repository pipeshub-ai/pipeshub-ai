"""Coda client implementation.

This module provides clients for interacting with the Coda API using either:
1. OAuth 2.0 authorization code flow
2. Pre-generated API Token (Bearer token)

Authentication Reference: https://coda.io/developers/apis/v1#section/Authentication
OAuth Reference: https://coda.io/developers/apis/v1#section/OAuth
API Reference: https://coda.io/developers/apis/v1
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


class CodaAuthType(str, Enum):
    """Authentication types supported by the Coda connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class CodaResponse(BaseModel):
    """Standardized Coda API response wrapper.

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


class CodaRESTClientViaOAuth(HTTPClient):
    """Coda REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Supports token refresh via client_id and client_secret.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (default: https://coda.io/apis/v1)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://coda.io/apis/v1",
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


class CodaRESTClientViaToken(HTTPClient):
    """Coda REST client via pre-generated API Token.

    API tokens are passed as Bearer tokens in the Authorization header.

    Args:
        token: The pre-generated API token
        base_url: API base URL (default: https://coda.io/apis/v1)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://coda.io/apis/v1",
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


class CodaOAuthConfig(BaseModel):
    """Configuration for Coda client via OAuth 2.0 authorization code flow.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://coda.io/apis/v1)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = "https://coda.io/apis/v1"

    def create_client(self) -> CodaRESTClientViaOAuth:
        return CodaRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class CodaTokenConfig(BaseModel):
    """Configuration for Coda client via pre-generated API Token.

    Args:
        token: The pre-generated API token
        base_url: API base URL (default: https://coda.io/apis/v1)
    """

    token: str
    base_url: str = "https://coda.io/apis/v1"

    def create_client(self) -> CodaRESTClientViaToken:
        return CodaRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class CodaAuthConfig(BaseModel):
    """Auth section of the Coda connector configuration from etcd."""

    authType: CodaAuthType = CodaAuthType.TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class CodaCredentialsConfig(BaseModel):
    """Credentials section of the Coda connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class CodaConnectorConfig(BaseModel):
    """Top-level Coda connector configuration from etcd."""

    auth: CodaAuthConfig = Field(default_factory=CodaAuthConfig)
    credentials: CodaCredentialsConfig = Field(
        default_factory=CodaCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Shared OAuth configuration models
# ---------------------------------------------------------------------------


class CodaSharedOAuthConfigEntry(BaseModel):
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


class CodaSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: CodaSharedOAuthConfigEntry = Field(
        default_factory=CodaSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class CodaClient(IClient):
    """Builder class for Coda clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Pre-generated API Token (Bearer token)
    """

    def __init__(
        self,
        client: CodaRESTClientViaOAuth | CodaRESTClientViaToken,
    ) -> None:
        """Initialize with a Coda client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> CodaRESTClientViaOAuth | CodaRESTClientViaToken:
        """Return the Coda client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: CodaOAuthConfig | CodaTokenConfig,
    ) -> "CodaClient":
        """Build CodaClient with configuration.

        Args:
            config: CodaOAuthConfig or CodaTokenConfig instance

        Returns:
            CodaClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "CodaClient":
        """Build CodaClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Pre-generated API Token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            CodaClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Coda connector configuration")

            connector_config = CodaConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == CodaAuthType.OAUTH:
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

                oauth_cfg = CodaOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == CodaAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "API token required for TOKEN auth type"
                    )

                token_config = CodaTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Coda client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "CodaClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            CodaClient instance
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

            oauth_cfg = CodaOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Coda client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> CodaSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched CodaSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/coda", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = CodaSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Coda."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Coda connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Coda connector config: {e}")
            raise ValueError(
                f"Failed to get Coda connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
