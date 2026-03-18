# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""DataStax Astra DB client implementation.

This module provides a client for interacting with DataStax Astra DB using the
official ``astrapy`` Python package (Data API).

Authentication:
  - Application Token: Passed to ``DataAPIClient``

SDK Reference: https://github.com/datastax/astrapy
"""

import base64
import json
import logging
from enum import Enum
from typing import Any, cast

from astrapy import DataAPIClient
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DataStaxAuthType(str, Enum):
    """Authentication types supported by the DataStax connector."""

    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DataStaxResponse(BaseModel):
    """Standardized DataStax API response wrapper.

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
# SDK wrapper class
# ---------------------------------------------------------------------------


class DataStaxClientViaToken:
    """DataStax Astra DB SDK wrapper authenticated via application token.

    Wraps the official ``astrapy`` ``DataAPIClient``.

    Args:
        token: The Astra DB application token (e.g. ``AstraCS:...``)
        api_endpoint: The database API endpoint URL
    """

    def __init__(self, token: str, api_endpoint: str) -> None:
        self.token = token
        self.api_endpoint = api_endpoint.rstrip("/")
        self._sdk: DataAPIClient | None = None

    def create_client(self) -> DataAPIClient:
        """Create and return the SDK client."""
        self._sdk = DataAPIClient(token=self.token)
        return self._sdk

    def get_sdk(self) -> DataAPIClient:
        """Return the SDK client, creating it lazily if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_api_endpoint(self) -> str:
        """Get the database API endpoint."""
        return self.api_endpoint


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DataStaxTokenConfig(BaseModel):
    """Configuration for DataStax client via Application Token.

    Args:
        token: The Astra DB application token
        api_endpoint: The database API endpoint URL
    """

    token: str
    api_endpoint: str

    def create_client(self) -> DataStaxClientViaToken:
        return DataStaxClientViaToken(
            token=self.token,
            api_endpoint=self.api_endpoint,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DataStaxAuthConfig(BaseModel):
    """Auth section of the DataStax connector configuration from etcd."""

    authType: DataStaxAuthType = DataStaxAuthType.TOKEN
    apiToken: str | None = None
    token: str | None = None
    apiEndpoint: str | None = None
    databaseId: str | None = None
    region: str | None = None

    class Config:
        extra = "allow"


class DataStaxCredentialsConfig(BaseModel):
    """Credentials section of the DataStax connector configuration."""

    token: str | None = None

    class Config:
        extra = "allow"


class DataStaxConnectorConfig(BaseModel):
    """Top-level DataStax connector configuration from etcd."""

    auth: DataStaxAuthConfig = Field(default_factory=DataStaxAuthConfig)
    credentials: DataStaxCredentialsConfig = Field(
        default_factory=DataStaxCredentialsConfig
    )
    apiEndpoint: str | None = None
    databaseId: str | None = None
    region: str | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DataStaxClient(IClient):
    """Builder class for DataStax Astra DB clients using the official SDK.

    Supports:
    - Application Token authentication via ``astrapy``
    """

    def __init__(
        self,
        client: DataStaxClientViaToken,
    ) -> None:
        """Initialize with a DataStax SDK wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> DataStaxClientViaToken:
        """Return the DataStax SDK wrapper."""
        return self.client

    def get_sdk(self) -> DataAPIClient:
        """Return the underlying astrapy DataAPIClient instance."""
        return self.client.get_sdk()

    def get_api_endpoint(self) -> str:
        """Return the database API endpoint."""
        return self.client.get_api_endpoint()

    @classmethod
    def build_with_config(
        cls,
        config: DataStaxTokenConfig,
    ) -> "DataStaxClient":
        """Build DataStaxClient with configuration.

        Args:
            config: DataStaxTokenConfig instance

        Returns:
            DataStaxClient instance
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
    ) -> "DataStaxClient":
        """Build DataStaxClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DataStaxClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get DataStax connector configuration"
                )

            connector_config = DataStaxConnectorConfig.model_validate(
                raw_config
            )

            token = (
                connector_config.auth.apiToken
                or connector_config.auth.token
                or connector_config.credentials.token
                or ""
            )
            if not token:
                raise ValueError(
                    "Application token required for DataStax auth"
                )

            # Resolve API endpoint
            api_endpoint = (
                connector_config.auth.apiEndpoint
                or connector_config.apiEndpoint
                or ""
            )
            if not api_endpoint:
                # Build from database_id and region if available
                database_id = (
                    connector_config.auth.databaseId
                    or connector_config.databaseId
                    or ""
                )
                region = (
                    connector_config.auth.region
                    or connector_config.region
                    or ""
                )
                if database_id and region:
                    api_endpoint = (
                        f"https://{database_id}-{region}"
                        f".apps.astra.datastax.com"
                    )
            if not api_endpoint:
                raise ValueError(
                    "API endpoint (or database ID + region) required for DataStax"
                )

            token_config = DataStaxTokenConfig(
                token=token, api_endpoint=api_endpoint
            )
            wrapper = token_config.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build DataStax client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DataStaxClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            DataStaxClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            token: str = str(
                credentials.get("token", "")
                or auth_config.get("apiToken", "")
                or auth_config.get("token", "")
            )
            if not token:
                raise ValueError(
                    "Application token not found in toolset config"
                )

            api_endpoint: str = str(
                auth_config.get("apiEndpoint", "")
                or toolset_config.get("apiEndpoint", "")
            )
            if not api_endpoint:
                database_id = str(
                    auth_config.get("databaseId", "")
                    or toolset_config.get("databaseId", "")
                )
                region = str(
                    auth_config.get("region", "")
                    or toolset_config.get("region", "")
                )
                if database_id and region:
                    api_endpoint = (
                        f"https://{database_id}-{region}"
                        f".apps.astra.datastax.com"
                    )
            if not api_endpoint:
                raise ValueError(
                    "API endpoint not found in toolset config"
                )

            token_config = DataStaxTokenConfig(
                token=token, api_endpoint=api_endpoint
            )
            wrapper = token_config.create_client()
            wrapper.get_sdk()
            return cls(wrapper)

        except Exception as e:
            logger.error(
                f"Failed to build DataStax client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for DataStax."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get DataStax connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get DataStax connector config: {e}"
            )
            raise ValueError(
                f"Failed to get DataStax connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
