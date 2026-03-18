"""Harvest client implementation.

This module provides clients for interacting with the Harvest API using either:
1. OAuth 2.0 authorization code flow
2. Personal Access Token (Bearer token)

Harvest requires a Harvest-Account-Id header on all API requests.

Authentication Reference: https://help.getharvest.com/api-v2/authentication-api/authentication/authentication/
OAuth Reference: https://id.getharvest.com/oauth2/authorize
API Reference: https://help.getharvest.com/api-v2/
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


class HarvestAuthType(str, Enum):
    """Authentication types supported by the Harvest connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class HarvestResponse(BaseModel):
    """Standardized Harvest API response wrapper.

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


class HarvestRESTClientViaOAuth(HTTPClient):
    """Harvest REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    The Harvest-Account-Id header is required on all requests.

    Args:
        access_token: The OAuth access token
        account_id: Harvest account ID (required for all API requests)
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (default: https://api.harvestapp.com/v2)
    """

    def __init__(
        self,
        access_token: str,
        account_id: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str = "https://api.harvestapp.com/v2",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Harvest-Account-Id"] = account_id
        self.headers["Content-Type"] = "application/json"
        self.headers["User-Agent"] = "PipesHub-Harvest-Connector"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class HarvestRESTClientViaToken(HTTPClient):
    """Harvest REST client via Personal Access Token.

    Personal access tokens are passed as Bearer tokens in the
    Authorization header. The Harvest-Account-Id header is required
    on all requests.

    Args:
        token: The personal access token
        account_id: Harvest account ID (required for all API requests)
        base_url: API base URL (default: https://api.harvestapp.com/v2)
    """

    def __init__(
        self,
        token: str,
        account_id: str,
        base_url: str = "https://api.harvestapp.com/v2",
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = base_url
        self.headers["Harvest-Account-Id"] = account_id
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class HarvestOAuthConfig(BaseModel):
    """Configuration for Harvest client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        account_id: Harvest account ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.harvestapp.com/v2)
    """

    access_token: str
    account_id: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = "https://api.harvestapp.com/v2"

    def create_client(self) -> HarvestRESTClientViaOAuth:
        return HarvestRESTClientViaOAuth(
            self.access_token,
            self.account_id,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class HarvestTokenConfig(BaseModel):
    """Configuration for Harvest client via Personal Access Token.

    Args:
        token: The personal access token
        account_id: Harvest account ID
        base_url: API base URL (default: https://api.harvestapp.com/v2)
    """

    token: str
    account_id: str
    base_url: str = "https://api.harvestapp.com/v2"

    def create_client(self) -> HarvestRESTClientViaToken:
        return HarvestRESTClientViaToken(
            self.token, self.account_id, self.base_url
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class HarvestAuthConfig(BaseModel):
    """Auth section of the Harvest connector configuration from etcd."""

    authType: HarvestAuthType = HarvestAuthType.OAUTH
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None
    accountId: str | None = None

    class Config:
        extra = "allow"


class HarvestCredentialsConfig(BaseModel):
    """Credentials section of the Harvest connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class HarvestConnectorConfig(BaseModel):
    """Top-level Harvest connector configuration from etcd."""

    auth: HarvestAuthConfig = Field(default_factory=HarvestAuthConfig)
    credentials: HarvestCredentialsConfig = Field(
        default_factory=HarvestCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Shared OAuth configuration models
# ---------------------------------------------------------------------------


class HarvestSharedOAuthConfigEntry(BaseModel):
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


class HarvestSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: HarvestSharedOAuthConfigEntry = Field(
        default_factory=HarvestSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class HarvestClient(IClient):
    """Builder class for Harvest clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Personal Access Token (Bearer token)

    All requests require a Harvest-Account-Id header.
    """

    def __init__(
        self,
        client: HarvestRESTClientViaOAuth | HarvestRESTClientViaToken,
    ) -> None:
        """Initialize with a Harvest client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> HarvestRESTClientViaOAuth | HarvestRESTClientViaToken:
        """Return the Harvest client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: HarvestOAuthConfig | HarvestTokenConfig,
    ) -> "HarvestClient":
        """Build HarvestClient with configuration.

        Args:
            config: HarvestOAuthConfig or HarvestTokenConfig instance

        Returns:
            HarvestClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "HarvestClient":
        """Build HarvestClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Personal Access Token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            HarvestClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Harvest connector configuration"
                )

            connector_config = HarvestConnectorConfig.model_validate(
                raw_config
            )
            account_id = connector_config.auth.accountId or ""

            if connector_config.auth.authType == HarvestAuthType.OAUTH:
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
                if not account_id:
                    raise ValueError(
                        "Account ID required for Harvest API requests"
                    )

                oauth_cfg = HarvestOAuthConfig(
                    access_token=access_token,
                    account_id=account_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == HarvestAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )
                if not account_id:
                    raise ValueError(
                        "Account ID required for Harvest API requests"
                    )

                token_config = HarvestTokenConfig(
                    token=token, account_id=account_id
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Harvest client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "HarvestClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            HarvestClient instance
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
                raise ValueError(
                    "Access token not found in toolset config"
                )

            account_id: str = str(auth_config.get("accountId", ""))
            if not account_id:
                raise ValueError(
                    "Account ID not found in toolset config"
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

            oauth_cfg = HarvestOAuthConfig(
                access_token=access_token,
                account_id=account_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Harvest client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> HarvestSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched HarvestSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/harvest", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = HarvestSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Harvest."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Harvest connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Harvest connector config: {e}")
            raise ValueError(
                f"Failed to get Harvest connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
