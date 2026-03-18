# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""OneLogin client implementation using the official ``onelogin`` SDK.

This module provides a client for interacting with the OneLogin API using
OAuth2 client_credentials authentication via the official SDK.

SDK Reference: https://github.com/onelogin/onelogin-python-sdk
API Reference: https://developers.onelogin.com/api-docs/2/getting-started/dev-overview
"""

import logging
from enum import Enum
from typing import Any, cast

import onelogin
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OneLoginAuthType(str, Enum):
    """Authentication types supported by the OneLogin connector."""

    CLIENT_CREDENTIALS = "CLIENT_CREDENTIALS"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class OneLoginResponse(BaseModel):
    """Standardised OneLogin API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = Field(default=None, description="Response data from the SDK")
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"
        arbitrary_types_allowed = True

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK wrapper client
# ---------------------------------------------------------------------------


class OneLoginClientViaClientCredentials:
    """OneLogin SDK client via OAuth2 client_credentials grant.

    Wraps the official ``onelogin`` SDK. Auto-fetches a token on first use.

    Args:
        region: OneLogin region (e.g. ``us``, ``eu``)
        client_id: OneLogin API client ID
        client_secret: OneLogin API client secret
    """

    def __init__(
        self,
        region: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.region = region
        self.client_id = client_id
        self.client_secret = client_secret

        self._sdk: onelogin.ApiClient | None = None
        self._configuration: onelogin.Configuration | None = None

    def create_client(self) -> onelogin.ApiClient:
        host = f"https://api.{self.region}.onelogin.com"
        self._configuration = onelogin.Configuration(
            host=host,
            username=self.client_id,
            password=self.client_secret,
        )
        self._sdk = onelogin.ApiClient(self._configuration)

        # Auto-fetch token via client_credentials grant
        token_api = onelogin.OAuth2Api(self._sdk)
        response = token_api.generate_token(
            onelogin.GenerateTokenRequest(grant_type="client_credentials")  # type: ignore[arg-type]
        )
        self._configuration.access_token = response.access_token

        return self._sdk

    def get_sdk(self) -> onelogin.ApiClient:
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return f"https://api.{self.region}.onelogin.com"


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class OneLoginClientCredentialsConfig(BaseModel):
    """Configuration for OneLogin client via client_credentials.

    Args:
        client_id: OneLogin API client ID
        client_secret: OneLogin API client secret
        region: OneLogin region (e.g. ``us``, ``eu``)
    """

    client_id: str
    client_secret: str
    region: str = "us"

    def create_client(self) -> OneLoginClientViaClientCredentials:
        return OneLoginClientViaClientCredentials(
            region=self.region,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class OneLoginAuthConfig(BaseModel):
    """Auth section of the OneLogin connector configuration from etcd."""

    authType: OneLoginAuthType = OneLoginAuthType.CLIENT_CREDENTIALS
    clientId: str | None = None
    clientSecret: str | None = None
    region: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class OneLoginCredentialsConfig(BaseModel):
    """Credentials section of the OneLogin connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class OneLoginConnectorConfig(BaseModel):
    """Top-level OneLogin connector configuration from etcd."""

    auth: OneLoginAuthConfig = Field(default_factory=OneLoginAuthConfig)
    credentials: OneLoginCredentialsConfig = Field(
        default_factory=OneLoginCredentialsConfig
    )
    region: str = "us"

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class OneLoginClient(IClient):
    """Builder class for OneLogin clients.

    Supports:
    - OAuth2 client_credentials grant authentication
    """

    def __init__(self, client: OneLoginClientViaClientCredentials) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> OneLoginClientViaClientCredentials:
        return self.client

    def get_sdk(self) -> onelogin.ApiClient:
        return self.client.get_sdk()

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: OneLoginClientCredentialsConfig,
    ) -> "OneLoginClient":
        client = config.create_client()
        _ = client.get_sdk()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "OneLoginClient":
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get OneLogin connector configuration"
                )

            connector_config = OneLoginConnectorConfig.model_validate(
                raw_config
            )

            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""
            region = (
                connector_config.auth.region
                or connector_config.region
                or "us"
            )

            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not (client_id and client_secret):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/onelogin", default=[]
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

            if not (client_id and client_secret):
                raise ValueError(
                    "client_id and client_secret are required "
                    "for OneLogin authentication"
                )

            cc_config = OneLoginClientCredentialsConfig(
                client_id=client_id,
                client_secret=client_secret,
                region=region,
            )
            return cls(cc_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build OneLogin client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "OneLoginClient":
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))
            region: str = str(auth_config.get("region", "us"))

            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/onelogin", default=[]
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

            if not (client_id and client_secret):
                raise ValueError(
                    "client_id and client_secret not found in toolset config"
                )

            cc_config = OneLoginClientCredentialsConfig(
                client_id=client_id,
                client_secret=client_secret,
                region=region,
            )
            return cls(cc_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build OneLogin client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get OneLogin connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get OneLogin connector config: {e}"
            )
            raise ValueError(
                f"Failed to get OneLogin connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
