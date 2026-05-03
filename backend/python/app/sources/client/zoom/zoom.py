"""Zoom client implementation.

This module provides clients for interacting with the Zoom API using either:
1. Server-to-Server OAuth (account_credentials grant)
2. OAuth 2.0 authorization code flow
3. Pre-generated Bearer token

Authentication Reference: https://developers.zoom.us/docs/internal-apps/s2s-oauth/
OAuth Reference: https://developers.zoom.us/docs/integrations/oauth/
API Reference: https://developers.zoom.us/docs/api/
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast, override

from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ZoomAuthType(str, Enum):
    """Authentication types supported by the Zoom connector."""

    SERVER_TO_SERVER = "SERVER_TO_SERVER"
    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ZoomResponse(BaseModel):
    """Standardized Zoom API response wrapper.

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


class ZoomRESTClientViaServerToServer(HTTPClient):
    """Zoom REST client via Server-to-Server OAuth.

    Uses account_credentials grant type to obtain an access token from the
    Zoom OAuth token endpoint. The token is fetched automatically on first
    use via ensure_authenticated().

    Args:
        account_id: Zoom account ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    def __init__(
        self,
        account_id: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.zoom.us/v2",
    ) -> None:
        # Initialize with empty token; will be set after authentication
        super().__init__("", token_type="Bearer")
        self.base_url = base_url
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via account_credentials grant if not already authenticated.

        Uses HTTP Basic Auth (client_id:client_secret) and posts to the
        Zoom token endpoint with grant_type=account_credentials.
        """
        if self._authenticated:
            return

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode("utf-8")

        token_request = HTTPRequest(
            url="https://zoom.us/oauth/token",
            method="POST",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body={
                "grant_type": "account_credentials",
                "account_id": self.account_id,
            },
        )

        response = await self.execute(token_request)  # type: ignore[reportUnknownMemberType]
        response_data = response.json()

        access_token = response_data.get("access_token")
        if not access_token:
            raise ValueError(
                "Failed to obtain access token from Zoom S2S OAuth: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class ZoomRESTClientViaOAuth(HTTPClient):
    """Zoom REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Supports token refresh via client_id and client_secret.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        redirect_uri: OAuth redirect URI
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        base_url: str = "https://api.zoom.us/v2",
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = base_url
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_token(self) -> str:
        """Return the current access token."""
        return self.access_token

    def set_token(self, token: str) -> None:
        """Update the access token and Authorization header atomically."""
        self.access_token = token
        self.headers["Authorization"] = f"Bearer {token}"


class ZoomRESTClientViaToken(HTTPClient):
    """Zoom REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.zoom.us/v2",
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


class ZoomServerToServerConfig(BaseModel):
    """Configuration for Zoom client via Server-to-Server OAuth.

    Args:
        account_id: Zoom account ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    account_id: str
    client_id: str
    client_secret: str
    base_url: str = "https://api.zoom.us/v2"

    def create_client(self) -> ZoomRESTClientViaServerToServer:
        return ZoomRESTClientViaServerToServer(
            self.account_id,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class ZoomOAuthConfig(BaseModel):
    """Configuration for Zoom client via OAuth 2.0 authorization code flow.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        redirect_uri: OAuth redirect URI
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    base_url: str = "https://api.zoom.us/v2"

    def create_client(self) -> ZoomRESTClientViaOAuth:
        return ZoomRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.base_url,
        )


class ZoomTokenConfig(BaseModel):
    """Configuration for Zoom client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        base_url: API base URL (default: https://api.zoom.us/v2)
    """

    token: str
    base_url: str = "https://api.zoom.us/v2"

    def create_client(self) -> ZoomRESTClientViaToken:
        return ZoomRESTClientViaToken(self.token, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class ZoomAuthConfig(BaseModel):
    """Auth section of the Zoom connector configuration from etcd."""

    authType: ZoomAuthType = ZoomAuthType.SERVER_TO_SERVER
    accountId: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class ZoomCredentialsConfig(BaseModel):
    """Credentials section of the Zoom connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class ZoomConnectorConfig(BaseModel):
    """Top-level Zoom connector configuration from etcd."""

    auth: ZoomAuthConfig = Field(default_factory=ZoomAuthConfig)
    credentials: ZoomCredentialsConfig = Field(
        default_factory=ZoomCredentialsConfig
    )

    class Config:
        extra = "allow"


class ZoomSharedOAuthConfigEntry(BaseModel):
    """A single entry from the shared OAuth config list in etcd.

    Handles both camelCase and snake_case key variants from the config store.
    """

    entry_id: str | None = Field(default=None, alias="_id")
    accountId: str | None = None
    account_id: str | None = None
    clientId: str | None = None
    client_id: str | None = None
    clientSecret: str | None = None
    client_secret: str | None = None
    redirectUri: str | None = None
    redirect_uri: str | None = None

    class Config:
        extra = "allow"
        populate_by_name = True

    def resolved_account_id(self, fallback: str = "") -> str:
        return self.accountId or self.account_id or fallback

    def resolved_client_id(self, fallback: str = "") -> str:
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        return self.clientSecret or self.client_secret or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        return self.redirectUri or self.redirect_uri or fallback


class ZoomSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: ZoomSharedOAuthConfigEntry = Field(
        default_factory=ZoomSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class ZoomClient(IClient):
    """Builder class for Zoom clients with different authentication methods.

    Supports:
    - Server-to-Server OAuth (account_credentials grant)
    - OAuth 2.0 authorization code flow
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: (
            ZoomRESTClientViaServerToServer
            | ZoomRESTClientViaOAuth
            | ZoomRESTClientViaToken
        ),
    ) -> None:
        """Initialize with a Zoom client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> (
        ZoomRESTClientViaServerToServer
        | ZoomRESTClientViaOAuth
        | ZoomRESTClientViaToken
    ):
        """Return the Zoom client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: ZoomServerToServerConfig | ZoomOAuthConfig | ZoomTokenConfig,
    ) -> "ZoomClient":
        """Build ZoomClient with configuration.

        Args:
            config: ZoomServerToServerConfig, ZoomOAuthConfig, or
                    ZoomTokenConfig instance

        Returns:
            ZoomClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ZoomClient":
        """Build ZoomClient using configuration service.

        Supports three authentication strategies:
        1. SERVER_TO_SERVER: Account credentials grant with account_id,
           client_id, and client_secret
        2. OAUTH: OAuth 2.0 authorization code flow with access token
        3. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ZoomClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Zoom connector configuration")

            connector_config = ZoomConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == ZoomAuthType.SERVER_TO_SERVER:
                account_id = connector_config.auth.accountId or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    shared_cfg = await cls._find_shared_oauth_config(
                        config_service, oauth_config_id, logger
                    )
                    if shared_cfg:
                        account_id = shared_cfg.resolved_account_id(account_id)
                        client_id = shared_cfg.resolved_client_id(client_id)
                        client_secret = shared_cfg.resolved_client_secret(
                            client_secret
                        )

                if not (account_id and client_id and client_secret):
                    raise ValueError(
                        "account_id, client_id, and client_secret are required "
                        "for SERVER_TO_SERVER auth type"
                    )

                s2s_config = ZoomServerToServerConfig(
                    account_id=account_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(s2s_config.create_client())

            elif connector_config.auth.authType == ZoomAuthType.OAUTH:
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

                oauth_cfg = ZoomOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == ZoomAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = ZoomTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Zoom client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "ZoomClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            ZoomClient instance
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
            redirect_uri: str = str(auth_config.get("redirectUri", ""))

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
                    redirect_uri = shared_cfg.resolved_redirect_uri(
                        redirect_uri
                    )

            oauth_cfg = ZoomOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Zoom client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> ZoomSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched ZoomSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/zoom", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = ZoomSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Zoom."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Zoom connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Zoom connector config: {e}")
            raise ValueError(
                f"Failed to get Zoom connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
