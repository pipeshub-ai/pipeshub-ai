# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Benchling client implementation.

This module provides clients for interacting with the Benchling API using the
official ``benchling-sdk`` Python package.

Authentication:
  - API Key: Passed via ``ApiKeyAuth`` to the SDK

SDK Reference: https://docs.benchling.com/docs/getting-started-with-the-sdk
"""

import base64
import json
import logging
from typing import Any, cast

from benchling_sdk.auth.api_key_auth import ApiKeyAuth
from benchling_sdk.benchling import Benchling
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class BenchlingResponse(BaseModel):
    """Standardized Benchling API response wrapper.

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
# SDK wrapper classes
# ---------------------------------------------------------------------------


class BenchlingClientViaApiKey:
    """Benchling SDK wrapper authenticated via API Key.

    Wraps the official ``benchling-sdk`` ``Benchling`` client.

    Args:
        tenant_url: Full tenant URL, e.g. ``https://your-tenant.benchling.com``
        api_key: The Benchling API key
    """

    def __init__(self, tenant_url: str, api_key: str) -> None:
        self.tenant_url = tenant_url.rstrip("/")
        self.api_key = api_key
        self._sdk: Benchling | None = None

    def create_client(self) -> Benchling:
        """Create and return the SDK client."""
        self._sdk = Benchling(
            url=self.tenant_url,
            auth_method=ApiKeyAuth(self.api_key),
        )
        return self._sdk

    def get_sdk(self) -> Benchling:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        """Get the tenant URL."""
        return self.tenant_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class BenchlingApiKeyConfig(BaseModel):
    """Configuration for Benchling client via API Key.

    Args:
        api_key: The Benchling API key
        tenant_url: Full tenant URL (e.g. ``https://your-tenant.benchling.com``)
    """

    api_key: str
    tenant_url: str

    def create_client(self) -> BenchlingClientViaApiKey:
        return BenchlingClientViaApiKey(
            tenant_url=self.tenant_url,
            api_key=self.api_key,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class BenchlingAuthConfig(BaseModel):
    """Auth section of the Benchling connector configuration from etcd."""

    apiKey: str | None = None
    tenant: str | None = None
    tenantUrl: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class BenchlingConnectorConfig(BaseModel):
    """Top-level Benchling connector configuration from etcd."""

    auth: BenchlingAuthConfig = Field(default_factory=BenchlingAuthConfig)

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class BenchlingClient(IClient):
    """Builder class for Benchling clients using the official SDK.

    Supports:
    - API Key authentication via ``benchling-sdk``
    """

    def __init__(self, client: BenchlingClientViaApiKey) -> None:
        """Initialize with a Benchling SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> BenchlingClientViaApiKey:
        """Return the Benchling SDK wrapper."""
        return self.client

    def get_sdk(self) -> Benchling:
        """Return the underlying Benchling SDK instance."""
        return self.client.get_sdk()

    def get_base_url(self) -> str:
        """Return the tenant URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: BenchlingApiKeyConfig,
    ) -> "BenchlingClient":
        """Build BenchlingClient with configuration.

        Args:
            config: BenchlingApiKeyConfig instance

        Returns:
            BenchlingClient instance
        """
        wrapper = config.create_client()
        wrapper.get_sdk()  # eagerly initialize
        return cls(wrapper)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "BenchlingClient":
        """Build BenchlingClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            BenchlingClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Benchling connector configuration"
                )

            connector_config = BenchlingConnectorConfig.model_validate(
                raw_config
            )

            api_key = connector_config.auth.apiKey or ""
            if not api_key:
                raise ValueError(
                    "API key required for Benchling authentication"
                )

            # Resolve tenant URL
            tenant_url = connector_config.auth.tenantUrl or connector_config.auth.baseUrl or ""
            if not tenant_url and connector_config.auth.tenant:
                tenant_url = f"https://{connector_config.auth.tenant}.benchling.com"
            if not tenant_url:
                raise ValueError("Tenant URL required for Benchling")

            config = BenchlingApiKeyConfig(
                api_key=api_key,
                tenant_url=tenant_url,
            )
            wrapper = config.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build Benchling client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "BenchlingClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            BenchlingClient instance
        """
        try:
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(auth_config.get("apiKey", ""))
            if not api_key:
                raise ValueError(
                    "API key not found in toolset config"
                )

            tenant_url: str = str(
                auth_config.get("tenantUrl", "")
                or auth_config.get("baseUrl", "")
            )
            if not tenant_url:
                tenant = str(auth_config.get("tenant", ""))
                if tenant:
                    tenant_url = f"https://{tenant}.benchling.com"
            if not tenant_url:
                raise ValueError("Tenant URL not found in toolset config")

            config = BenchlingApiKeyConfig(
                api_key=api_key,
                tenant_url=tenant_url,
            )
            wrapper = config.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build Benchling client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Benchling."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Benchling connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Benchling connector config: {e}")
            raise ValueError(
                f"Failed to get Benchling connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
