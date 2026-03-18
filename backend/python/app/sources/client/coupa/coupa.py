"""Coupa client implementation.

This module provides clients for interacting with the Coupa API using either:
1. API Key authentication (X-COUPA-API-KEY header)
2. OAuth 2.0 client_credentials flow

Token Endpoint: https://{instance}.coupahost.com/oauth2/token
API Base URL: https://{instance}.coupahost.com/api
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

import httpx  # type: ignore
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CoupaAuthType(str, Enum):
    """Authentication types supported by the Coupa connector."""

    API_KEY = "API_KEY"
    OAUTH = "OAUTH"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class CoupaResponse(BaseModel):
    """Standardized Coupa API response wrapper.

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


class CoupaRESTClientViaApiKey(HTTPClient):
    """Coupa REST client via API Key (X-COUPA-API-KEY header).

    Args:
        api_key: The Coupa API key
        instance: The Coupa instance name (e.g. 'mycompany')
    """

    def __init__(self, api_key: str, instance: str) -> None:
        # Initialize with empty token; we use custom header
        super().__init__("", token_type="Bearer")
        self.base_url = f"https://{instance}.coupahost.com/api"
        self.instance = instance
        # Remove default Authorization header, use X-COUPA-API-KEY instead
        _ = self.headers.pop("Authorization", None)
        self.headers["X-COUPA-API-KEY"] = api_key
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class CoupaRESTClientViaOAuth(HTTPClient):
    """Coupa REST client via OAuth 2.0 client_credentials.

    Automatically fetches an access token from the Coupa token endpoint.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        instance: The Coupa instance name (e.g. 'mycompany')
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        instance: str,
    ) -> None:
        super().__init__("", token_type="Bearer")
        self.base_url = f"https://{instance}.coupahost.com/api"
        self.instance = instance
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = (
            f"https://{instance}.coupahost.com/oauth2/token"
        )
        self._access_token: str | None = None
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    async def _fetch_token(self) -> str:
        """Fetch an access token using client_credentials grant.

        Returns:
            Access token string.
        """
        async with httpx.AsyncClient() as client:  # type: ignore[reportUnknownMemberType]
            response = await client.post(  # type: ignore[reportUnknownMemberType]
                self.token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "core.common.read",
                },
                headers={
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


class CoupaApiKeyConfig(BaseModel):
    """Configuration for Coupa client via API Key.

    Args:
        api_key: The Coupa API key
        instance: The Coupa instance name
    """

    api_key: str
    instance: str

    def create_client(self) -> CoupaRESTClientViaApiKey:
        return CoupaRESTClientViaApiKey(self.api_key, self.instance)


class CoupaOAuthConfig(BaseModel):
    """Configuration for Coupa client via OAuth 2.0 client_credentials.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        instance: The Coupa instance name
    """

    client_id: str
    client_secret: str
    instance: str

    def create_client(self) -> CoupaRESTClientViaOAuth:
        return CoupaRESTClientViaOAuth(
            self.client_id,
            self.client_secret,
            self.instance,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class CoupaAuthConfigModel(BaseModel):
    """Auth section of the Coupa connector configuration from etcd."""

    authType: CoupaAuthType = CoupaAuthType.API_KEY
    apiKey: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    instance: str | None = None

    class Config:
        extra = "allow"


class CoupaConnectorConfig(BaseModel):
    """Top-level Coupa connector configuration from etcd."""

    auth: CoupaAuthConfigModel = Field(default_factory=CoupaAuthConfigModel)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class CoupaClient(IClient):
    """Builder class for Coupa clients with different authentication methods.

    Supports:
    - API Key authentication (X-COUPA-API-KEY header)
    - OAuth 2.0 client_credentials flow
    """

    def __init__(
        self,
        client: CoupaRESTClientViaApiKey | CoupaRESTClientViaOAuth,
    ) -> None:
        """Initialize with a Coupa client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> CoupaRESTClientViaApiKey | CoupaRESTClientViaOAuth:
        """Return the Coupa client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: CoupaApiKeyConfig | CoupaOAuthConfig,
    ) -> "CoupaClient":
        """Build CoupaClient with configuration.

        Args:
            config: CoupaApiKeyConfig or CoupaOAuthConfig instance

        Returns:
            CoupaClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "CoupaClient":
        """Build CoupaClient using configuration service.

        Supports two authentication strategies:
        1. API_KEY: API Key via X-COUPA-API-KEY header
        2. OAUTH: OAuth 2.0 client_credentials

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            CoupaClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Coupa connector configuration"
                )

            connector_config = CoupaConnectorConfig.model_validate(raw_config)

            instance = connector_config.auth.instance or ""
            if not instance:
                raise ValueError("Coupa instance name is required")

            if connector_config.auth.authType == CoupaAuthType.API_KEY:
                api_key = connector_config.auth.apiKey or ""
                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )

                config = CoupaApiKeyConfig(
                    api_key=api_key,
                    instance=instance,
                )
                return cls(config.create_client())

            elif connector_config.auth.authType == CoupaAuthType.OAUTH:
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                if not (client_id and client_secret):
                    raise ValueError(
                        "client_id and client_secret required for OAuth auth type"
                    )

                oauth_config = CoupaOAuthConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    instance=instance,
                )
                return cls(oauth_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Coupa client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "CoupaClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused for Coupa)

        Returns:
            CoupaClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            instance: str = str(auth_config.get("instance", ""))
            if not instance:
                raise ValueError(
                    "Coupa instance name not found in toolset config"
                )

            auth_type = str(auth_config.get("authType", "API_KEY"))

            if auth_type == "API_KEY":
                api_key: str = str(auth_config.get("apiKey", ""))
                if not api_key:
                    raise ValueError(
                        "API key not found in toolset config"
                    )
                config: CoupaApiKeyConfig | CoupaOAuthConfig = (
                    CoupaApiKeyConfig(api_key=api_key, instance=instance)
                )
            else:
                client_id: str = str(auth_config.get("clientId", ""))
                client_secret: str = str(auth_config.get("clientSecret", ""))
                if not (client_id and client_secret):
                    raise ValueError(
                        "client_id and client_secret not found in toolset config"
                    )
                config = CoupaOAuthConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    instance=instance,
                )

            return cls(config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Coupa client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Coupa."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Coupa connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Coupa connector config: {e}")
            raise ValueError(
                f"Failed to get Coupa connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
