"""IAP SSO (Google Cloud Identity-Aware Proxy) client implementation.

This module provides clients for interacting with the Google Cloud IAP API
using either:
1. OAuth2 (Google OAuth authorization code flow)
2. Pre-generated Bearer token (e.g. from a Service Account)

OAuth Auth Endpoint: https://accounts.google.com/o/oauth2/v2/auth
Token Endpoint: https://oauth2.googleapis.com/token
Auth Method: body
Scopes: https://www.googleapis.com/auth/cloud-platform
API Base URL: https://iap.googleapis.com/v1
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


class IAPSSOAuthType(str, Enum):
    """Authentication types supported by the IAP SSO connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class IAPSSOResponse(BaseModel):
    """Standardized IAP SSO API response wrapper.

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

_BASE_URL = "https://iap.googleapis.com/v1"


class IAPSSORESTClientViaOAuth(HTTPClient):
    """IAP SSO REST client via OAuth 2.0 authorization code flow.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    Supports token refresh via client_id and client_secret.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
        redirect_uri: OAuth redirect URI
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = _BASE_URL
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class IAPSSORESTClientViaToken(HTTPClient):
    """IAP SSO REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token (e.g. from a
    Service Account) passed directly in the Authorization header.

    Args:
        token: The pre-generated Bearer token
    """

    def __init__(
        self,
        token: str,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = _BASE_URL
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class IAPSSOOAuthConfig(BaseModel):
    """Configuration for IAP SSO client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
        redirect_uri: OAuth redirect URI
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None

    def create_client(self) -> IAPSSORESTClientViaOAuth:
        return IAPSSORESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
            self.redirect_uri,
        )


class IAPSSOTokenConfig(BaseModel):
    """Configuration for IAP SSO client via Bearer token.

    Args:
        token: The pre-generated Bearer token
    """

    token: str

    def create_client(self) -> IAPSSORESTClientViaToken:
        return IAPSSORESTClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class IAPSSOAuthConfig(BaseModel):
    """Auth section of the IAP SSO connector configuration from etcd."""

    authType: IAPSSOAuthType = IAPSSOAuthType.OAUTH
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class IAPSSOCredentialsConfig(BaseModel):
    """Credentials section of the IAP SSO connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class IAPSSOConnectorConfig(BaseModel):
    """Top-level IAP SSO connector configuration from etcd."""

    auth: IAPSSOAuthConfig = Field(default_factory=IAPSSOAuthConfig)
    credentials: IAPSSOCredentialsConfig = Field(
        default_factory=IAPSSOCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class IAPSSOClient(IClient):
    """Builder class for IAP SSO clients.

    Supports:
    - OAuth 2.0 authorization code flow
    - Pre-generated Bearer token (Service Account)
    """

    def __init__(
        self,
        client: IAPSSORESTClientViaOAuth | IAPSSORESTClientViaToken,
    ) -> None:
        """Initialize with an IAP SSO client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> IAPSSORESTClientViaOAuth | IAPSSORESTClientViaToken:
        """Return the IAP SSO client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: IAPSSOOAuthConfig | IAPSSOTokenConfig,
    ) -> "IAPSSOClient":
        """Build IAPSSOClient with configuration.

        Args:
            config: IAPSSOOAuthConfig or IAPSSOTokenConfig instance

        Returns:
            IAPSSOClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "IAPSSOClient":
        """Build IAPSSOClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            IAPSSOClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get IAP SSO connector configuration"
                )

            connector_config = IAPSSOConnectorConfig.model_validate(
                raw_config
            )

            if connector_config.auth.authType == IAPSSOAuthType.OAUTH:
                access_token = (
                    connector_config.credentials.access_token or ""
                )
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""
                redirect_uri = connector_config.auth.redirectUri or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/iapsso", default=[]
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
                                redirect_uri = str(
                                    shared.get("redirectUri")
                                    or shared.get("redirect_uri")
                                    or redirect_uri
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

                oauth_cfg = IAPSSOOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                )
                return cls(oauth_cfg.create_client())

            elif connector_config.auth.authType == IAPSSOAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = IAPSSOTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                "Failed to build IAP SSO client from services: "
                f"{str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "IAPSSOClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth

        Returns:
            IAPSSOClient instance
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
            redirect_uri: str = str(auth_config.get("redirectUri", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/iapsso", default=[]
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
                            redirect_uri = str(
                                shared.get("redirectUri")
                                or shared.get("redirect_uri")
                                or redirect_uri
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            oauth_cfg = IAPSSOOAuthConfig(
                access_token=access_token,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                "Failed to build IAP SSO client from toolset: "
                f"{str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for IAP SSO."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    "Failed to get IAP SSO connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get IAP SSO connector config: {e}"
            )
            raise ValueError(
                "Failed to get IAP SSO connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
