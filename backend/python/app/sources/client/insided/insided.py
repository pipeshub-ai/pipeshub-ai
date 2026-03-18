"""InSided (Gainsight Customer Communities) client implementation.

This module provides clients for interacting with the InSided API using either:
1. OAuth2 client_credentials authentication
2. Bearer token authentication

API Reference: https://api.insided.com/docs
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


class InSidedAuthType(str, Enum):
    """Authentication types supported by the InSided connector."""

    CLIENT_CREDENTIALS = "CLIENT_CREDENTIALS"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class InSidedResponse(BaseModel):
    """Standardized InSided API response wrapper.

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


class InSidedRESTClientViaClientCredentials(HTTPClient):
    """InSided REST client via OAuth2 client_credentials flow.

    Fetches a token from the InSided OAuth2 token endpoint using
    client_id and client_secret, then uses that token for API calls.

    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        token_endpoint: Token endpoint URL
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_endpoint: str = "https://api.insided.com/oauth2/token",
    ) -> None:
        # Initialize with empty token; will be set after fetching
        super().__init__("", token_type="Bearer")
        self.base_url = "https://api.insided.com/v2"
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_endpoint = token_endpoint
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class InSidedRESTClientViaToken(HTTPClient):
    """InSided REST client via Bearer token.

    Args:
        token: Bearer token for authentication
    """

    def __init__(self, token: str) -> None:
        super().__init__(token, "Bearer")
        self.base_url = "https://api.insided.com/v2"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class InSidedClientCredentialsConfig(BaseModel):
    """Configuration for InSided client via client_credentials.

    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        token_endpoint: Token endpoint URL
    """

    client_id: str
    client_secret: str
    token_endpoint: str = "https://api.insided.com/oauth2/token"

    def create_client(self) -> InSidedRESTClientViaClientCredentials:
        return InSidedRESTClientViaClientCredentials(
            self.client_id, self.client_secret, self.token_endpoint
        )


class InSidedTokenConfig(BaseModel):
    """Configuration for InSided client via Bearer token.

    Args:
        token: Bearer token
    """

    token: str

    def create_client(self) -> InSidedRESTClientViaToken:
        return InSidedRESTClientViaToken(self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class InSidedAuthConfig(BaseModel):
    """Auth section of the InSided connector configuration from etcd."""

    authType: InSidedAuthType = InSidedAuthType.TOKEN
    clientId: str | None = None
    clientSecret: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class InSidedCredentialsConfig(BaseModel):
    """Credentials section of the InSided connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class InSidedConnectorConfig(BaseModel):
    """Top-level InSided connector configuration from etcd."""

    auth: InSidedAuthConfig = Field(default_factory=InSidedAuthConfig)
    credentials: InSidedCredentialsConfig = Field(
        default_factory=InSidedCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class InSidedClient(IClient):
    """Builder class for InSided clients with different authentication methods.

    Supports:
    - OAuth2 client_credentials authentication
    - Bearer token authentication
    """

    def __init__(
        self,
        client: InSidedRESTClientViaClientCredentials | InSidedRESTClientViaToken,
    ) -> None:
        """Initialize with an InSided client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> InSidedRESTClientViaClientCredentials | InSidedRESTClientViaToken:
        """Return the InSided client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: InSidedClientCredentialsConfig | InSidedTokenConfig,
    ) -> "InSidedClient":
        """Build InSidedClient with configuration.

        Args:
            config: InSidedClientCredentialsConfig or InSidedTokenConfig instance

        Returns:
            InSidedClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "InSidedClient":
        """Build InSidedClient using configuration service.

        Supports two authentication strategies:
        1. CLIENT_CREDENTIALS: For OAuth2 client_credentials flow
        2. TOKEN: For Bearer token authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            InSidedClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get InSided connector configuration"
                )

            connector_config = InSidedConnectorConfig.model_validate(
                raw_config
            )

            if (
                connector_config.auth.authType
                == InSidedAuthType.CLIENT_CREDENTIALS
            ):
                client_id = connector_config.auth.clientId or ""
                client_secret = connector_config.auth.clientSecret or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/insided", default=[]
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
                        "client_id and client_secret required for "
                        "CLIENT_CREDENTIALS auth type"
                    )

                cc_config = InSidedClientCredentialsConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(cc_config.create_client())

            elif connector_config.auth.authType == InSidedAuthType.TOKEN:
                token = (
                    connector_config.auth.token
                    or connector_config.credentials.access_token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = InSidedTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build InSided client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "InSidedClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            InSidedClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = InSidedTokenConfig(token=access_token)
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build InSided client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for InSided."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get InSided connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get InSided connector config: {e}"
            )
            raise ValueError(
                f"Failed to get InSided connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
