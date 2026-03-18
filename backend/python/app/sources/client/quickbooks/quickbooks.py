"""QuickBooks Online client implementation.

This module provides a client for interacting with the QuickBooks Online API
using OAuth 2.0 (authorization code flow).

The base URL includes the company_id:
https://quickbooks.api.intuit.com/v3/company/{company_id}

OAuth Auth Endpoint: https://appcenter.intuit.com/connect/oauth2
OAuth Token Endpoint: https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer
Auth Method: "body"

Authentication Reference: https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization
API Reference: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class QuickBooksResponse(BaseModel):
    """Standardized QuickBooks API response wrapper.

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
# REST client class
# ---------------------------------------------------------------------------


class QuickBooksRESTClientViaOAuth(HTTPClient):
    """QuickBooks Online REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.
    The base URL includes the company_id for all API operations.

    Args:
        access_token: The OAuth access token
        company_id: The QuickBooks company (realm) ID
        client_id: OAuth client ID (for token refresh)
        client_secret: OAuth client secret (for token refresh)
    """

    def __init__(
        self,
        access_token: str,
        company_id: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = (
            f"https://quickbooks.api.intuit.com/v3/company/{company_id}"
        )
        self.company_id = company_id
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including company ID."""
        return self.base_url

    def get_company_id(self) -> str:
        """Get the company (realm) ID."""
        return self.company_id


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class QuickBooksOAuthConfig(BaseModel):
    """Configuration for QuickBooks client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        company_id: The QuickBooks company (realm) ID
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    company_id: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> QuickBooksRESTClientViaOAuth:
        return QuickBooksRESTClientViaOAuth(
            self.access_token,
            self.company_id,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class QuickBooksAuthConfig(BaseModel):
    """Auth section of the QuickBooks connector configuration from etcd."""

    companyId: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class QuickBooksCredentialsConfig(BaseModel):
    """Credentials section of the QuickBooks connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class QuickBooksConnectorConfig(BaseModel):
    """Top-level QuickBooks connector configuration from etcd."""

    auth: QuickBooksAuthConfig = Field(default_factory=QuickBooksAuthConfig)
    credentials: QuickBooksCredentialsConfig = Field(
        default_factory=QuickBooksCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class QuickBooksClient(IClient):
    """Builder class for QuickBooks Online clients.

    Supports:
    - OAuth 2.0 access token authentication (authorization code flow)
    """

    def __init__(
        self,
        client: QuickBooksRESTClientViaOAuth,
    ) -> None:
        """Initialize with a QuickBooks client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> QuickBooksRESTClientViaOAuth:
        """Return the QuickBooks client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @property
    def company_id(self) -> str:
        """Return the company (realm) ID."""
        return self.client.get_company_id()

    @classmethod
    def build_with_config(
        cls,
        config: QuickBooksOAuthConfig,
    ) -> "QuickBooksClient":
        """Build QuickBooksClient with configuration.

        Args:
            config: QuickBooksOAuthConfig instance

        Returns:
            QuickBooksClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "QuickBooksClient":
        """Build QuickBooksClient using configuration service.

        Supports OAuth 2.0 authentication (authorization code flow).

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            QuickBooksClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get QuickBooks connector configuration"
                )

            connector_config = QuickBooksConnectorConfig.model_validate(
                raw_config
            )

            access_token = connector_config.credentials.access_token or ""
            company_id = connector_config.auth.companyId or ""
            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""

            if not company_id:
                raise ValueError("Company ID (realm ID) is required")

            # Try shared OAuth config if credentials are missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not (client_id and client_secret):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/quickbooks", default=[]
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

            oauth_cfg = QuickBooksOAuthConfig(
                access_token=access_token,
                company_id=company_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build QuickBooks client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "QuickBooksClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            QuickBooksClient instance
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

            company_id: str = str(auth_config.get("companyId", ""))
            if not company_id:
                raise ValueError(
                    "Company ID not found in toolset config"
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
                        "/services/oauth/quickbooks", default=[]
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

            oauth_cfg = QuickBooksOAuthConfig(
                access_token=access_token,
                company_id=company_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build QuickBooks client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for QuickBooks."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get QuickBooks connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get QuickBooks connector config: {e}"
            )
            raise ValueError(
                f"Failed to get QuickBooks connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
