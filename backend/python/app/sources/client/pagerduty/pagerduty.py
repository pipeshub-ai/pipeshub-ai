"""PagerDuty REST API Client - Official SDK Integration.

This module provides a PagerDuty client using the official python-pagerduty SDK.
It follows the project's pattern (similar to Box and Asana) by wrapping the official SDK.

Architecture:
- Uses official RestApiV2Client from pagerduty SDK (>=5.0.0)
- Returns standardized PagerDutyResponse objects
- Supports Token authentication
- Type hints use proper SDK types
- Pydantic models for configuration

Reference: https://github.com/PagerDuty/python-pagerduty
Documentation: https://pagerduty.github.io/python-pagerduty/
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

try:
    from pagerduty import RestApiV2Client  # Official PagerDuty SDK
except ImportError:
    raise ImportError(
        "pagerduty is not installed. Please install it with `pip install pagerduty>=5.0.0`",
    )

logger = logging.getLogger(__name__)


class PagerDutyResponse(BaseModel):
    """Standardized PagerDuty API response wrapper."""

    success: bool = Field(..., description="Whether the API call was successful")
    data: dict[str, Any] | None = Field(
        None, description="Response data from PagerDuty API",
    )
    error: str | None = Field(None, description="Error message if the call failed")
    message: str | None = Field(None, description="Additional message information")

    class Config:
        """Pydantic configuration."""

        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"id": "PXXXXXX", "type": "incident"},
                "error": None,
                "message": None,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.model_dump_json()


class PagerDutyRESTClientViaToken:
    """PagerDuty REST client using API Token authentication.

    Wraps the official RestApiV2Client from pagerduty SDK.

    Args:
        api_token: The PagerDuty API token (starts with 'u+')
        base_url: Optional base URL for PagerDuty API (for non-US regions)

    """

    def __init__(
        self,
        api_token: str,
        base_url: str | None = None,
    ) -> None:
        """Initialize PagerDuty client configuration.

        Args:
            api_token: PagerDuty API token
            base_url: Optional custom base URL (default: https://api.pagerduty.com)

        """
        self.api_token = api_token
        self.base_url = base_url
        self._client: RestApiV2Client | None = None

    async def create_client(self) -> RestApiV2Client:
        """Create and initialize the PagerDuty SDK client.

        Returns:
            RestApiV2Client instance from official pagerduty SDK

        Raises:
            ValueError: If API token is invalid

        """
        # Validate token is not empty
        # Note: PagerDuty supports multiple token types:
        # - User tokens (start with 'u+')
        # - Account-level API keys (may start with 'v2_')
        # - OAuth access tokens (various formats)
        # The SDK will validate the token during API calls, so we only check for emptiness
        if not self.api_token:
            raise ValueError("PagerDuty API token cannot be empty")

        # Initialize official SDK client
        # Note: RestApiV2Client uses 'api_key' parameter
        self._client = RestApiV2Client(api_key=self.api_token)

        # Note: SDK doesn't support custom base_url in __init__
        # Warn if a custom base_url was provided but cannot be used
        if self.base_url:
            logger.warning(
                "A custom base_url was provided, but the PagerDuty SDK does not support it. "
                "Using the default URL (https://api.pagerduty.com).",
            )

        return self._client

    def get_sdk_client(self) -> RestApiV2Client:
        """Get the underlying PagerDuty SDK client.

        Returns:
            RestApiV2Client instance from official pagerduty SDK

        Raises:
            RuntimeError: If client not initialized

        """
        if self._client is None:
            raise RuntimeError(
                "Client not initialized. Call create_client() first.",
            )
        return self._client


class PagerDutyTokenConfig(BaseModel):
    """Configuration for PagerDuty REST client via API Token.

    Args:
        api_token: The PagerDuty API token
        base_url: Optional base URL for PagerDuty API

    """

    api_token: str = Field(..., description="PagerDuty API token (starts with 'u+')")
    base_url: str | None = Field(
        None, description="Optional base URL for PagerDuty API",
    )

    def create_client(self) -> PagerDutyRESTClientViaToken:
        """Create a PagerDuty REST client instance.

        Returns:
            PagerDutyRESTClientViaToken instance

        """
        return PagerDutyRESTClientViaToken(self.api_token, self.base_url)

    def to_dict(self) -> dict[str, Any]:
        """Convert the configuration to a dictionary.

        Returns:
            Dictionary representation of the configuration

        """
        return self.model_dump()


class PagerDutyClient(IClient):
    """Builder class for PagerDuty clients with different construction methods.

    This follows the same pattern as BoxClient and AsanaClient in the project.
    """

    def __init__(self, client: PagerDutyRESTClientViaToken) -> None:
        """Initialize with a PagerDuty client object.

        Args:
            client: PagerDutyRESTClientViaToken instance

        """
        self.client = client

    def get_client(self) -> PagerDutyRESTClientViaToken:
        """Return the PagerDuty client wrapper.

        Returns:
            PagerDutyRESTClientViaToken instance

        """
        return self.client

    def get_sdk_client(self) -> RestApiV2Client:
        """Return the underlying SDK client directly.

        Returns:
            RestApiV2Client from official pagerduty SDK

        Raises:
            RuntimeError: If client not initialized

        """
        return self.client.get_sdk_client()

    @classmethod
    async def build_with_config(cls, config: PagerDutyTokenConfig) -> "PagerDutyClient":
        """Build PagerDutyClient with configuration.

        Args:
            config: PagerDutyTokenConfig instance (Pydantic model)

        Returns:
            PagerDutyClient instance

        Example:
            >>> config = PagerDutyTokenConfig(api_token="u+YOUR_TOKEN")
            >>> client = await PagerDutyClient.build_with_config(config)
            >>> sdk = client.get_sdk_client()
            >>> response = sdk.get("/users/me")

        """
        client = config.create_client()
        await client.create_client()  # Initialize the SDK client
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "PagerDutyClient":
        """Build PagerDutyClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            PagerDutyClient instance

        Raises:
            ValueError: If configuration is invalid or missing

        """
        try:
            # Get PagerDuty configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get PagerDuty connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError(
                    "Auth configuration not found in PagerDuty connector configuration",
                )

            credentials_config = config.get("credentials", {}) or {}

            # Extract configuration values
            auth_type = auth_config.get("authType", "API_TOKEN")

            # Get token based on auth type
            token: str
            if auth_type == "API_TOKEN":
                token = auth_config.get("apiToken", "")
                if not token:
                    raise ValueError("API token required for API_TOKEN auth type")
            elif auth_type == "OAUTH":
                token = credentials_config.get("access_token", "")
                if not token:
                    raise ValueError("Access token required for OAuth auth type")
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            # Create and initialize client with token
            base_url = auth_config.get("baseUrl")
            client = PagerDutyRESTClientViaToken(token, base_url)
            await client.create_client()  # Initialize the SDK client

            return cls(client)

        except Exception:
            logger.exception("Failed to build PagerDuty client from services")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for PagerDuty.

        Args:
            logger: Logger instance
            config_service: Configuration service

        Returns:
            Configuration dictionary

        """
        try:
            config = await config_service.get_config("/services/connectors/pagerduty/config")
            return config or {}
        except (KeyError, ConnectionError, TimeoutError) as e:
            logger.exception(f"Failed to get PagerDuty connector config: {e}")
            return {}
        except Exception as e:
            logger.error(f"Unexpected error getting PagerDuty connector config: {e}")
            return {}

