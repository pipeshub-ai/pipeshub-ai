"""Quip client implementation.

This module provides clients for interacting with the Quip API using either:
1. OAuth 2.0 access token authentication
2. Personal Access Token (Bearer)

Authentication Reference: https://quip.com/dev/automation/documentation
API Base URL: https://platform.quip.com/1
OAuth Auth Endpoint: https://platform.quip.com/1/oauth/login
OAuth Token Endpoint: https://platform.quip.com/1/oauth/access_token
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


class QuipAuthType(str, Enum):
    """Authentication types supported by the Quip connector."""

    OAUTH = "OAUTH"
    PERSONAL_TOKEN = "PERSONAL_TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class QuipResponse(BaseModel):
    """Standardized Quip API response wrapper.

    The data field supports JSON responses (dict/list) and binary file
    downloads (bytes). When serializing to dict/JSON, binary data is
    automatically base64-encoded.
    """

    success: bool = Field(
        ..., description="Whether the request was successful"
    )
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None,
        description="Response data (JSON) or file content (bytes)",
    )
    error: str | None = Field(
        default=None, description="Error message if failed"
    )
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


class QuipRESTClientViaToken(HTTPClient):
    """Quip REST client via Personal Access Token.

    Personal tokens are passed as Bearer tokens in the Authorization header.

    Args:
        token: The personal access token
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        token: str,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(token, token_type="Bearer", timeout=timeout)
        self.base_url = "https://platform.quip.com/1"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class QuipRESTClientViaOAuth(HTTPClient):
    """Quip REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        access_token: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(access_token, "Bearer", timeout=timeout)
        self.base_url = "https://platform.quip.com/1"
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class QuipTokenConfig(BaseModel):
    """Configuration for Quip client via Personal Access Token.

    Args:
        token: The personal access token
    """

    token: str

    def create_client(self) -> QuipRESTClientViaToken:
        return QuipRESTClientViaToken(self.token)


class QuipOAuthConfig(BaseModel):
    """Configuration for Quip client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> QuipRESTClientViaOAuth:
        return QuipRESTClientViaOAuth(
            self.access_token,
            self.client_id,
            self.client_secret,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class QuipAuthConfig(BaseModel):
    """Auth section of the Quip connector configuration from etcd."""

    authType: QuipAuthType = QuipAuthType.PERSONAL_TOKEN
    apiToken: str | None = None
    token: str | None = None
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class QuipCredentialsConfig(BaseModel):
    """Credentials section of the Quip connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class QuipConnectorConfig(BaseModel):
    """Top-level Quip connector configuration from etcd."""

    auth: QuipAuthConfig = Field(default_factory=QuipAuthConfig)
    credentials: QuipCredentialsConfig = Field(
        default_factory=QuipCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class QuipClient(IClient):
    """Builder class for Quip clients with different authentication methods.

    Supports:
    - Personal Access Token authentication
    - OAuth 2.0 access token authentication
    """

    def __init__(
        self,
        client: QuipRESTClientViaToken | QuipRESTClientViaOAuth,
    ) -> None:
        """Initialize with a Quip client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> QuipRESTClientViaToken | QuipRESTClientViaOAuth:
        """Return the Quip client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: QuipTokenConfig | QuipOAuthConfig,
    ) -> "QuipClient":
        """Build QuipClient with configuration.

        Args:
            config: QuipTokenConfig or QuipOAuthConfig instance

        Returns:
            QuipClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "QuipClient":
        """Build QuipClient using configuration service.

        Supports two authentication strategies:
        1. PERSONAL_TOKEN: For personal access tokens
        2. OAUTH: For OAuth 2.0 access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            QuipClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Quip connector configuration"
                )

            connector_config = QuipConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == QuipAuthType.OAUTH:
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
                            "/services/oauth/quip", default=[]
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

                oauth_cfg = QuipOAuthConfig(
                    access_token=access_token,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_cfg.create_client())

            elif (
                connector_config.auth.authType
                == QuipAuthType.PERSONAL_TOKEN
            ):
                token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Personal token required for PERSONAL_TOKEN auth type"
                    )

                token_config = QuipTokenConfig(token=token)
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Quip client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Quip."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Quip connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Quip connector config: {e}")
            raise ValueError(
                f"Failed to get Quip connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
