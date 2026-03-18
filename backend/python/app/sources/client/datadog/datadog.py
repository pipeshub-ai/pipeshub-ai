"""Datadog client implementation using the official datadog-api-client SDK.

This module provides a client for interacting with the Datadog API using
API Key + Application Key authentication via the official Python SDK.

Authentication Reference: https://docs.datadoghq.com/api/latest/authentication/
SDK Reference: https://github.com/DataDog/datadog-api-client-python

Datadog authenticates via two keys set on the Configuration object:
- apiKeyAuth: The API key
- appKeyAuth: The application key

The site is set via server_variables["site"] on the Configuration.
"""

import logging
from typing import Any, cast

from datadog_api_client import (  # type: ignore[reportMissingImports]
    ApiClient,  # type: ignore[reportUnknownVariableType]
    Configuration,  # type: ignore[reportUnknownVariableType]
)
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DatadogResponse(BaseModel):
    """Standardized Datadog API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = None
    error: str | None = Field(
        default=None, description="Error message if failed"
    )
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK client class
# ---------------------------------------------------------------------------


class DatadogClientViaApiKey:
    """Datadog SDK client via API Key + Application Key.

    Wraps the official ``datadog-api-client`` SDK.  Stores the
    ``Configuration`` object and creates ``ApiClient`` instances on
    demand (the SDK uses a context-manager pattern).

    Args:
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site domain (default: datadoghq.com)
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        site: str = "datadoghq.com",
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.app_key = app_key
        self.site = site

        self._configuration: Any = Configuration()  # type: ignore[reportUnknownVariableType]
        self._configuration.api_key["apiKeyAuth"] = api_key  # type: ignore[reportUnknownMemberType]
        self._configuration.api_key["appKeyAuth"] = app_key  # type: ignore[reportUnknownMemberType]
        self._configuration.server_variables["site"] = site  # type: ignore[reportUnknownMemberType]

    def get_sdk(self) -> Any:  # Configuration
        """Return the SDK ``Configuration`` object.

        Callers should use it with ``ApiClient(configuration)`` as a
        context manager to obtain an ``ApiClient`` instance::

            with ApiClient(config.get_sdk()) as api_client:
                api = DashboardsApi(api_client)
                dashboards = api.list_dashboards()
        """
        return self._configuration  # type: ignore[reportUnknownVariableType]

    def get_api_client(self) -> Any:  # ApiClient
        """Return a new ``ApiClient`` instance.

        The caller is responsible for closing it (or using it as a
        context manager).
        """
        return ApiClient(self._configuration)  # type: ignore[reportUnknownVariableType]


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DatadogApiKeyConfig(BaseModel):
    """Configuration for Datadog client via API Key + Application Key.

    Args:
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site domain (default: datadoghq.com)
    """

    api_key: str
    app_key: str
    site: str = "datadoghq.com"

    def create_client(self) -> DatadogClientViaApiKey:
        return DatadogClientViaApiKey(
            self.api_key,
            self.app_key,
            self.site,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DatadogAuthConfig(BaseModel):
    """Auth section of the Datadog connector configuration from etcd."""

    authType: str = "API_KEY"
    apiKey: str | None = None
    applicationKey: str | None = None
    site: str | None = None

    class Config:
        extra = "allow"


class DatadogCredentialsConfig(BaseModel):
    """Credentials section of the Datadog connector configuration."""

    api_key: str | None = None
    application_key: str | None = None

    class Config:
        extra = "allow"


class DatadogConnectorConfig(BaseModel):
    """Top-level Datadog connector configuration from etcd."""

    auth: DatadogAuthConfig = Field(default_factory=DatadogAuthConfig)
    credentials: DatadogCredentialsConfig = Field(
        default_factory=DatadogCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DatadogClient(IClient):
    """Builder class for Datadog clients.

    Supports:
    - API Key + Application Key authentication
    """

    def __init__(self, client: DatadogClientViaApiKey) -> None:
        """Initialize with a Datadog SDK client wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> DatadogClientViaApiKey:
        """Return the Datadog SDK client wrapper."""
        return self.client

    def get_sdk(self) -> Any:  # Configuration
        """Convenience: return the SDK Configuration."""
        return self.client.get_sdk()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]

    @classmethod
    def build_with_config(
        cls,
        config: DatadogApiKeyConfig,
    ) -> "DatadogClient":
        """Build DatadogClient with configuration.

        Args:
            config: DatadogApiKeyConfig instance

        Returns:
            DatadogClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "DatadogClient":
        """Build DatadogClient using configuration service.

        Reads API key and application key from the config service (etcd).

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DatadogClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Datadog connector configuration"
                )

            connector_config = DatadogConnectorConfig.model_validate(
                raw_config
            )

            api_key = (
                connector_config.credentials.api_key
                or connector_config.auth.apiKey
                or ""
            )
            app_key = (
                connector_config.credentials.application_key
                or connector_config.auth.applicationKey
                or ""
            )
            site = connector_config.auth.site or "datadoghq.com"

            if not (api_key and app_key):
                raise ValueError(
                    "api_key and app_key are required "
                    "for Datadog API_KEY auth type"
                )

            api_key_config = DatadogApiKeyConfig(
                api_key=api_key,
                app_key=app_key,
                site=site,
            )
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Datadog client from services: {e!s}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DatadogClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service (unused for Datadog)

        Returns:
            DatadogClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            api_key: str = str(
                credentials.get("api_key")
                or auth_config.get("apiKey", "")
            )
            app_key: str = str(
                credentials.get("application_key")
                or auth_config.get("applicationKey", "")
            )
            site: str = str(auth_config.get("site", "datadoghq.com"))

            if not (api_key and app_key):
                raise ValueError(
                    "API key and application key not found in toolset config"
                )

            api_key_config = DatadogApiKeyConfig(
                api_key=api_key,
                app_key=app_key,
                site=site,
            )
            return cls(api_key_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build Datadog client from toolset: {e!s}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Datadog."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Datadog connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Datadog connector config: {e}")
            raise ValueError(
                f"Failed to get Datadog connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
