"""JFrog Artifactory client implementation.

This module provides clients for interacting with the JFrog Artifactory API using either:
1. API Key authentication (X-JFrog-Art-Api header)
2. Bearer Token authentication
3. Basic Auth (username:password)

Authentication Reference: https://jfrog.com/help/r/jfrog-platform-administration-documentation/access-tokens
API Reference: https://jfrog.com/help/r/jfrog-rest-apis/artifactory-rest-apis
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


class JFrogAuthType(str, Enum):
    """Authentication types supported by the JFrog connector."""

    API_KEY = "API_KEY"
    TOKEN = "TOKEN"
    BASIC_AUTH = "BASIC_AUTH"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class JFrogResponse(BaseModel):
    """Standardized JFrog API response wrapper.

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


class JFrogRESTClientViaApiKey(HTTPClient):
    """JFrog REST client via API Key authentication.

    API keys are passed in the X-JFrog-Art-Api header.

    Args:
        api_key: The JFrog API key
        domain: The JFrog domain (e.g., "mycompany" for mycompany.jfrog.io)
        base_url: Optional full base URL override
    """

    def __init__(
        self,
        api_key: str,
        domain: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__(api_key, token_type="Bearer")
        self.base_url = (
            base_url or f"https://{domain}.jfrog.io/artifactory/api"
        )
        self.domain = domain
        # Override Authorization with the JFrog-specific header
        del self.headers["Authorization"]
        self.headers["X-JFrog-Art-Api"] = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class JFrogRESTClientViaToken(HTTPClient):
    """JFrog REST client via Bearer Token authentication.

    Bearer tokens are passed in the standard Authorization header.

    Args:
        token: The Bearer token
        domain: The JFrog domain (e.g., "mycompany" for mycompany.jfrog.io)
        base_url: Optional full base URL override
    """

    def __init__(
        self,
        token: str,
        domain: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = (
            base_url or f"https://{domain}.jfrog.io/artifactory/api"
        )
        self.domain = domain
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class JFrogRESTClientViaBasicAuth(HTTPClient):
    """JFrog REST client via Basic Auth (username:password).

    Credentials are base64-encoded and passed in the Authorization header.

    Args:
        username: The JFrog username
        password: The JFrog password or API key
        domain: The JFrog domain (e.g., "mycompany" for mycompany.jfrog.io)
        base_url: Optional full base URL override
    """

    def __init__(
        self,
        username: str,
        password: str,
        domain: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__("", token_type="Basic")
        self.base_url = (
            base_url or f"https://{domain}.jfrog.io/artifactory/api"
        )
        self.domain = domain
        self.username = username
        self.password = password
        credentials = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class JFrogApiKeyConfig(BaseModel):
    """Configuration for JFrog client via API Key.

    Args:
        api_key: The JFrog API key
        domain: The JFrog domain
        base_url: Optional full base URL override
    """

    api_key: str
    domain: str
    base_url: str | None = None

    def create_client(self) -> JFrogRESTClientViaApiKey:
        return JFrogRESTClientViaApiKey(
            self.api_key, self.domain, self.base_url
        )


class JFrogTokenConfig(BaseModel):
    """Configuration for JFrog client via Bearer Token.

    Args:
        token: The Bearer token
        domain: The JFrog domain
        base_url: Optional full base URL override
    """

    token: str
    domain: str
    base_url: str | None = None

    def create_client(self) -> JFrogRESTClientViaToken:
        return JFrogRESTClientViaToken(
            self.token, self.domain, self.base_url
        )


class JFrogBasicAuthConfig(BaseModel):
    """Configuration for JFrog client via Basic Auth.

    Args:
        username: The JFrog username
        password: The JFrog password or API key
        domain: The JFrog domain
        base_url: Optional full base URL override
    """

    username: str
    password: str
    domain: str
    base_url: str | None = None

    def create_client(self) -> JFrogRESTClientViaBasicAuth:
        return JFrogRESTClientViaBasicAuth(
            self.username, self.password, self.domain, self.base_url
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class JFrogAuthConfig(BaseModel):
    """Auth section of the JFrog connector configuration from etcd."""

    authType: JFrogAuthType = JFrogAuthType.API_KEY
    apiKey: str | None = None
    token: str | None = None
    username: str | None = None
    password: str | None = None
    domain: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class JFrogCredentialsConfig(BaseModel):
    """Credentials section of the JFrog connector configuration."""

    access_token: str | None = None
    api_key: str | None = None

    class Config:
        extra = "allow"


class JFrogConnectorConfig(BaseModel):
    """Top-level JFrog connector configuration from etcd."""

    auth: JFrogAuthConfig = Field(default_factory=JFrogAuthConfig)
    credentials: JFrogCredentialsConfig = Field(
        default_factory=JFrogCredentialsConfig
    )
    domain: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class JFrogClient(IClient):
    """Builder class for JFrog clients with different authentication methods.

    Supports:
    - API Key authentication (X-JFrog-Art-Api header)
    - Bearer Token authentication
    - Basic Auth (username:password)
    """

    def __init__(
        self,
        client: (
            JFrogRESTClientViaApiKey
            | JFrogRESTClientViaToken
            | JFrogRESTClientViaBasicAuth
        ),
    ) -> None:
        """Initialize with a JFrog client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> (
        JFrogRESTClientViaApiKey
        | JFrogRESTClientViaToken
        | JFrogRESTClientViaBasicAuth
    ):
        """Return the JFrog client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: JFrogApiKeyConfig | JFrogTokenConfig | JFrogBasicAuthConfig,
    ) -> "JFrogClient":
        """Build JFrogClient with configuration.

        Args:
            config: JFrogApiKeyConfig, JFrogTokenConfig, or
                JFrogBasicAuthConfig instance

        Returns:
            JFrogClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "JFrogClient":
        """Build JFrogClient using configuration service.

        Supports three authentication strategies:
        1. API_KEY: For JFrog API key (X-JFrog-Art-Api header)
        2. TOKEN: For Bearer token authentication
        3. BASIC_AUTH: For username:password authentication

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            JFrogClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get JFrog connector configuration"
                )

            connector_config = JFrogConnectorConfig.model_validate(
                raw_config
            )
            domain = (
                connector_config.auth.domain or connector_config.domain or ""
            )
            base_url = connector_config.auth.baseUrl or None

            if not domain and not base_url:
                raise ValueError(
                    "JFrog domain or base URL is required"
                )

            if connector_config.auth.authType == JFrogAuthType.TOKEN:
                token = (
                    connector_config.credentials.access_token
                    or connector_config.auth.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = JFrogTokenConfig(
                    token=token, domain=domain, base_url=base_url
                )
                return cls(token_config.create_client())

            elif connector_config.auth.authType == JFrogAuthType.BASIC_AUTH:
                username = connector_config.auth.username or ""
                password = connector_config.auth.password or ""

                if not username or not password:
                    raise ValueError(
                        "Username and password required for BASIC_AUTH"
                    )

                basic_config = JFrogBasicAuthConfig(
                    username=username,
                    password=password,
                    domain=domain,
                    base_url=base_url,
                )
                return cls(basic_config.create_client())

            else:
                # Default: API_KEY
                api_key = (
                    connector_config.credentials.api_key
                    or connector_config.auth.apiKey
                    or ""
                )
                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )

                api_key_config = JFrogApiKeyConfig(
                    api_key=api_key, domain=domain, base_url=base_url
                )
                return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build JFrog client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "JFrogClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            JFrogClient instance
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
            auth_type = auth_config.get("authType", "API_KEY")

            if auth_type == "TOKEN":
                token = str(
                    credentials.get("access_token", "")
                    or auth_config.get("token", "")
                )
                if not token:
                    raise ValueError(
                        "Token not found in toolset config"
                    )
                token_cfg = JFrogTokenConfig(
                    token=token, domain=domain, base_url=base_url
                )
                return cls(token_cfg.create_client())

            elif auth_type == "BASIC_AUTH":
                username = str(auth_config.get("username", ""))
                password = str(
                    credentials.get("password", "")
                    or auth_config.get("password", "")
                )
                if not username or not password:
                    raise ValueError(
                        "Username and password not found in toolset config"
                    )
                basic_cfg = JFrogBasicAuthConfig(
                    username=username,
                    password=password,
                    domain=domain,
                    base_url=base_url,
                )
                return cls(basic_cfg.create_client())

            else:
                # Default: API_KEY
                api_key = str(
                    credentials.get("api_key", "")
                    or auth_config.get("apiKey", "")
                )
                if not api_key:
                    raise ValueError(
                        "API key not found in toolset config"
                    )
                api_key_cfg = JFrogApiKeyConfig(
                    api_key=api_key, domain=domain, base_url=base_url
                )
                return cls(api_key_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build JFrog client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for JFrog."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get JFrog connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get JFrog connector config: {e}")
            raise ValueError(
                f"Failed to get JFrog connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
