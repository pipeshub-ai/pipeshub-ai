"""Redmine client implementation.

This module provides clients for interacting with the Redmine API using either:
1. API Key authentication (X-Redmine-API-Key header)
2. Basic Auth (username + password)

The base URL is the instance URL: https://{instance}
All endpoints return JSON when .json is appended.

Authentication Reference: https://www.redmine.org/projects/redmine/wiki/Rest_api#Authentication
API Reference: https://www.redmine.org/projects/redmine/wiki/Rest_api
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


class RedmineAuthType(str, Enum):
    """Authentication types supported by the Redmine connector."""

    API_KEY = "API_KEY"
    BASIC = "BASIC"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class RedmineResponse(BaseModel):
    """Standardized Redmine API response wrapper.

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


class RedmineRESTClientViaApiKey(HTTPClient):
    """Redmine REST client via API Key (X-Redmine-API-Key header).

    Args:
        api_key: The Redmine API key
        instance_url: Redmine instance URL (e.g. "redmine.example.com")
    """

    def __init__(self, api_key: str, instance_url: str) -> None:
        # Initialize with empty token; we set the custom header below
        super().__init__("", token_type="Bearer")
        self.base_url = f"https://{instance_url}"
        self.instance_url = instance_url
        self.api_key = api_key
        # Remove the default Authorization header and set Redmine-specific key
        _ = self.headers.pop("Authorization", None)
        self.headers["X-Redmine-API-Key"] = api_key
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL."""
        return self.instance_url


class RedmineRESTClientViaBasicAuth(HTTPClient):
    """Redmine REST client via Basic Auth (username + password).

    Args:
        username: Redmine username
        password: Redmine password
        instance_url: Redmine instance URL (e.g. "redmine.example.com")
    """

    def __init__(
        self,
        username: str,
        password: str,
        instance_url: str,
    ) -> None:
        super().__init__("", token_type="Basic")
        self.base_url = f"https://{instance_url}"
        self.instance_url = instance_url
        self.username = username
        credentials = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL."""
        return self.instance_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class RedmineApiKeyConfig(BaseModel):
    """Configuration for Redmine client via API Key.

    Args:
        api_key: The Redmine API key
        instance_url: Redmine instance URL (e.g. "redmine.example.com")
    """

    api_key: str
    instance_url: str

    def create_client(self) -> RedmineRESTClientViaApiKey:
        return RedmineRESTClientViaApiKey(self.api_key, self.instance_url)


class RedmineBasicAuthConfig(BaseModel):
    """Configuration for Redmine client via Basic Auth.

    Args:
        username: Redmine username
        password: Redmine password
        instance_url: Redmine instance URL (e.g. "redmine.example.com")
    """

    username: str
    password: str
    instance_url: str

    def create_client(self) -> RedmineRESTClientViaBasicAuth:
        return RedmineRESTClientViaBasicAuth(
            self.username, self.password, self.instance_url
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class RedmineAuthConfigModel(BaseModel):
    """Auth section of the Redmine connector configuration from etcd."""

    authType: RedmineAuthType = RedmineAuthType.API_KEY
    instanceUrl: str | None = None
    apiKey: str | None = None
    username: str | None = None
    password: str | None = None

    class Config:
        extra = "allow"


class RedmineCredentialsConfig(BaseModel):
    """Credentials section of the Redmine connector configuration."""

    api_key: str | None = None

    class Config:
        extra = "allow"


class RedmineConnectorConfig(BaseModel):
    """Top-level Redmine connector configuration from etcd."""

    auth: RedmineAuthConfigModel = Field(
        default_factory=RedmineAuthConfigModel
    )
    credentials: RedmineCredentialsConfig = Field(
        default_factory=RedmineCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class RedmineClient(IClient):
    """Builder class for Redmine clients with different authentication methods.

    Supports:
    - API Key authentication (X-Redmine-API-Key header)
    - Basic Auth (username + password)
    """

    def __init__(
        self,
        client: RedmineRESTClientViaApiKey | RedmineRESTClientViaBasicAuth,
    ) -> None:
        """Initialize with a Redmine client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> RedmineRESTClientViaApiKey | RedmineRESTClientViaBasicAuth:
        """Return the Redmine client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: RedmineApiKeyConfig | RedmineBasicAuthConfig,
    ) -> "RedmineClient":
        """Build RedmineClient with configuration.

        Args:
            config: RedmineApiKeyConfig or RedmineBasicAuthConfig instance

        Returns:
            RedmineClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "RedmineClient":
        """Build RedmineClient using configuration service.

        Supports two authentication strategies:
        1. API_KEY: API key in X-Redmine-API-Key header
        2. BASIC: Basic Auth with username and password

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            RedmineClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Redmine connector configuration"
                )

            connector_config = RedmineConnectorConfig.model_validate(
                raw_config
            )

            instance_url = connector_config.auth.instanceUrl or ""
            if not instance_url:
                raise ValueError("Instance URL is required")

            if connector_config.auth.authType == RedmineAuthType.API_KEY:
                api_key = (
                    connector_config.auth.apiKey
                    or connector_config.credentials.api_key
                    or ""
                )
                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )

                api_key_cfg = RedmineApiKeyConfig(
                    api_key=api_key, instance_url=instance_url
                )
                return cls(api_key_cfg.create_client())

            elif connector_config.auth.authType == RedmineAuthType.BASIC:
                username = connector_config.auth.username or ""
                password = connector_config.auth.password or ""

                if not (username and password):
                    raise ValueError(
                        "Username and password required for Basic auth type"
                    )

                basic_cfg = RedmineBasicAuthConfig(
                    username=username,
                    password=password,
                    instance_url=instance_url,
                )
                return cls(basic_cfg.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Redmine client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "RedmineClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            RedmineClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            instance_url: str = str(auth_config.get("instanceUrl", ""))
            if not instance_url:
                raise ValueError(
                    "Instance URL not found in toolset config"
                )

            api_key: str = str(
                credentials.get("api_key", "")
                or auth_config.get("apiKey", "")
            )
            if not api_key:
                raise ValueError("API key not found in toolset config")

            api_key_cfg = RedmineApiKeyConfig(
                api_key=api_key, instance_url=instance_url
            )
            return cls(api_key_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Redmine client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Redmine."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Redmine connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get Redmine connector config: {e}"
            )
            raise ValueError(
                f"Failed to get Redmine connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
