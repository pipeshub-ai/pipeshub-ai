"""WordPress client implementation.

This module provides clients for interacting with the WordPress REST API using:
1. OAuth 2.0 access token authentication (WordPress.com)
2. Application Password authentication (self-hosted WordPress)
3. Pre-generated Bearer token authentication

WordPress.com OAuth Reference: https://developer.wordpress.com/docs/oauth2/
WordPress REST API Reference: https://developer.wordpress.org/rest-api/reference/
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


class WordPressAuthType(str, Enum):
    """Authentication types supported by the WordPress connector."""

    OAUTH = "OAUTH"
    APPLICATION_PASSWORD = "APPLICATION_PASSWORD"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class WordPressResponse(BaseModel):
    """Standardized WordPress API response wrapper.

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


class WordPressRESTClientViaOAuth(HTTPClient):
    """WordPress REST client via OAuth 2.0 (WordPress.com).

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    For WordPress.com sites, the base URL uses the WordPress.com REST API
    endpoint with the site ID.

    Args:
        access_token: The OAuth access token
        site_id: WordPress.com site ID or domain
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        base_url: API base URL (auto-constructed for WordPress.com)
    """

    def __init__(
        self,
        access_token: str,
        site_id: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = (
            base_url
            or f"https://public-api.wordpress.com/wp/v2/sites/{site_id}"
        )
        self.access_token = access_token
        self.site_id = site_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class WordPressRESTClientViaApplicationPassword(HTTPClient):
    """WordPress REST client via Application Password (self-hosted).

    Uses HTTP Basic Authentication with the WordPress username and an
    application password. This is the recommended auth method for
    self-hosted WordPress sites.

    Args:
        site_url: The WordPress site URL (e.g., "example.com" or
                  "example.com/wordpress")
        username: WordPress username
        application_password: Application password generated in WordPress
        base_url: API base URL (auto-constructed from site_url)
    """

    def __init__(
        self,
        site_url: str,
        username: str,
        application_password: str,
        base_url: str | None = None,
    ) -> None:
        # Initialize with empty token; we override the header below
        super().__init__("", token_type="Basic")
        # Strip protocol if provided
        clean_url = site_url.rstrip("/")
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"https://{clean_url}"
        self.base_url = base_url or f"{clean_url}/wp-json/wp/v2"
        self.site_url = site_url
        self.username = username
        self.application_password = application_password
        # Basic Auth: base64(username:application_password)
        credentials = base64.b64encode(
            f"{username}:{application_password}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class WordPressRESTClientViaToken(HTTPClient):
    """WordPress REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The pre-generated Bearer token
        site_url: The WordPress site URL
        base_url: API base URL (auto-constructed from site_url)
    """

    def __init__(
        self,
        token: str,
        site_url: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        # Strip protocol if provided
        clean_url = site_url.rstrip("/")
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"https://{clean_url}"
        self.base_url = base_url or f"{clean_url}/wp-json/wp/v2"
        self.site_url = site_url
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class WordPressOAuthConfig(BaseModel):
    """Configuration for WordPress client via OAuth 2.0 (WordPress.com).

    Args:
        access_token: The OAuth access token
        site_id: WordPress.com site ID or domain
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: API base URL (optional override)
    """

    access_token: str
    site_id: str
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str | None = None

    def create_client(self) -> WordPressRESTClientViaOAuth:
        return WordPressRESTClientViaOAuth(
            self.access_token,
            self.site_id,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


class WordPressApplicationPasswordConfig(BaseModel):
    """Configuration for WordPress client via Application Password.

    Args:
        site_url: The WordPress site URL
        username: WordPress username
        application_password: Application password
        base_url: API base URL (optional override)
    """

    site_url: str
    username: str
    application_password: str
    base_url: str | None = None

    def create_client(self) -> WordPressRESTClientViaApplicationPassword:
        return WordPressRESTClientViaApplicationPassword(
            self.site_url,
            self.username,
            self.application_password,
            self.base_url,
        )


class WordPressTokenConfig(BaseModel):
    """Configuration for WordPress client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        site_url: The WordPress site URL
        base_url: API base URL (optional override)
    """

    token: str
    site_url: str
    base_url: str | None = None

    def create_client(self) -> WordPressRESTClientViaToken:
        return WordPressRESTClientViaToken(
            self.token,
            self.site_url,
            self.base_url,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class WordPressAuthConfig(BaseModel):
    """Auth section of the WordPress connector configuration from etcd."""

    authType: WordPressAuthType = WordPressAuthType.OAUTH
    siteUrl: str | None = None
    siteId: str | None = None
    username: str | None = None
    applicationPassword: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class WordPressCredentialsConfig(BaseModel):
    """Credentials section of the WordPress connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class WordPressConnectorConfig(BaseModel):
    """Top-level WordPress connector configuration from etcd."""

    auth: WordPressAuthConfig = Field(default_factory=WordPressAuthConfig)
    credentials: WordPressCredentialsConfig = Field(
        default_factory=WordPressCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class WordPressClient(IClient):
    """Builder class for WordPress clients with different authentication methods.

    Supports:
    - OAuth 2.0 (WordPress.com)
    - Application Password (self-hosted WordPress)
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: (
            WordPressRESTClientViaOAuth
            | WordPressRESTClientViaApplicationPassword
            | WordPressRESTClientViaToken
        ),
    ) -> None:
        """Initialize with a WordPress client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> (
        WordPressRESTClientViaOAuth
        | WordPressRESTClientViaApplicationPassword
        | WordPressRESTClientViaToken
    ):
        """Return the WordPress client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: (
            WordPressOAuthConfig
            | WordPressApplicationPasswordConfig
            | WordPressTokenConfig
        ),
    ) -> "WordPressClient":
        """Build WordPressClient with configuration.

        Args:
            config: WordPressOAuthConfig, WordPressApplicationPasswordConfig,
                    or WordPressTokenConfig instance

        Returns:
            WordPressClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "WordPressClient":
        """Build WordPressClient using configuration service.

        Supports three authentication strategies:
        1. OAUTH: OAuth 2.0 with WordPress.com access token
        2. APPLICATION_PASSWORD: Basic Auth with username + app password
        3. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            WordPressClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get WordPress connector configuration"
                )

            connector_config = WordPressConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == WordPressAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                site_id = connector_config.auth.siteId or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/wordpress", default=[]
                        )
                        oauth_configs: list[Any] = (
                            cast(list[Any], oauth_configs_raw)
                            if isinstance(oauth_configs_raw, list)
                            else []
                        )
                        for cfg in oauth_configs:
                            c: dict[str, Any] = cast(dict[str, Any], cfg)
                            if c.get("_id") == oauth_config_id:
                                shared: dict[str, Any] = cast(
                                    dict[str, Any], c.get("config", {})
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
                if not site_id:
                    raise ValueError(
                        "Site ID required for OAuth auth type "
                        "(WordPress.com site ID or domain)"
                    )

                oauth_cfg = WordPressOAuthConfig(
                    access_token=access_token,
                    site_id=site_id,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif (
                connector_config.auth.authType
                == WordPressAuthType.APPLICATION_PASSWORD
            ):
                site_url = connector_config.auth.siteUrl or ""
                username = connector_config.auth.username or ""
                app_password = (
                    connector_config.auth.applicationPassword or ""
                )

                if not (site_url and username and app_password):
                    raise ValueError(
                        "site_url, username, and application_password are "
                        "required for APPLICATION_PASSWORD auth type"
                    )

                app_pw_cfg = WordPressApplicationPasswordConfig(
                    site_url=site_url,
                    username=username,
                    application_password=app_password,
                )
                return cls(app_pw_cfg.create_client())

            elif connector_config.auth.authType == WordPressAuthType.TOKEN:
                token = connector_config.auth.token or ""
                site_url = connector_config.auth.siteUrl or ""

                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )
                if not site_url:
                    raise ValueError(
                        "Site URL required for TOKEN auth type"
                    )

                token_config = WordPressTokenConfig(
                    token=token,
                    site_url=site_url,
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build WordPress client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "WordPressClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            WordPressClient instance
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

            site_id: str = str(auth_config.get("siteId", ""))
            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/wordpress", default=[]
                    )
                    oauth_configs: list[Any] = (
                        cast(list[Any], oauth_configs_raw)
                        if isinstance(oauth_configs_raw, list)
                        else []
                    )
                    for cfg in oauth_configs:
                        c: dict[str, Any] = cast(dict[str, Any], cfg)
                        if c.get("_id") == oauth_config_id:
                            shared: dict[str, Any] = cast(
                                dict[str, Any], c.get("config", {})
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

            oauth_cfg = WordPressOAuthConfig(
                access_token=access_token,
                site_id=site_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build WordPress client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for WordPress."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get WordPress connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get WordPress connector config: {e}")
            raise ValueError(
                f"Failed to get WordPress connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
