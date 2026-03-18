# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""LumApps client implementation.

This module provides clients for interacting with the LumApps API using the
official ``lumapps-sdk`` Python package.

Authentication:
  - Access Token: Passed directly to ``BaseClient``
  - Service Account: Client credentials passed via ``auth_info``

SDK Reference: https://github.com/lumapps/lumapps-sdk
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from lumapps.api import BaseClient
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LumAppsAuthType(str, Enum):
    """Authentication types supported by the LumApps connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class LumAppsResponse(BaseModel):
    """Standardized LumApps API response wrapper.

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
# SDK wrapper classes
# ---------------------------------------------------------------------------


class LumAppsClientViaToken:
    """LumApps SDK wrapper authenticated via access token.

    Args:
        token: The access token
        base_url: LumApps cell URL (e.g. ``https://go-cell-001.api.lumapps.com``)
    """

    def __init__(self, token: str, base_url: str = "https://go-cell-001.api.lumapps.com") -> None:
        self.token = token
        self.base_url = base_url
        self._sdk: BaseClient | None = None

    def create_client(self) -> BaseClient:
        """Create and return the SDK client."""
        self._sdk = BaseClient(
            api_info={"base_url": self.base_url},
            token=self.token,
        )
        return self._sdk

    def get_sdk(self) -> BaseClient:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk


class LumAppsClientViaServiceAccount:
    """LumApps SDK wrapper authenticated via service account credentials.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        base_url: LumApps cell URL (e.g. ``https://go-cell-001.api.lumapps.com``)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://go-cell-001.api.lumapps.com",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url
        self._sdk: BaseClient | None = None

    def create_client(self) -> BaseClient:
        """Create and return the SDK client."""
        self._sdk = BaseClient(
            api_info={"base_url": self.base_url},
            auth_info={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        return self._sdk

    def get_sdk(self) -> BaseClient:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class LumAppsTokenConfig(BaseModel):
    """Configuration for LumApps client via access token.

    Args:
        token: The access token
        base_url: LumApps cell URL
    """

    token: str
    base_url: str = "https://go-cell-001.api.lumapps.com"

    def create_client(self) -> LumAppsClientViaToken:
        return LumAppsClientViaToken(token=self.token, base_url=self.base_url)


class LumAppsOAuthConfig(BaseModel):
    """Configuration for LumApps client via service account.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        access_token: Optional pre-fetched access token (used as token auth)
        base_url: LumApps cell URL
    """

    client_id: str
    client_secret: str
    access_token: str | None = None
    base_url: str = "https://go-cell-001.api.lumapps.com"

    def create_client(self) -> LumAppsClientViaToken | LumAppsClientViaServiceAccount:
        # If we already have an access token, use token auth
        if self.access_token:
            return LumAppsClientViaToken(token=self.access_token, base_url=self.base_url)
        return LumAppsClientViaServiceAccount(
            client_id=self.client_id,
            client_secret=self.client_secret,
            base_url=self.base_url,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class LumAppsAuthConfigModel(BaseModel):
    """Auth section of the LumApps connector configuration from etcd."""

    authType: LumAppsAuthType = LumAppsAuthType.OAUTH
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class LumAppsCredentialsConfig(BaseModel):
    """Credentials section of the LumApps connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class LumAppsConnectorConfig(BaseModel):
    """Top-level LumApps connector configuration from etcd."""

    auth: LumAppsAuthConfigModel = Field(default_factory=LumAppsAuthConfigModel)
    credentials: LumAppsCredentialsConfig = Field(
        default_factory=LumAppsCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class LumAppsClient(IClient):
    """Builder class for LumApps clients using the official SDK.

    Supports:
    - Access token authentication
    - Service account (client credentials) authentication
    """

    def __init__(
        self,
        client: LumAppsClientViaToken | LumAppsClientViaServiceAccount,
    ) -> None:
        """Initialize with a LumApps SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> LumAppsClientViaToken | LumAppsClientViaServiceAccount:
        """Return the LumApps SDK wrapper."""
        return self.client

    def get_sdk(self) -> BaseClient:
        """Return the underlying LumApps SDK instance."""
        return self.client.get_sdk()

    @classmethod
    def build_with_config(
        cls,
        config: LumAppsTokenConfig | LumAppsOAuthConfig,
    ) -> "LumAppsClient":
        """Build LumAppsClient with configuration.

        Args:
            config: LumAppsTokenConfig or LumAppsOAuthConfig instance

        Returns:
            LumAppsClient instance
        """
        wrapper = config.create_client()
        wrapper.get_sdk()  # eagerly initialize
        return cls(wrapper)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "LumAppsClient":
        """Build LumAppsClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 access token (service account)
        2. TOKEN: Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            LumAppsClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get LumApps connector configuration"
                )

            connector_config = LumAppsConnectorConfig.model_validate(raw_config)

            base_url = (
                connector_config.auth.baseUrl
                or "https://go-cell-001.api.lumapps.com"
            )

            if connector_config.auth.authType == LumAppsAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/lumapps", default=[]
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

                if access_token:
                    wrapper = LumAppsClientViaToken(
                        token=access_token, base_url=base_url
                    )
                elif client_id and client_secret:
                    wrapper = LumAppsClientViaServiceAccount(
                        client_id=client_id,
                        client_secret=client_secret,
                        base_url=base_url,
                    )
                else:
                    raise ValueError(
                        "Access token or client credentials required for OAuth auth type"
                    )

                wrapper.get_sdk()
                return cls(wrapper)

            elif connector_config.auth.authType == LumAppsAuthType.TOKEN:
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                wrapper = LumAppsClientViaToken(token=token, base_url=base_url)
                wrapper.get_sdk()
                return cls(wrapper)

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build LumApps client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "LumAppsClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            LumAppsClient instance
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
                        "/services/oauth/lumapps", default=[]
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

            wrapper = LumAppsClientViaToken(token=access_token)
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build LumApps client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for LumApps."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get LumApps connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get LumApps connector config: {e}")
            raise ValueError(
                f"Failed to get LumApps connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
