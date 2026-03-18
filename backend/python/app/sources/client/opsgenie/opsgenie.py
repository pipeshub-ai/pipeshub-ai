# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""Opsgenie client implementation using the official ``opsgenie-sdk`` package.

This module provides a client for interacting with the Opsgenie API using
API Key authentication via the official SDK.

SDK Reference: https://github.com/opsgenie/opsgenie-python-sdk
API Reference: https://docs.opsgenie.com/docs/api-overview
"""

import logging
from typing import Any

import opsgenie_sdk
from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class OpsgenieResponse(BaseModel):
    """Standardised Opsgenie API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Any = Field(default=None, description="Response data from the SDK")
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        arbitrary_types_allowed = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.model_dump_json()


# ---------------------------------------------------------------------------
# SDK wrapper client
# ---------------------------------------------------------------------------


class OpsgenieClientViaApiKey:
    """Opsgenie SDK client via API Key.

    Wraps the official ``opsgenie_sdk`` package. The API key is configured
    with the ``GenieKey`` prefix as required by Opsgenie.

    Args:
        api_key: The Opsgenie API key
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._sdk: opsgenie_sdk.ApiClient | None = None
        self._configuration: opsgenie_sdk.Configuration | None = None

    def create_client(self) -> opsgenie_sdk.ApiClient:
        self._configuration = opsgenie_sdk.Configuration()
        self._configuration.api_key["Authorization"] = self.api_key
        self._configuration.api_key_prefix["Authorization"] = "GenieKey"
        self._sdk = opsgenie_sdk.ApiClient(self._configuration)
        return self._sdk

    def get_sdk(self) -> opsgenie_sdk.ApiClient:
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return "https://api.opsgenie.com/v2"


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class OpsgenieApiKeyConfig(BaseModel):
    """Configuration for Opsgenie client via API Key.

    Args:
        api_key: The Opsgenie API key
    """

    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("api_key cannot be empty or None")
        return v

    def create_client(self) -> OpsgenieClientViaApiKey:
        return OpsgenieClientViaApiKey(self.api_key)

    def to_dict(self) -> dict[str, Any]:
        return {"has_api_key": bool(self.api_key)}


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class OpsgenieClient(IClient):
    """Builder class for Opsgenie clients."""

    def __init__(self, client: OpsgenieClientViaApiKey) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> OpsgenieClientViaApiKey:
        return self.client

    def get_sdk(self) -> opsgenie_sdk.ApiClient:
        return self.client.get_sdk()

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: OpsgenieApiKeyConfig,
    ) -> "OpsgenieClient":
        client = config.create_client()
        _ = client.get_sdk()
        return cls(client)

    @classmethod
    def build_with_api_key(
        cls,
        api_key: str,
    ) -> "OpsgenieClient":
        config = OpsgenieApiKeyConfig(api_key=api_key)
        return cls.build_with_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "OpsgenieClient":
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError(
                "Failed to get Opsgenie connector configuration"
            )
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_KEY")
        if auth_type == "API_KEY":
            api_key = auth_config.get("apiKey", "")
            if not api_key:
                raise ValueError("API key required for API key auth type")
            client = OpsgenieApiKeyConfig(api_key=api_key).create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            config = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Opsgenie connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return dict(config)  # type: ignore[arg-type]
        except Exception as e:
            logger.error(f"Failed to get Opsgenie connector config: {e}")
            raise ValueError(
                f"Failed to get Opsgenie connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
