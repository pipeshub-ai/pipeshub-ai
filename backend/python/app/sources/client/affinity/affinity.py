"""Affinity client implementation.

This module provides a client for interacting with the Affinity CRM API
using API Key authentication via HTTP Basic Auth.

Affinity uses Basic Auth with an empty username and the API key as the
password: ``Authorization: Basic base64(":api_key")``.

Authentication Reference: https://api-docs.affinity.co/#authentication
API Reference: https://api-docs.affinity.co/
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


class AffinityResponse(BaseModel):
    """Standardized Affinity API response wrapper.

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


class AffinityRESTClientViaApiKey(HTTPClient):
    """Affinity REST client via API Key (Basic Auth).

    Uses HTTP Basic Auth with an empty username and the API key as
    the password: ``Authorization: Basic base64(":api_key")``.

    Args:
        api_key: Affinity API key
        base_url: API base URL (default: https://api.affinity.co)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.affinity.co",
    ) -> None:
        # Initialize parent with empty token; we override Authorization below
        super().__init__("", token_type="Basic")
        self.api_key = api_key
        self.base_url = base_url
        # Affinity Basic Auth: empty username, api_key as password
        credentials = base64.b64encode(f":{api_key}".encode()).decode("utf-8")
        self.headers["Authorization"] = f"Basic {credentials}"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class AffinityApiKeyConfig(BaseModel):
    """Configuration for Affinity client via API Key.

    Args:
        api_key: Affinity API key
        base_url: API base URL (default: https://api.affinity.co)
    """

    api_key: str
    base_url: str = "https://api.affinity.co"

    def create_client(self) -> AffinityRESTClientViaApiKey:
        return AffinityRESTClientViaApiKey(self.api_key, self.base_url)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class AffinityAuthConfig(BaseModel):
    """Auth section of the Affinity connector configuration from etcd."""

    apiKey: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class AffinityConnectorConfig(BaseModel):
    """Top-level Affinity connector configuration from etcd."""

    auth: AffinityAuthConfig = Field(default_factory=AffinityAuthConfig)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class AffinityClient(IClient):
    """Builder class for Affinity clients.

    Supports:
    - API Key authentication via HTTP Basic Auth
    """

    def __init__(
        self,
        client: AffinityRESTClientViaApiKey,
    ) -> None:
        """Initialize with an Affinity client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> AffinityRESTClientViaApiKey:
        """Return the Affinity client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: AffinityApiKeyConfig,
    ) -> "AffinityClient":
        """Build AffinityClient with configuration.

        Args:
            config: AffinityApiKeyConfig instance

        Returns:
            AffinityClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "AffinityClient":
        """Build AffinityClient using configuration service.

        Uses API key from the connector configuration for Basic Auth.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            AffinityClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Affinity connector configuration"
                )

            connector_config = AffinityConnectorConfig.model_validate(
                raw_config
            )

            api_key = connector_config.auth.apiKey or ""

            # Try shared OAuth config if API key is missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not api_key:
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/affinity", default=[]
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
                            api_key = str(
                                shared.get("apiKey")
                                or shared.get("api_key")
                                or api_key
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            if not api_key:
                raise ValueError(
                    "api_key is required for Affinity auth"
                )

            api_key_config = AffinityApiKeyConfig(api_key=api_key)
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Affinity client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "AffinityClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared config

        Returns:
            AffinityClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(auth_config.get("apiKey", ""))

            # Try shared config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not api_key:
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/affinity", default=[]
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
                            api_key = str(
                                shared.get("apiKey")
                                or shared.get("api_key")
                                or api_key
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            if not api_key:
                raise ValueError(
                    "api_key is required in toolset config for Affinity"
                )

            api_key_config = AffinityApiKeyConfig(api_key=api_key)
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Affinity client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Affinity."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Affinity connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Affinity connector config: {e}")
            raise ValueError(
                f"Failed to get Affinity connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
