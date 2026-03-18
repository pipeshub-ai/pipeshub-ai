"""Marketo client implementation.

This module provides a client for interacting with the Marketo REST API using
OAuth 2.0 Client Credentials authentication.

The client automatically fetches an access token from the Marketo identity
endpoint using client_id and client_secret before making API calls.

Authentication Reference: https://developers.marketo.com/rest-api/authentication/
API Reference: https://developers.marketo.com/rest-api/
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class MarketoResponse(BaseModel):
    """Standardized Marketo API response wrapper.

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


class MarketoRESTClientViaClientCredentials(HTTPClient):
    """Marketo REST client via OAuth 2.0 Client Credentials.

    Automatically fetches an access token from the Marketo identity endpoint
    using the client_id and client_secret on first use via
    ensure_authenticated().

    Args:
        munchkin_id: Marketo Munchkin Account ID (e.g. "123-ABC-456")
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    def __init__(
        self,
        munchkin_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        # Initialize with empty token; will be set after authentication
        super().__init__("", token_type="Bearer")
        self.munchkin_id = munchkin_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = f"https://{munchkin_id}.mktorest.com/rest"
        self._identity_url = (
            f"https://{munchkin_id}.mktorest.com/identity/oauth/token"
        )
        self._authenticated = False
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via client_credentials grant if not already authenticated.

        Posts to the Marketo identity token endpoint with grant_type=client_credentials,
        client_id, and client_secret as query parameters.
        """
        if self._authenticated:
            return

        token_url = (
            f"{self._identity_url}"
            f"?grant_type=client_credentials"
            f"&client_id={self.client_id}"
            f"&client_secret={self.client_secret}"
        )

        token_request = HTTPRequest(
            url=token_url,
            method="GET",
            headers={"Content-Type": "application/json"},
        )

        response = await self.execute(token_request)  # type: ignore[reportUnknownMemberType]
        response_data = response.json()

        access_token = response_data.get("access_token")
        if not access_token:
            raise ValueError(
                "Failed to obtain access token from Marketo identity endpoint: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class MarketoClientCredentialsConfig(BaseModel):
    """Configuration for Marketo client via OAuth 2.0 Client Credentials.

    Args:
        munchkin_id: Marketo Munchkin Account ID (e.g. "123-ABC-456")
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    munchkin_id: str
    client_id: str
    client_secret: str

    def create_client(self) -> MarketoRESTClientViaClientCredentials:
        return MarketoRESTClientViaClientCredentials(
            self.munchkin_id,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class MarketoAuthConfig(BaseModel):
    """Auth section of the Marketo connector configuration from etcd."""

    munchkinId: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class MarketoCredentialsConfig(BaseModel):
    """Credentials section of the Marketo connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class MarketoConnectorConfig(BaseModel):
    """Top-level Marketo connector configuration from etcd."""

    auth: MarketoAuthConfig = Field(default_factory=MarketoAuthConfig)
    credentials: MarketoCredentialsConfig = Field(
        default_factory=MarketoCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class MarketoClient(IClient):
    """Builder class for Marketo clients.

    Supports:
    - OAuth 2.0 Client Credentials authentication (auto-fetches token
      using munchkin_id, client_id, and client_secret)
    """

    def __init__(
        self,
        client: MarketoRESTClientViaClientCredentials,
    ) -> None:
        """Initialize with a Marketo client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> MarketoRESTClientViaClientCredentials:
        """Return the Marketo client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: MarketoClientCredentialsConfig,
    ) -> "MarketoClient":
        """Build MarketoClient with configuration.

        Args:
            config: MarketoClientCredentialsConfig instance

        Returns:
            MarketoClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "MarketoClient":
        """Build MarketoClient using configuration service.

        Uses client_credentials grant with munchkin_id, client_id,
        and client_secret from the connector configuration.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            MarketoClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get Marketo connector configuration")

            connector_config = MarketoConnectorConfig.model_validate(raw_config)

            munchkin_id = connector_config.auth.munchkinId or ""
            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""

            # Try shared OAuth config if credentials are missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not (client_id and client_secret):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/marketo", default=[]
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
                            munchkin_id = str(
                                shared.get("munchkinId")
                                or shared.get("munchkin_id")
                                or munchkin_id
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

            if not (munchkin_id and client_id and client_secret):
                raise ValueError(
                    "munchkin_id, client_id, and client_secret are required "
                    "for Marketo client credentials auth"
                )

            creds_config = MarketoClientCredentialsConfig(
                munchkin_id=munchkin_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(creds_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Marketo client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "MarketoClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            MarketoClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            munchkin_id: str = str(auth_config.get("munchkinId", ""))
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
                        "/services/oauth/marketo", default=[]
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
                            munchkin_id = str(
                                shared.get("munchkinId")
                                or shared.get("munchkin_id")
                                or munchkin_id
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

            if not (munchkin_id and client_id and client_secret):
                raise ValueError(
                    "munchkin_id, client_id, and client_secret are required "
                    "in toolset config for Marketo"
                )

            creds_config = MarketoClientCredentialsConfig(
                munchkin_id=munchkin_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(creds_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Marketo client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Marketo."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Marketo connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Marketo connector config: {e}")
            raise ValueError(
                f"Failed to get Marketo connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
