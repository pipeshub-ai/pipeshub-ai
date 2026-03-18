# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Bynder client implementation.

This module provides clients for interacting with the Bynder API using the
official ``bynder-sdk`` Python package.

Authentication:
  - Permanent Token: Direct token authentication
  - OAuth 2.0: Client credentials with token

SDK Reference: https://github.com/Bynder/bynder-python-sdk
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from bynder_sdk import BynderClient as BynderSDKClient
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BynderAuthType(str, Enum):
    """Authentication types supported by the Bynder connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class BynderResponse(BaseModel):
    """Standardized Bynder API response wrapper.

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


class BynderClientViaPermanentToken:
    """Bynder SDK wrapper authenticated via permanent token.

    Args:
        domain: Bynder portal domain (e.g. ``portal.getbynder.com``)
        permanent_token: The permanent API token
    """

    def __init__(self, domain: str, permanent_token: str) -> None:
        self.domain = domain
        self.permanent_token = permanent_token
        self._sdk: BynderSDKClient | None = None

    def create_client(self) -> BynderSDKClient:
        """Create and return the SDK client."""
        self._sdk = BynderSDKClient(
            domain=self.domain,
            permanent_token=self.permanent_token,
        )
        return self._sdk

    def get_sdk(self) -> BynderSDKClient:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_domain(self) -> str:
        """Get the Bynder domain."""
        return self.domain


class BynderClientViaOAuth:
    """Bynder SDK wrapper authenticated via OAuth 2.0.

    Args:
        domain: Bynder portal domain (e.g. ``portal.getbynder.com``)
        redirect_uri: OAuth redirect URI
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token: OAuth token dict (must include ``access_token``)
    """

    def __init__(
        self,
        domain: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        token: dict[str, Any],
    ) -> None:
        self.domain = domain
        self.redirect_uri = redirect_uri
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = token
        self._sdk: BynderSDKClient | None = None

    def create_client(self) -> BynderSDKClient:
        """Create and return the SDK client."""
        self._sdk = BynderSDKClient(
            domain=self.domain,
            redirect_uri=self.redirect_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token=self.token,
        )
        return self._sdk

    def get_sdk(self) -> BynderSDKClient:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_domain(self) -> str:
        """Get the Bynder domain."""
        return self.domain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class BynderPermanentTokenConfig(BaseModel):
    """Configuration for Bynder client via permanent token.

    Args:
        domain: Bynder portal domain
        permanent_token: The permanent API token
    """

    domain: str
    permanent_token: str

    def create_client(self) -> BynderClientViaPermanentToken:
        return BynderClientViaPermanentToken(
            domain=self.domain,
            permanent_token=self.permanent_token,
        )


class BynderOAuthConfig(BaseModel):
    """Configuration for Bynder client via OAuth 2.0.

    Args:
        domain: Bynder portal domain
        redirect_uri: OAuth redirect URI
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token: OAuth token dict
    """

    domain: str
    redirect_uri: str
    client_id: str
    client_secret: str
    token: dict[str, Any]

    def create_client(self) -> BynderClientViaOAuth:
        return BynderClientViaOAuth(
            domain=self.domain,
            redirect_uri=self.redirect_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token=self.token,
        )


# Backward-compatible alias
BynderTokenConfig = BynderPermanentTokenConfig

# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class BynderAuthConfigModel(BaseModel):
    """Auth section of the Bynder connector configuration from etcd."""

    authType: BynderAuthType = BynderAuthType.TOKEN
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class BynderCredentialsConfigModel(BaseModel):
    """Credentials section of the Bynder connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class BynderConnectorConfig(BaseModel):
    """Top-level Bynder connector configuration from etcd."""

    auth: BynderAuthConfigModel = Field(
        default_factory=BynderAuthConfigModel
    )
    credentials: BynderCredentialsConfigModel = Field(
        default_factory=BynderCredentialsConfigModel
    )
    domain: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class BynderClient(IClient):
    """Builder class for Bynder clients using the official SDK.

    Supports:
    - Permanent token authentication
    - OAuth 2.0 authentication
    """

    def __init__(
        self,
        client: BynderClientViaPermanentToken | BynderClientViaOAuth,
    ) -> None:
        """Initialize with a Bynder SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> BynderClientViaPermanentToken | BynderClientViaOAuth:
        """Return the Bynder SDK wrapper."""
        return self.client

    def get_sdk(self) -> BynderSDKClient:
        """Return the underlying Bynder SDK instance."""
        return self.client.get_sdk()

    @property
    def domain(self) -> str:
        """Return the Bynder domain."""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: BynderPermanentTokenConfig | BynderOAuthConfig,
    ) -> "BynderClient":
        """Build BynderClient with configuration.

        Args:
            config: BynderPermanentTokenConfig or BynderOAuthConfig instance

        Returns:
            BynderClient instance
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
    ) -> "BynderClient":
        """Build BynderClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: For OAuth 2.0 access tokens
        2. TOKEN: For permanent token authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            BynderClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Bynder connector configuration"
                )

            connector_config = BynderConnectorConfig.model_validate(
                raw_config
            )

            domain = connector_config.domain
            if not domain:
                raise ValueError("Bynder domain is required")

            if connector_config.auth.authType == BynderAuthType.OAUTH:
                access_token = (
                    connector_config.credentials.access_token or ""
                )
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/bynder", default=[]
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

                redirect_uri = connector_config.auth.redirectUri or ""
                oauth_cfg = BynderOAuthConfig(
                    domain=domain,
                    redirect_uri=redirect_uri,
                    client_id=client_id,
                    client_secret=client_secret,
                    token={"access_token": access_token},
                )
                wrapper = oauth_cfg.create_client()
                wrapper.get_sdk()
                return cls(wrapper)

            elif connector_config.auth.authType == BynderAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = BynderPermanentTokenConfig(
                    domain=domain, permanent_token=token
                )
                wrapper = token_config.create_client()
                wrapper.get_sdk()
                return cls(wrapper)

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Bynder client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "BynderClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            BynderClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            domain: str = str(toolset_config.get("domain", ""))

            if not domain:
                raise ValueError("Bynder domain not found in toolset config")

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
                        "/services/oauth/bynder", default=[]
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

            redirect_uri: str = str(auth_config.get("redirectUri", ""))
            oauth_cfg = BynderOAuthConfig(
                domain=domain,
                redirect_uri=redirect_uri,
                client_id=client_id,
                client_secret=client_secret,
                token={"access_token": access_token},
            )
            wrapper = oauth_cfg.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build Bynder client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Bynder."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Bynder connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Bynder connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Bynder connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
