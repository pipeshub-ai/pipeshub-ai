"""Docebo client implementation.

This module provides clients for interacting with the Docebo API using either:
1. OAuth2 client_credentials grant (auto-fetches token)
2. Pre-generated Bearer Token

Authentication Reference: https://www.docebo.com/knowledge-base/docebo-api-authentication/
API Reference: https://www.docebo.com/knowledge-base/docebo-api/
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
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DoceboAuthType(str, Enum):
    """Authentication types supported by the Docebo connector."""

    CLIENT_CREDENTIALS = "CLIENT_CREDENTIALS"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DoceboResponse(BaseModel):
    """Standardized Docebo API response wrapper.

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


class DoceboRESTClientViaClientCredentials(HTTPClient):
    """Docebo REST client via OAuth2 client_credentials grant.

    Automatically fetches an access token from the Docebo OAuth2 token
    endpoint on first use via ensure_authenticated().

    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        domain: The Docebo domain (e.g., "mycompany" for
            mycompany.docebosaas.com)
        base_url: Optional full base URL override
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        domain: str,
        base_url: str | None = None,
    ) -> None:
        # Initialize with empty token; will be set after authentication
        super().__init__("", token_type="Bearer")
        self.base_url = base_url or f"https://{domain}.docebosaas.com/api"
        self.domain = domain
        self.client_id = client_id
        self.client_secret = client_secret
        self._authenticated = False
        self.token_endpoint = (
            f"https://{domain}.docebosaas.com/oauth2/token"
        )
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def ensure_authenticated(self) -> None:
        """Fetch an access token via client_credentials grant.

        Posts to the Docebo token endpoint with grant_type=client_credentials
        and client_id/client_secret in the request body.
        """
        if self._authenticated:
            return

        token_request = HTTPRequest(
            url=self.token_endpoint,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )

        response = await self.execute(token_request)  # type: ignore[reportUnknownMemberType]
        response_data = response.json()

        access_token = response_data.get("access_token")
        if not access_token:
            raise ValueError(
                "Failed to obtain access token from Docebo OAuth2: "
                f"{response_data}"
            )

        self.headers["Authorization"] = f"Bearer {access_token}"
        self._authenticated = True


class DoceboRESTClientViaToken(HTTPClient):
    """Docebo REST client via pre-generated Bearer Token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        token: The Bearer token
        domain: The Docebo domain (e.g., "mycompany" for
            mycompany.docebosaas.com)
        base_url: Optional full base URL override
    """

    def __init__(
        self,
        token: str,
        domain: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = base_url or f"https://{domain}.docebosaas.com/api"
        self.domain = domain
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DoceboClientCredentialsConfig(BaseModel):
    """Configuration for Docebo client via OAuth2 client_credentials.

    Args:
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        domain: The Docebo domain
        base_url: Optional full base URL override
    """

    client_id: str
    client_secret: str
    domain: str
    base_url: str | None = None

    def create_client(self) -> DoceboRESTClientViaClientCredentials:
        return DoceboRESTClientViaClientCredentials(
            self.client_id,
            self.client_secret,
            self.domain,
            self.base_url,
        )


class DoceboTokenConfig(BaseModel):
    """Configuration for Docebo client via Bearer Token.

    Args:
        token: The Bearer token
        domain: The Docebo domain
        base_url: Optional full base URL override
    """

    token: str
    domain: str
    base_url: str | None = None

    def create_client(self) -> DoceboRESTClientViaToken:
        return DoceboRESTClientViaToken(
            self.token, self.domain, self.base_url
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DoceboAuthConfig(BaseModel):
    """Auth section of the Docebo connector configuration from etcd."""

    authType: DoceboAuthType = DoceboAuthType.CLIENT_CREDENTIALS
    clientId: str | None = None
    clientSecret: str | None = None
    token: str | None = None
    domain: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class DoceboCredentialsConfig(BaseModel):
    """Credentials section of the Docebo connector configuration."""

    access_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    class Config:
        extra = "allow"


class DoceboConnectorConfig(BaseModel):
    """Top-level Docebo connector configuration from etcd."""

    auth: DoceboAuthConfig = Field(default_factory=DoceboAuthConfig)
    credentials: DoceboCredentialsConfig = Field(
        default_factory=DoceboCredentialsConfig
    )
    domain: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DoceboClient(IClient):
    """Builder class for Docebo clients with different authentication methods.

    Supports:
    - OAuth2 client_credentials grant (auto-fetches token)
    - Pre-generated Bearer Token
    """

    def __init__(
        self,
        client: (
            DoceboRESTClientViaClientCredentials | DoceboRESTClientViaToken
        ),
    ) -> None:
        """Initialize with a Docebo client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> DoceboRESTClientViaClientCredentials | DoceboRESTClientViaToken:
        """Return the Docebo client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: DoceboClientCredentialsConfig | DoceboTokenConfig,
    ) -> "DoceboClient":
        """Build DoceboClient with configuration.

        Args:
            config: DoceboClientCredentialsConfig or DoceboTokenConfig instance

        Returns:
            DoceboClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "DoceboClient":
        """Build DoceboClient using configuration service.

        Supports two authentication strategies:
        1. CLIENT_CREDENTIALS: For OAuth2 client_credentials grant
        2. TOKEN: For pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DoceboClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Docebo connector configuration"
                )

            connector_config = DoceboConnectorConfig.model_validate(
                raw_config
            )
            domain = (
                connector_config.auth.domain or connector_config.domain or ""
            )
            base_url = connector_config.auth.baseUrl or None

            if not domain and not base_url:
                raise ValueError("Docebo domain or base URL is required")

            if connector_config.auth.authType == DoceboAuthType.TOKEN:
                token = (
                    connector_config.credentials.access_token
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = DoceboTokenConfig(
                    token=token, domain=domain, base_url=base_url
                )
                return cls(token_config.create_client())

            else:
                # Default: CLIENT_CREDENTIALS
                client_id = (
                    connector_config.credentials.client_id
                    or connector_config.auth.clientId
                    or ""
                )
                client_secret = (
                    connector_config.credentials.client_secret
                    or connector_config.auth.clientSecret
                    or ""
                )

                if not client_id or not client_secret:
                    raise ValueError(
                        "Client ID and secret required for "
                        "CLIENT_CREDENTIALS auth type"
                    )

                cc_config = DoceboClientCredentialsConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    domain=domain,
                    base_url=base_url,
                )
                return cls(cc_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Docebo client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DoceboClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            DoceboClient instance
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
            base_url: str | None = cast(
                str | None, toolset_config.get("baseUrl")
            )
            auth_type = auth_config.get("authType", "CLIENT_CREDENTIALS")

            if auth_type == "TOKEN":
                token = str(
                    credentials.get("access_token", "")
                    or auth_config.get("token", "")
                )
                if not token:
                    raise ValueError(
                        "Token not found in toolset config"
                    )
                token_cfg = DoceboTokenConfig(
                    token=token, domain=domain, base_url=base_url
                )
                return cls(token_cfg.create_client())

            else:
                # Default: CLIENT_CREDENTIALS
                client_id = str(
                    credentials.get("client_id", "")
                    or auth_config.get("clientId", "")
                )
                client_secret = str(
                    credentials.get("client_secret", "")
                    or auth_config.get("clientSecret", "")
                )
                if not client_id or not client_secret:
                    raise ValueError(
                        "Client ID and secret not found in toolset config"
                    )
                cc_cfg = DoceboClientCredentialsConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    domain=domain,
                    base_url=base_url,
                )
                return cls(cc_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Docebo client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Docebo."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Docebo connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Docebo connector config: {e}")
            raise ValueError(
                f"Failed to get Docebo connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
