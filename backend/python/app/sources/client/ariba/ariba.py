"""SAP Ariba client implementation.

This module provides a client for interacting with the SAP Ariba API using:
1. OAuth 2.0 client_credentials flow (auto token fetch via Basic Auth)

Token Endpoint: https://api.ariba.com/v2/oauth/token
   (grant_type=client_credentials, Basic Auth header with client_id:client_secret)
API Base URL: https://openapi.ariba.com/api
"""

import base64
import json
import logging
from typing import Any, cast

import httpx  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class AribaResponse(BaseModel):
    """Standardized SAP Ariba API response wrapper.

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


class AribaRESTClientViaClientCredentials(HTTPClient):
    """SAP Ariba REST client via OAuth 2.0 client_credentials.

    Automatically fetches an access token from the SAP Ariba token endpoint
    using Basic Auth (client_id:client_secret) before making API requests.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token_endpoint: Token endpoint URL
            (default: https://api.ariba.com/v2/oauth/token)
        base_url: API base URL
            (default: https://openapi.ariba.com/api)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str = "https://api.ariba.com/v2/oauth/token",
        base_url: str = "https://openapi.ariba.com/api",
    ) -> None:
        # Initialize with empty token; will be set after fetching
        super().__init__("", token_type="Bearer")
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = token_endpoint
        self._access_token: str | None = None
        self.headers["Content-Type"] = "application/json"

    async def _fetch_token(self) -> str:
        """Fetch an access token using client_credentials grant.

        Uses Basic Auth header with client_id:client_secret to authenticate
        at the token endpoint.

        Returns:
            Access token string.
        """
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode("utf-8")

        async with httpx.AsyncClient() as client:  # type: ignore[reportUnknownMemberType]
            response = await client.post(  # type: ignore[reportUnknownMemberType]
                self.token_endpoint,
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()  # type: ignore[reportUnknownMemberType]
            token_data: dict[str, Any] = response.json()  # type: ignore[reportUnknownMemberType]
            access_token: str = str(token_data.get("access_token", ""))  # type: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            if not access_token:
                raise ValueError("No access_token in token response")
            return access_token

    async def ensure_token(self) -> None:
        """Ensure a valid access token is set in headers."""
        if not self._access_token:
            self._access_token = await self._fetch_token()
            self.headers["Authorization"] = f"Bearer {self._access_token}"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class AribaClientCredentialsConfig(BaseModel):
    """Configuration for SAP Ariba client via client_credentials.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        token_endpoint: Token endpoint URL
        base_url: API base URL
    """

    client_id: str
    client_secret: str
    token_endpoint: str = "https://api.ariba.com/v2/oauth/token"
    base_url: str = "https://openapi.ariba.com/api"

    def create_client(self) -> AribaRESTClientViaClientCredentials:
        return AribaRESTClientViaClientCredentials(
            self.client_id,
            self.client_secret,
            self.token_endpoint,
            self.base_url,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class AribaAuthConfigModel(BaseModel):
    """Auth section of the Ariba connector configuration from etcd."""

    clientId: str | None = None
    clientSecret: str | None = None
    tokenEndpoint: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class AribaConnectorConfig(BaseModel):
    """Top-level Ariba connector configuration from etcd."""

    auth: AribaAuthConfigModel = Field(default_factory=AribaAuthConfigModel)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class AribaClient(IClient):
    """Builder class for SAP Ariba clients.

    Supports:
    - OAuth 2.0 client_credentials flow (auto token fetch)
    """

    def __init__(
        self,
        client: AribaRESTClientViaClientCredentials,
    ) -> None:
        """Initialize with an Ariba client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> AribaRESTClientViaClientCredentials:
        """Return the Ariba client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: AribaClientCredentialsConfig,
    ) -> "AribaClient":
        """Build AribaClient with configuration.

        Args:
            config: AribaClientCredentialsConfig instance

        Returns:
            AribaClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "AribaClient":
        """Build AribaClient using configuration service.

        Uses client_credentials OAuth flow.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            AribaClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Ariba connector configuration"
                )

            connector_config = AribaConnectorConfig.model_validate(raw_config)

            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""

            if not (client_id and client_secret):
                raise ValueError(
                    "client_id and client_secret required for Ariba"
                )

            token_endpoint = (
                connector_config.auth.tokenEndpoint
                or "https://api.ariba.com/v2/oauth/token"
            )
            base_url = (
                connector_config.auth.baseUrl
                or "https://openapi.ariba.com/api"
            )

            config = AribaClientCredentialsConfig(
                client_id=client_id,
                client_secret=client_secret,
                token_endpoint=token_endpoint,
                base_url=base_url,
            )
            return cls(config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Ariba client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "AribaClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused for Ariba)

        Returns:
            AribaClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            if not (client_id and client_secret):
                raise ValueError(
                    "client_id and client_secret not found in toolset config"
                )

            token_endpoint: str = str(
                auth_config.get(
                    "tokenEndpoint",
                    "https://api.ariba.com/v2/oauth/token",
                )
            )
            base_url: str = str(
                auth_config.get(
                    "baseUrl",
                    "https://openapi.ariba.com/api",
                )
            )

            config = AribaClientCredentialsConfig(
                client_id=client_id,
                client_secret=client_secret,
                token_endpoint=token_endpoint,
                base_url=base_url,
            )
            return cls(config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Ariba client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Ariba."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Ariba connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Ariba connector config: {e}")
            raise ValueError(
                f"Failed to get Ariba connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
