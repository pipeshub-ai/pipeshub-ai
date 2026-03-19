"""DokuWiki client implementation.

This module provides clients for interacting with the DokuWiki JSON-RPC API using either:
1. HTTP Basic Auth (username:password)
2. HTTP Bearer Token (JWT)

Authentication Reference: https://www.dokuwiki.org/devel:jsonapi
API Reference: https://www.dokuwiki.org/devel:jsonapi

The DokuWiki JSON API uses a single endpoint:
    https://<instance>/lib/exe/jsonapi.php
All operations are POST requests with the method name as the URL path.
"""

import base64
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


class DokuWikiAuthType(str, Enum):
    """Authentication types supported by the DokuWiki connector."""

    BASIC_AUTH = "BASIC_AUTH"
    BEARER = "BEARER"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DokuWikiResponse(BaseModel):
    """Standardized DokuWiki API response wrapper.

    The data field supports JSON responses (dict/list) and primitive values
    (str/int/bool). When serializing to dict/JSON, all types are handled
    automatically.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | str | int | bool | None = Field(
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

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


def _normalize_base_url(instance_url: str) -> str:
    """Normalize a DokuWiki instance URL to the JSON-RPC API base URL.

    Accepts any of:
    - Bare hostname: ``wiki.company.com``
    - Full URL: ``https://wiki.company.com``
    - URL with subpath: ``https://company.com/wiki``
    - URL with trailing slash or ``doku.php`` etc.

    Returns the base URL for the JSON API endpoint, e.g.
    ``https://wiki.company.com/lib/exe/jsonapi.php``.
    """
    url = instance_url.strip().rstrip("/")

    # Strip trailing doku.php, start, or similar page paths
    for suffix in (
        "/doku.php",
        "/start",
        "/lib/exe/xmlrpc.php",
        "/lib/exe/jsonapi.php",
    ):
        if url.endswith(suffix):
            url = url[: -len(suffix)]

    # Ensure scheme is present
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    return f"{url.rstrip('/')}/lib/exe/jsonapi.php"


class DokuWikiRESTClientViaBasicAuth(HTTPClient):
    """DokuWiki REST client via HTTP Basic Auth (username:password).

    Credentials are base64-encoded and passed in the Authorization header.

    Args:
        instance_url: The DokuWiki instance base URL.
            Examples: ``https://wiki.company.com``,
            ``https://company.com/wiki``, ``wiki.example.com``
        username: DokuWiki username
        password: DokuWiki password
    """

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
    ) -> None:
        super().__init__("", token_type="Basic")
        self.instance_url = instance_url.rstrip("/")
        self.base_url = _normalize_base_url(instance_url)
        self.username = username
        self.password = password
        credentials = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class DokuWikiRESTClientViaBearerToken(HTTPClient):
    """DokuWiki REST client via HTTP Bearer Token (JWT).

    JWT token is passed as a Bearer token in the Authorization header.

    Args:
        instance_url: The DokuWiki instance base URL.
            Examples: ``https://wiki.company.com``,
            ``https://company.com/wiki``, ``wiki.example.com``
        jwt_token: JWT authentication token
    """

    def __init__(
        self,
        instance_url: str,
        jwt_token: str,
    ) -> None:
        super().__init__(jwt_token, "Bearer")
        self.instance_url = instance_url.rstrip("/")
        self.base_url = _normalize_base_url(instance_url)
        self.jwt_token = jwt_token
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DokuWikiBasicAuthConfig(BaseModel):
    """Configuration for DokuWiki client via Basic Auth.

    Args:
        instance_url: The DokuWiki instance base URL
        username: DokuWiki username
        password: DokuWiki password
    """

    instance_url: str
    username: str
    password: str

    def create_client(self) -> DokuWikiRESTClientViaBasicAuth:
        return DokuWikiRESTClientViaBasicAuth(
            self.instance_url,
            self.username,
            self.password,
        )


class DokuWikiBearerTokenConfig(BaseModel):
    """Configuration for DokuWiki client via Bearer Token (JWT).

    Args:
        instance_url: The DokuWiki instance base URL
        jwt_token: JWT authentication token
    """

    instance_url: str
    jwt_token: str

    def create_client(self) -> DokuWikiRESTClientViaBearerToken:
        return DokuWikiRESTClientViaBearerToken(
            self.instance_url,
            self.jwt_token,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DokuWikiAuthConfig(BaseModel):
    """Auth section of the DokuWiki connector configuration from etcd."""

    authType: DokuWikiAuthType = DokuWikiAuthType.BASIC_AUTH
    instanceUrl: str | None = None
    username: str | None = None
    password: str | None = None
    jwtToken: str | None = None

    class Config:
        extra = "allow"


class DokuWikiCredentialsConfig(BaseModel):
    """Credentials section of the DokuWiki connector configuration."""

    instance_url: str | None = None
    username: str | None = None
    password: str | None = None
    jwt_token: str | None = None

    class Config:
        extra = "allow"


class DokuWikiConnectorConfig(BaseModel):
    """Top-level DokuWiki connector configuration from etcd."""

    auth: DokuWikiAuthConfig = Field(default_factory=DokuWikiAuthConfig)
    credentials: DokuWikiCredentialsConfig = Field(
        default_factory=DokuWikiCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DokuWikiClient(IClient):
    """Builder class for DokuWiki clients with different authentication methods.

    Supports:
    - HTTP Basic Auth (username:password)
    - HTTP Bearer Token (JWT)
    """

    def __init__(
        self,
        client: DokuWikiRESTClientViaBasicAuth | DokuWikiRESTClientViaBearerToken,
    ) -> None:
        """Initialize with a DokuWiki client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> DokuWikiRESTClientViaBasicAuth | DokuWikiRESTClientViaBearerToken:
        """Return the DokuWiki client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: DokuWikiBasicAuthConfig | DokuWikiBearerTokenConfig,
    ) -> "DokuWikiClient":
        """Build DokuWikiClient with configuration.

        Args:
            config: DokuWikiBasicAuthConfig or DokuWikiBearerTokenConfig instance

        Returns:
            DokuWikiClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "DokuWikiClient":
        """Build DokuWikiClient using configuration service.

        Supports two authentication strategies:
        1. BEARER: For JWT token authentication
        2. BASIC_AUTH: For username:password authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DokuWikiClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get DokuWiki connector configuration"
                )

            connector_config = DokuWikiConnectorConfig.model_validate(raw_config)

            instance_url = (
                connector_config.credentials.instance_url
                or connector_config.auth.instanceUrl
                or ""
            )

            if not instance_url:
                raise ValueError(
                    "Instance URL is required for DokuWiki authentication"
                )

            if connector_config.auth.authType == DokuWikiAuthType.BEARER:
                jwt_token = (
                    connector_config.credentials.jwt_token
                    or connector_config.auth.jwtToken
                    or ""
                )

                if not jwt_token:
                    raise ValueError(
                        "JWT token required for Bearer auth type"
                    )

                bearer_cfg = DokuWikiBearerTokenConfig(
                    instance_url=instance_url,
                    jwt_token=jwt_token,
                )
                return cls(bearer_cfg.create_client())

            else:
                # Default: BASIC_AUTH
                username = (
                    connector_config.credentials.username
                    or connector_config.auth.username
                    or ""
                )
                password = (
                    connector_config.credentials.password
                    or connector_config.auth.password
                    or ""
                )

                if not username or not password:
                    raise ValueError(
                        "Username and password required for "
                        "BASIC_AUTH auth type"
                    )

                basic_cfg = DokuWikiBasicAuthConfig(
                    instance_url=instance_url,
                    username=username,
                    password=password,
                )
                return cls(basic_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build DokuWiki client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DokuWikiClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            DokuWikiClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            auth_type = auth_config.get("authType", "BASIC_AUTH")

            instance_url = str(
                credentials.get("instance_url", "")
                or auth_config.get("instanceUrl", "")
            )

            if not instance_url:
                raise ValueError(
                    "Instance URL not found in toolset config"
                )

            if auth_type == "BEARER":
                jwt_token = str(
                    credentials.get("jwt_token", "")
                    or auth_config.get("jwtToken", "")
                )
                if not jwt_token:
                    raise ValueError(
                        "JWT token not found in toolset config"
                    )
                bearer_cfg = DokuWikiBearerTokenConfig(
                    instance_url=instance_url,
                    jwt_token=jwt_token,
                )
                return cls(bearer_cfg.create_client())

            else:
                # Default: BASIC_AUTH
                username = str(
                    credentials.get("username", "")
                    or auth_config.get("username", "")
                )
                password = str(
                    credentials.get("password", "")
                    or auth_config.get("password", "")
                )
                if not username or not password:
                    raise ValueError(
                        "Username and password not found in toolset config"
                    )
                basic_cfg = DokuWikiBasicAuthConfig(
                    instance_url=instance_url,
                    username=username,
                    password=password,
                )
                return cls(basic_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build DokuWiki client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for DokuWiki."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get DokuWiki connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get DokuWiki connector config: {e}")
            raise ValueError(
                f"Failed to get DokuWiki connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
