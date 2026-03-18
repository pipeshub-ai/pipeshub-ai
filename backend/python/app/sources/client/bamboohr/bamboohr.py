"""BambooHR client implementation.

This module provides clients for interacting with the BambooHR API using:
1. API Key authentication (HTTP Basic Auth with api_key as username, "x" as password)

Authentication Reference: https://documentation.bamboohr.com/docs/getting-started#authentication
API Reference: https://documentation.bamboohr.com/reference
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


class BambooHRAuthType(str, Enum):
    """Authentication types supported by the BambooHR connector."""

    API_KEY = "API_KEY"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class BambooHRResponse(BaseModel):
    """Standardized BambooHR API response wrapper.

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


class BambooHRRESTClientViaApiKey(HTTPClient):
    """BambooHR REST client via API Key.

    BambooHR uses HTTP Basic Authentication with the API key as the username
    and "x" as the password.

    Args:
        company_domain: The BambooHR company subdomain (e.g., 'mycompany')
        api_key: The API key for authentication
    """

    def __init__(self, company_domain: str, api_key: str) -> None:
        # BambooHR uses Basic auth with API key as username, 'x' as password
        credentials = f"{api_key}:x"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize HTTPClient with Basic token
        super().__init__(encoded_credentials, "Basic")
        self.company_domain = company_domain
        self.base_url = (
            f"https://api.bamboohr.com/api/gateway.php/{company_domain}/v1"
        )
        self.api_key = api_key
        self.headers["Accept"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_company_domain(self) -> str:
        """Get the BambooHR company domain."""
        return self.company_domain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class BambooHRApiKeyConfig(BaseModel):
    """Configuration for BambooHR client via API Key.

    Args:
        company_domain: The BambooHR company subdomain (e.g., 'mycompany')
        api_key: The API key for authentication
    """

    company_domain: str
    api_key: str

    def create_client(self) -> BambooHRRESTClientViaApiKey:
        return BambooHRRESTClientViaApiKey(self.company_domain, self.api_key)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class BambooHRAuthConfig(BaseModel):
    """Auth section of the BambooHR connector configuration from etcd."""

    authType: BambooHRAuthType = BambooHRAuthType.API_KEY
    apiKey: str | None = None
    companyDomain: str | None = None

    class Config:
        extra = "allow"


class BambooHRCredentialsConfig(BaseModel):
    """Credentials section of the BambooHR connector configuration."""

    api_key: str | None = None

    class Config:
        extra = "allow"


class BambooHRConnectorConfig(BaseModel):
    """Top-level BambooHR connector configuration from etcd."""

    auth: BambooHRAuthConfig = Field(default_factory=BambooHRAuthConfig)
    credentials: BambooHRCredentialsConfig = Field(
        default_factory=BambooHRCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class BambooHRClient(IClient):
    """Builder class for BambooHR clients with API Key authentication.

    Supports:
    - API Key authentication (HTTP Basic Auth)
    """

    def __init__(
        self,
        client: BambooHRRESTClientViaApiKey,
    ) -> None:
        """Initialize with a BambooHR client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> BambooHRRESTClientViaApiKey:
        """Return the BambooHR client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    def get_company_domain(self) -> str:
        """Return the company domain."""
        return self.client.get_company_domain()

    @classmethod
    def build_with_config(
        cls,
        config: BambooHRApiKeyConfig,
    ) -> "BambooHRClient":
        """Build BambooHRClient with configuration.

        Args:
            config: BambooHRApiKeyConfig instance

        Returns:
            BambooHRClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "BambooHRClient":
        """Build BambooHRClient using configuration service.

        Supports API Key authentication strategy.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            BambooHRClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError("Failed to get BambooHR connector configuration")

            connector_config = BambooHRConnectorConfig.model_validate(raw_config)

            if connector_config.auth.authType == BambooHRAuthType.API_KEY:
                api_key = (
                    connector_config.auth.apiKey
                    or connector_config.credentials.api_key
                    or ""
                )
                company_domain = connector_config.auth.companyDomain or ""

                if not api_key:
                    raise ValueError(
                        "API key required for API_KEY auth type"
                    )
                if not company_domain:
                    raise ValueError(
                        "Company domain required for API_KEY auth type"
                    )

                api_key_config = BambooHRApiKeyConfig(
                    company_domain=company_domain,
                    api_key=api_key,
                )
                return cls(api_key_config.create_client())

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build BambooHR client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for BambooHR."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get BambooHR connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get BambooHR connector config: {e}")
            raise ValueError(
                f"Failed to get BambooHR connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
