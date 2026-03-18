"""Tableau client implementation using the official tableauserverclient SDK.

This module provides clients for interacting with Tableau Server/Cloud using:
1. Personal Access Token (PAT) authentication via TSC.PersonalAccessTokenAuth
2. Username/Password authentication via TSC.TableauAuth

SDK Reference: https://tableau.github.io/server-client-python/docs/
"""

import logging
from enum import Enum
from typing import Any, cast

import tableauserverclient as TSC  # type: ignore[reportMissingImports]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TableauAuthType(str, Enum):
    """Authentication types supported by the Tableau connector."""

    PAT = "PAT"
    PASSWORD = "PASSWORD"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class TableauResponse(BaseModel):
    """Standardized Tableau API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = None
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK client classes
# ---------------------------------------------------------------------------


class TableauClientViaPAT:
    """Tableau SDK client via Personal Access Token (PAT).

    Wraps TSC.Server and authenticates using TSC.PersonalAccessTokenAuth.

    Args:
        server_url: Tableau Server/Cloud URL (e.g., "https://10ax.online.tableau.com")
        token_name: Personal Access Token name
        token_secret: Personal Access Token secret
        site_id: Site content URL (empty string for default site)
    """

    def __init__(
        self,
        server_url: str,
        token_name: str,
        token_secret: str,
        site_id: str = "",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.token_name = token_name
        self.token_secret = token_secret
        self.site_id = site_id
        self._server: TSC.Server | None = None  # type: ignore[reportUnknownMemberType]
        self._authenticated = False

    def create_client(self) -> TSC.Server:  # type: ignore[reportUnknownMemberType]
        """Create and authenticate the TSC.Server instance."""
        self._server = TSC.Server(self.server_url, use_server_version=True)  # type: ignore[reportUnknownMemberType]
        self.ensure_authenticated()
        return self._server  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def ensure_authenticated(self) -> None:
        """Sign in to Tableau using PAT credentials if not already authenticated."""
        if self._authenticated:
            return
        if self._server is None:  # type: ignore[reportUnknownMemberType]
            self._server = TSC.Server(self.server_url, use_server_version=True)  # type: ignore[reportUnknownMemberType]
        auth = TSC.PersonalAccessTokenAuth(  # type: ignore[reportUnknownMemberType]
            self.token_name, self.token_secret, site_id=self.site_id
        )
        try:
            self._server.auth.sign_in(auth)  # type: ignore[reportUnknownMemberType, reportOptionalMemberAccess]
        except Exception as e:
            raise RuntimeError("Tableau PAT authentication failed") from e
        self._authenticated = True

    def get_sdk(self) -> TSC.Server:  # type: ignore[reportUnknownMemberType]
        """Return the authenticated TSC.Server instance."""
        if self._server is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
        return self._server  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def get_server_url(self) -> str:
        """Return the server URL."""
        return self.server_url


class TableauClientViaPassword:
    """Tableau SDK client via Username/Password.

    Wraps TSC.Server and authenticates using TSC.TableauAuth.

    Args:
        server_url: Tableau Server/Cloud URL (e.g., "https://10ax.online.tableau.com")
        username: Tableau username
        password: Tableau password
        site_id: Site content URL (empty string for default site)
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        site_id: str = "",
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.username = username
        self.password = password
        self.site_id = site_id
        self._server: TSC.Server | None = None  # type: ignore[reportUnknownMemberType]
        self._authenticated = False

    def create_client(self) -> TSC.Server:  # type: ignore[reportUnknownMemberType]
        """Create and authenticate the TSC.Server instance."""
        self._server = TSC.Server(self.server_url, use_server_version=True)  # type: ignore[reportUnknownMemberType]
        self.ensure_authenticated()
        return self._server  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def ensure_authenticated(self) -> None:
        """Sign in to Tableau using username/password if not already authenticated."""
        if self._authenticated:
            return
        if self._server is None:  # type: ignore[reportUnknownMemberType]
            self._server = TSC.Server(self.server_url, use_server_version=True)  # type: ignore[reportUnknownMemberType]
        auth = TSC.TableauAuth(self.username, self.password, site_id=self.site_id)  # type: ignore[reportUnknownMemberType]
        try:
            self._server.auth.sign_in(auth)  # type: ignore[reportUnknownMemberType, reportOptionalMemberAccess]
        except Exception as e:
            raise RuntimeError("Tableau password authentication failed") from e
        self._authenticated = True

    def get_sdk(self) -> TSC.Server:  # type: ignore[reportUnknownMemberType]
        """Return the authenticated TSC.Server instance."""
        if self._server is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]
        return self._server  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def get_server_url(self) -> str:
        """Return the server URL."""
        return self.server_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class TableauPATConfig(BaseModel):
    """Configuration for Tableau client via Personal Access Token.

    Args:
        server_url: Tableau Server/Cloud URL
        token_name: Personal Access Token name
        token_secret: Personal Access Token secret
        site_id: Site content URL (empty string for default site)
    """

    server_url: str
    token_name: str
    token_secret: str
    site_id: str = ""

    def create_client(self) -> TableauClientViaPAT:
        return TableauClientViaPAT(
            server_url=self.server_url,
            token_name=self.token_name,
            token_secret=self.token_secret,
            site_id=self.site_id,
        )


class TableauPasswordConfig(BaseModel):
    """Configuration for Tableau client via Username/Password.

    Args:
        server_url: Tableau Server/Cloud URL
        username: Tableau username
        password: Tableau password
        site_id: Site content URL (empty string for default site)
    """

    server_url: str
    username: str
    password: str
    site_id: str = ""

    def create_client(self) -> TableauClientViaPassword:
        return TableauClientViaPassword(
            server_url=self.server_url,
            username=self.username,
            password=self.password,
            site_id=self.site_id,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class TableauAuthConfig(BaseModel):
    """Auth section of the Tableau connector configuration from etcd."""

    authType: TableauAuthType = TableauAuthType.PAT
    serverUrl: str | None = None
    tokenName: str | None = None
    tokenSecret: str | None = None
    siteId: str | None = None
    username: str | None = None
    password: str | None = None

    class Config:
        extra = "allow"


class TableauConnectorConfig(BaseModel):
    """Top-level Tableau connector configuration from etcd."""

    auth: TableauAuthConfig = Field(default_factory=TableauAuthConfig)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class TableauClient(IClient):
    """Builder class for Tableau clients with different authentication methods.

    Supports:
    - Personal Access Token (PAT) authentication
    - Username/Password authentication
    """

    def __init__(
        self,
        client: TableauClientViaPAT | TableauClientViaPassword,
    ) -> None:
        """Initialize with a Tableau SDK client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> TableauClientViaPAT | TableauClientViaPassword:
        """Return the Tableau SDK client object."""
        return self.client

    def get_sdk(self) -> TSC.Server:  # type: ignore[reportUnknownMemberType]
        """Return the authenticated TSC.Server instance."""
        return self.client.get_sdk()  # type: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def get_server_url(self) -> str:
        """Return the server URL."""
        return self.client.get_server_url()

    @classmethod
    def build_with_config(
        cls,
        config: TableauPATConfig | TableauPasswordConfig,
    ) -> "TableauClient":
        """Build TableauClient with configuration.

        Args:
            config: TableauPATConfig or TableauPasswordConfig instance

        Returns:
            TableauClient instance
        """
        client = config.create_client()
        client.get_sdk()  # type: ignore[reportUnknownMemberType]
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "TableauClient":
        """Build TableauClient using configuration service.

        Supports two authentication strategies:
        1. PAT: Personal Access Token
        2. PASSWORD: Username/Password

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            TableauClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Tableau connector configuration"
                )

            connector_config = TableauConnectorConfig.model_validate(
                raw_config
            )

            server_url = connector_config.auth.serverUrl or ""
            if not server_url:
                raise ValueError(
                    "server_url is required for Tableau connector"
                )

            site_id = connector_config.auth.siteId or ""

            if connector_config.auth.authType == TableauAuthType.PAT:
                token_name = connector_config.auth.tokenName or ""
                token_secret = connector_config.auth.tokenSecret or ""

                if not (token_name and token_secret):
                    raise ValueError(
                        "token_name and token_secret are required "
                        "for PAT auth type"
                    )

                pat_config = TableauPATConfig(
                    server_url=server_url,
                    token_name=token_name,
                    token_secret=token_secret,
                    site_id=site_id,
                )
                client = pat_config.create_client()
                client.get_sdk()  # type: ignore[reportUnknownMemberType]
                return cls(client)

            elif connector_config.auth.authType == TableauAuthType.PASSWORD:
                username = connector_config.auth.username or ""
                password = connector_config.auth.password or ""

                if not (username and password):
                    raise ValueError(
                        "username and password are required "
                        "for PASSWORD auth type"
                    )

                password_config = TableauPasswordConfig(
                    server_url=server_url,
                    username=username,
                    password=password,
                    site_id=site_id,
                )
                client = password_config.create_client()
                client.get_sdk()  # type: ignore[reportUnknownMemberType]
                return cls(client)

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Tableau client from services: {e!s}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "TableauClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused for Tableau)

        Returns:
            TableauClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            server_url: str = str(auth_config.get("serverUrl", ""))
            if not server_url:
                raise ValueError(
                    "server_url not found in toolset config"
                )

            site_id: str = str(auth_config.get("siteId", ""))

            # Try PAT auth first
            token_name: str = str(auth_config.get("tokenName", ""))
            token_secret: str = str(auth_config.get("tokenSecret", ""))

            if token_name and token_secret:
                pat_config = TableauPATConfig(
                    server_url=server_url,
                    token_name=token_name,
                    token_secret=token_secret,
                    site_id=site_id,
                )
                client = pat_config.create_client()
                client.get_sdk()  # type: ignore[reportUnknownMemberType]
                return cls(client)

            # Fall back to password auth
            username: str = str(auth_config.get("username", ""))
            password: str = str(auth_config.get("password", ""))

            if username and password:
                password_config = TableauPasswordConfig(
                    server_url=server_url,
                    username=username,
                    password=password,
                    site_id=site_id,
                )
                client = password_config.create_client()
                client.get_sdk()  # type: ignore[reportUnknownMemberType]
                return cls(client)

            raise ValueError(
                "Either tokenName+tokenSecret or "
                "username+password required in toolset config"
            )

        except Exception as e:
            logger.error(
                f"Failed to build Tableau client from toolset: {e!s}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Tableau."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Tableau connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Tableau connector config: {e}")
            raise ValueError(
                f"Failed to get Tableau connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
