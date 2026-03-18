"""Adobe Experience Manager (AEM as Cloud Service) client implementation.

This module provides a client for interacting with the AEM API using
Bearer token authentication.

AEM uses instance-based URLs: https://{instance}.adobeaemcloud.com

API Reference: https://experienceleague.adobe.com/docs/experience-manager-cloud-service/content/implementing/developing/generating-access-tokens-for-server-side-apis.html
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


class AdobeAEMResponse(BaseModel):
    """Standardized Adobe AEM API response wrapper.

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


class AdobeAEMRESTClientViaToken(HTTPClient):
    """Adobe AEM REST client via Bearer token.

    Args:
        token: Bearer token for authentication
        instance: AEM instance identifier (e.g., "author-p12345-e67890")
    """

    def __init__(self, token: str, instance: str) -> None:
        super().__init__(token, "Bearer")
        self.instance = instance
        self.base_url = f"https://{instance}.adobeaemcloud.com"
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including instance."""
        return self.base_url

    def get_instance(self) -> str:
        """Get the AEM instance identifier."""
        return self.instance


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class AdobeAEMTokenConfig(BaseModel):
    """Configuration for AEM client via Bearer token.

    Args:
        token: Bearer token
        instance: AEM instance identifier
    """

    token: str
    instance: str

    def create_client(self) -> AdobeAEMRESTClientViaToken:
        return AdobeAEMRESTClientViaToken(self.token, self.instance)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class AdobeAEMAuthConfig(BaseModel):
    """Auth section of the AEM connector configuration from etcd."""

    token: str | None = None
    apiToken: str | None = None

    class Config:
        extra = "allow"


class AdobeAEMCredentialsConfig(BaseModel):
    """Credentials section of the AEM connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class AdobeAEMConnectorConfig(BaseModel):
    """Top-level AEM connector configuration from etcd."""

    auth: AdobeAEMAuthConfig = Field(default_factory=AdobeAEMAuthConfig)
    credentials: AdobeAEMCredentialsConfig = Field(
        default_factory=AdobeAEMCredentialsConfig
    )
    instance: str = ""

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class AdobeAEMClient(IClient):
    """Builder class for Adobe AEM clients.

    Supports:
    - Bearer token authentication
    - Instance-based URL construction
    """

    def __init__(
        self,
        client: AdobeAEMRESTClientViaToken,
    ) -> None:
        """Initialize with an AEM client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> AdobeAEMRESTClientViaToken:
        """Return the AEM client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @property
    def instance(self) -> str:
        """Return the AEM instance identifier."""
        return self.client.get_instance()

    @classmethod
    def build_with_config(
        cls,
        config: AdobeAEMTokenConfig,
    ) -> "AdobeAEMClient":
        """Build AdobeAEMClient with configuration.

        Args:
            config: AdobeAEMTokenConfig instance

        Returns:
            AdobeAEMClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "AdobeAEMClient":
        """Build AdobeAEMClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            AdobeAEMClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get AEM connector configuration"
                )

            connector_config = AdobeAEMConnectorConfig.model_validate(
                raw_config
            )

            instance = connector_config.instance
            if not instance:
                raise ValueError("AEM instance identifier is required")

            token = (
                connector_config.auth.token
                or connector_config.auth.apiToken
                or connector_config.credentials.access_token
                or ""
            )
            if not token:
                raise ValueError(
                    "Token required for AEM authentication"
                )

            token_config = AdobeAEMTokenConfig(
                token=token, instance=instance
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build AEM client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "AdobeAEMClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service

        Returns:
            AdobeAEMClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any],
                toolset_config.get("credentials", {}) or {},
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            instance: str = str(toolset_config.get("instance", ""))

            if not instance:
                raise ValueError(
                    "AEM instance not found in toolset config"
                )

            access_token: str = str(
                credentials.get("access_token", "")
                or auth_config.get("token", "")
                or auth_config.get("apiToken", "")
            )
            if not access_token:
                raise ValueError(
                    "Access token not found in toolset config"
                )

            token_config = AdobeAEMTokenConfig(
                token=access_token, instance=instance
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build AEM client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for AEM."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get AEM connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(
                f"Failed to get AEM connector config: {e}"
            )
            raise ValueError(
                f"Failed to get AEM connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
