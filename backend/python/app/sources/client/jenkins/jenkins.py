"""Jenkins client implementation.

This module provides clients for interacting with the Jenkins API using either:
1. API Token via HTTP Basic Auth (username:api_token)
2. Pre-generated Bearer token

Authentication Reference: https://www.jenkins.io/doc/book/system-administration/authenticating-scripted-clients/
API Reference: https://www.jenkins.io/doc/book/using/remote-access-api/
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


class JenkinsAuthType(str, Enum):
    """Authentication types supported by the Jenkins connector."""

    API_TOKEN = "API_TOKEN"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class JenkinsResponse(BaseModel):
    """Standardized Jenkins API response wrapper.

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


class JenkinsRESTClientViaApiToken(HTTPClient):
    """Jenkins REST client via API Token with HTTP Basic Auth.

    Uses HTTP Basic Authentication with username:api_token encoded as
    a Base64 string in the Authorization header.

    Args:
        jenkins_url: The Jenkins instance URL (e.g. https://jenkins.example.com)
        username: Jenkins username
        api_token: Jenkins API token
    """

    def __init__(
        self,
        jenkins_url: str,
        username: str,
        api_token: str,
    ) -> None:
        # Initialize with empty token; we override the header below
        super().__init__("", token_type="Basic")
        self.base_url = jenkins_url.rstrip("/")
        self.username = username
        self.api_token = api_token
        # Jenkins API Token auth: HTTP Basic with username:api_token
        credentials = base64.b64encode(
            f"{username}:{api_token}".encode()
        ).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


class JenkinsRESTClientViaToken(HTTPClient):
    """Jenkins REST client via pre-generated Bearer token.

    Simple authentication using a pre-generated token passed directly
    in the Authorization header.

    Args:
        jenkins_url: The Jenkins instance URL (e.g. https://jenkins.example.com)
        token: The pre-generated Bearer token
    """

    def __init__(
        self,
        jenkins_url: str,
        token: str,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.base_url = jenkins_url.rstrip("/")
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class JenkinsApiTokenConfig(BaseModel):
    """Configuration for Jenkins client via API Token (Basic Auth).

    Args:
        jenkins_url: The Jenkins instance URL
        username: Jenkins username
        api_token: Jenkins API token
    """

    jenkins_url: str
    username: str
    api_token: str

    def create_client(self) -> JenkinsRESTClientViaApiToken:
        return JenkinsRESTClientViaApiToken(
            self.jenkins_url,
            self.username,
            self.api_token,
        )


class JenkinsTokenConfig(BaseModel):
    """Configuration for Jenkins client via pre-generated Bearer token.

    Args:
        jenkins_url: The Jenkins instance URL
        token: The pre-generated Bearer token
    """

    jenkins_url: str
    token: str

    def create_client(self) -> JenkinsRESTClientViaToken:
        return JenkinsRESTClientViaToken(self.jenkins_url, self.token)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class JenkinsAuthConfig(BaseModel):
    """Auth section of the Jenkins connector configuration from etcd."""

    authType: JenkinsAuthType = JenkinsAuthType.API_TOKEN
    jenkinsUrl: str | None = None
    jenkins_url: str | None = None
    username: str | None = None
    apiToken: str | None = None
    api_token: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class JenkinsCredentialsConfig(BaseModel):
    """Credentials section of the Jenkins connector configuration."""

    api_token: str | None = None
    token: str | None = None

    class Config:
        extra = "allow"


class JenkinsConnectorConfig(BaseModel):
    """Top-level Jenkins connector configuration from etcd."""

    auth: JenkinsAuthConfig = Field(default_factory=JenkinsAuthConfig)
    credentials: JenkinsCredentialsConfig = Field(
        default_factory=JenkinsCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class JenkinsClient(IClient):
    """Builder class for Jenkins clients with different authentication methods.

    Supports:
    - API Token via HTTP Basic Auth (username:api_token)
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: JenkinsRESTClientViaApiToken | JenkinsRESTClientViaToken,
    ) -> None:
        """Initialize with a Jenkins client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> JenkinsRESTClientViaApiToken | JenkinsRESTClientViaToken:
        """Return the Jenkins client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: JenkinsApiTokenConfig | JenkinsTokenConfig,
    ) -> "JenkinsClient":
        """Build JenkinsClient with configuration.

        Args:
            config: JenkinsApiTokenConfig or JenkinsTokenConfig instance

        Returns:
            JenkinsClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "JenkinsClient":
        """Build JenkinsClient using configuration service.

        Supports two authentication strategies:
        1. API_TOKEN: HTTP Basic Auth with username:api_token
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            JenkinsClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Jenkins connector configuration"
                )

            connector_config = JenkinsConnectorConfig.model_validate(
                raw_config
            )

            jenkins_url = (
                connector_config.auth.jenkinsUrl
                or connector_config.auth.jenkins_url
                or ""
            )
            if not jenkins_url:
                raise ValueError(
                    "jenkins_url is required in Jenkins connector configuration"
                )

            if connector_config.auth.authType == JenkinsAuthType.API_TOKEN:
                username = connector_config.auth.username or ""
                api_token = (
                    connector_config.auth.apiToken
                    or connector_config.auth.api_token
                    or connector_config.credentials.api_token
                    or ""
                )

                if not (username and api_token):
                    raise ValueError(
                        "username and api_token are required "
                        "for API_TOKEN auth type"
                    )

                api_token_config = JenkinsApiTokenConfig(
                    jenkins_url=jenkins_url,
                    username=username,
                    api_token=api_token,
                )
                return cls(api_token_config.create_client())

            elif connector_config.auth.authType == JenkinsAuthType.TOKEN:
                token = (
                    connector_config.auth.token
                    or connector_config.credentials.token
                    or ""
                )
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = JenkinsTokenConfig(
                    jenkins_url=jenkins_url,
                    token=token,
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build Jenkins client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "JenkinsClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            JenkinsClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            jenkins_url: str = str(
                auth_config.get("jenkinsUrl")
                or auth_config.get("jenkins_url")
                or ""
            )
            if not jenkins_url:
                raise ValueError(
                    "jenkins_url not found in toolset config"
                )

            auth_type = str(
                auth_config.get("authType", JenkinsAuthType.API_TOKEN.value)
            )

            if auth_type == JenkinsAuthType.API_TOKEN.value:
                username: str = str(auth_config.get("username", ""))
                api_token: str = str(
                    credentials.get("api_token")
                    or auth_config.get("apiToken")
                    or auth_config.get("api_token")
                    or ""
                )

                if not (username and api_token):
                    raise ValueError(
                        "username and api_token not found in toolset config"
                    )

                api_token_cfg = JenkinsApiTokenConfig(
                    jenkins_url=jenkins_url,
                    username=username,
                    api_token=api_token,
                )
                return cls(api_token_cfg.create_client())

            else:
                token: str = str(
                    credentials.get("token")
                    or auth_config.get("token")
                    or ""
                )
                if not token:
                    raise ValueError(
                        "token not found in toolset config"
                    )

                token_cfg = JenkinsTokenConfig(
                    jenkins_url=jenkins_url,
                    token=token,
                )
                return cls(token_cfg.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Jenkins client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Jenkins."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Jenkins connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Jenkins connector config: {e}")
            raise ValueError(
                f"Failed to get Jenkins connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
