"""DokuWiki client implementation.

This module provides a client for interacting with DokuWiki via XML-RPC.
DokuWiki exposes its API through XML-RPC at /lib/exe/xmlrpc.php.

Authentication is done via Basic Auth embedded in the XML-RPC transport URL.
This client does NOT extend HTTPClient since it uses xmlrpc.client.ServerProxy.

API Reference: https://www.dokuwiki.org/devel:xmlrpc
"""

import logging
import xmlrpc.client
from typing import Any, cast
from urllib.parse import quote

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DokuWikiResponse(BaseModel):
    """Standardized DokuWiki API response wrapper.

    The data field supports various response types from the XML-RPC API.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | str | int | bool | None = Field(
        default=None, description="Response data from XML-RPC call"
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
# XML-RPC client class
# ---------------------------------------------------------------------------


class DokuWikiClientViaBasicAuth:
    """DokuWiki XML-RPC client via Basic Auth.

    Credentials are embedded in the XML-RPC URL for transport-level
    authentication. Uses Python's xmlrpc.client.ServerProxy.

    Args:
        instance_url: DokuWiki instance domain (e.g. "wiki.example.com")
        username: DokuWiki username
        password: DokuWiki password
    """

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
    ) -> None:
        self.instance_url = instance_url
        self.username = username
        # URL-encode credentials to handle special characters
        encoded_user = quote(username, safe="")
        encoded_pass = quote(password, safe="")
        url = (
            f"https://{encoded_user}:{encoded_pass}"
            f"@{instance_url}/lib/exe/xmlrpc.php"
        )
        self._server = xmlrpc.client.ServerProxy(url)

    def get_sdk(self) -> xmlrpc.client.ServerProxy:
        """Return the XML-RPC server proxy."""
        return self._server

    def get_instance_url(self) -> str:
        """Get the instance URL."""
        return self.instance_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DokuWikiBasicAuthConfig(BaseModel):
    """Configuration for DokuWiki client via Basic Auth.

    Args:
        instance_url: DokuWiki instance domain (e.g. "wiki.example.com")
        username: DokuWiki username
        password: DokuWiki password
    """

    instance_url: str
    username: str
    password: str

    def create_client(self) -> DokuWikiClientViaBasicAuth:
        return DokuWikiClientViaBasicAuth(
            self.instance_url, self.username, self.password
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DokuWikiAuthConfigModel(BaseModel):
    """Auth section of the DokuWiki connector configuration from etcd."""

    instanceUrl: str | None = None
    username: str | None = None
    password: str | None = None

    class Config:
        extra = "allow"


class DokuWikiConnectorConfig(BaseModel):
    """Top-level DokuWiki connector configuration from etcd."""

    auth: DokuWikiAuthConfigModel = Field(
        default_factory=DokuWikiAuthConfigModel
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DokuWikiClient(IClient):
    """Builder class for DokuWiki clients.

    Supports:
    - Basic Auth (username + password) via XML-RPC transport
    """

    def __init__(
        self,
        client: DokuWikiClientViaBasicAuth,
    ) -> None:
        """Initialize with a DokuWiki client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> DokuWikiClientViaBasicAuth:
        """Return the DokuWiki client object."""
        return self.client

    def get_sdk(self) -> xmlrpc.client.ServerProxy:
        """Return the XML-RPC server proxy for direct method calls."""
        return self.client.get_sdk()

    def get_instance_url(self) -> str:
        """Return the instance URL."""
        return self.client.get_instance_url()

    @classmethod
    def build_with_config(
        cls,
        config: DokuWikiBasicAuthConfig,
    ) -> "DokuWikiClient":
        """Build DokuWikiClient with configuration.

        Args:
            config: DokuWikiBasicAuthConfig instance

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

        Supports Basic Auth via XML-RPC transport.

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

            connector_config = DokuWikiConnectorConfig.model_validate(
                raw_config
            )

            instance_url = connector_config.auth.instanceUrl or ""
            username = connector_config.auth.username or ""
            password = connector_config.auth.password or ""

            if not instance_url:
                raise ValueError("Instance URL is required")
            if not (username and password):
                raise ValueError(
                    "Username and password are required"
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
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            instance_url: str = str(auth_config.get("instanceUrl", ""))
            if not instance_url:
                raise ValueError(
                    "Instance URL not found in toolset config"
                )

            username: str = str(auth_config.get("username", ""))
            password: str = str(auth_config.get("password", ""))
            if not (username and password):
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
            logger.error(
                f"Failed to get DokuWiki connector config: {e}"
            )
            raise ValueError(
                f"Failed to get DokuWiki connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
