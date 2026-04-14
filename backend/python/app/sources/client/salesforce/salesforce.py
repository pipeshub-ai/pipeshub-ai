import logging
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.api.routes.toolsets import get_oauth_credentials_for_toolset
from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class SalesforceResponse(BaseModel):
    """Standardized Salesforce API response wrapper"""
    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[Union[Dict[str, Any], list[Any]]] = Field(
        None, description="Response data from Salesforce API"
    )
    error: Optional[str] = Field(None, description="Error message if the call failed")
    message: Optional[str] = Field(None, description="Additional message information")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class SalesforceRESTClient(HTTPClient):
    """Salesforce REST client via Bearer Token (OAuth Access Token)

    Args:
        instance_url: The Salesforce instance URL (e.g., https://my-domain.my.salesforce.com)
        access_token: The OAuth access token
        api_version: The Salesforce API version (default: '59.0')
    """

    def __init__(self, instance_url: str, access_token: str, api_version: str = "59.0", refresh_token: str = None) -> None:
        super().__init__(access_token, "Bearer")

        # Ensure instance_url doesn't end with a slash
        self.instance_url = instance_url.rstrip('/')
        self.api_version = api_version
        self.refresh_token = refresh_token
        # Construct the base API URL
        # Format: https://instance.salesforce.com/services/data/vXX.X
        self.base_url = self.instance_url

        # Add Salesforce-specific headers
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the raw instance URL"""
        return self.instance_url


class SalesforceConfig(BaseModel):
    """Configuration for Salesforce REST client

    Args:
        instance_url: The Salesforce instance URL
        access_token: The OAuth access token
        api_version: API version to use (default: 59.0)
    """
    instance_url: str = Field(..., description="The Salesforce instance URL")
    access_token: str = Field(..., description="The OAuth access token")
    api_version: str = Field(default="59.0", description="The Salesforce API version")
    refresh_token: str | None = Field(
        default=None, description="The OAuth refresh token"
    )

    @field_validator('instance_url')
    @classmethod
    def validate_instance_url(cls, v: str) -> str:
        if not v:
            raise ValueError("instance_url cannot be empty")
        if not v.startswith(('http://', 'https://')):
            # Assume https if protocol not provided
            return f"https://{v}"
        return v

    def create_client(self) -> SalesforceRESTClient:
        """Create Salesforce REST client"""
        return SalesforceRESTClient(
            instance_url=self.instance_url,
            access_token=self.access_token,
            api_version=self.api_version,
            refresh_token=self.refresh_token,
        )

    def to_dict(self) -> dict:
        """Convert configuration to dictionary"""
        return self.model_dump()


class SalesforceClient(IClient):
    """Builder class for Salesforce clients"""

    def __init__(self, client: SalesforceRESTClient) -> None:
        """Initialize with a Salesforce client object"""
        self.client = client

    def get_client(self) -> SalesforceRESTClient:
        """Return the Salesforce client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(cls, config: SalesforceConfig) -> "SalesforceClient":
        """Build SalesforceClient with configuration

        Args:
            config: SalesforceConfig instance
        Returns:
            SalesforceClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "SalesforceClient":
        """Build SalesforceClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            SalesforceClient instance
        """
        try:
            # Get Salesforce configuration from the configuration service
            config_data = await cls._get_connector_config(logger, config_service)
            if not config_data:
                raise ValueError("Failed to get Salesforce connector configuration")

            # Extract auth configuration
            auth_config = config_data.get("auth", {})
            auth_type = config_data.get("authType", "OAUTH") # Default to OAUTH

            # For Salesforce, we typically need the instance URL and the access token
            # regardless of whether the flow was OAUTH or a manual TOKEN entry.

            if auth_type == "OAUTH":
                credentials = auth_config.get("credentials", {})
                access_token = credentials.get("accessToken") or auth_config.get("accessToken")
                instance_url = credentials.get("instanceUrl") or auth_config.get("instanceUrl")
            elif auth_type == "ACCESS_TOKEN":
                access_token = auth_config.get("accessToken")
                instance_url = auth_config.get("instanceUrl")
            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")
            if not access_token:
                raise ValueError("Access token is required")
            if not instance_url:
                raise ValueError("Instance URL is required")
            config = SalesforceConfig(
                instance_url=instance_url,
                access_token=access_token,
                api_version=config_data.get("apiVersion", "59.0")
            )

            return cls.build_with_config(config)

        except Exception as e:
            logger.error(f"Failed to build Salesforce client from services: {str(e)}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        logger: logging.Logger,
        config_service: Optional["ConfigurationService"] = None,
    ) -> "SalesforceClient":
        """Build SalesforceClient from toolset configuration (agent architecture).

        The access_token and refresh_token come from per-user credentials.
        The instance_url is a CONFIGURE-level field: read from the linked OAuth app
        config (/services/oauths/toolsets/...), with fallback to instance.auth for
        legacy instances without oauthConfigId.

        Args:
            toolset_config: Toolset configuration dictionary from etcd
            logger: Logger instance
            config_service: Required; used to resolve OAuth app config and instance record

        Returns:
            SalesforceClient instance
        """
        try:
            if not toolset_config:
                raise ValueError("Toolset config is required for Salesforce client")

            if not config_service:
                raise ValueError(
                    "ConfigurationService is required to resolve Salesforce instance URL for toolsets."
                )

            credentials_config = toolset_config.get("credentials", {}) or {}
            access_token = credentials_config.get("access_token")
            if not access_token:
                raise ValueError("Access token required for Salesforce client (OAuth)")

            refresh_token = credentials_config.get("refresh_token")

            oauth_config = await get_oauth_credentials_for_toolset(
                toolset_config=toolset_config,
                config_service=config_service,
                logger=logger,
            )
            instance_url = oauth_config.get("instance_url")
            if isinstance(instance_url, str):
                instance_url = instance_url.strip()

            if not instance_url:
                raise ValueError(
                    "Instance URL required for Salesforce client. "
                    "Admin must configure the Salesforce Instance URL in toolset settings."
                )

            config = SalesforceConfig(
                instance_url=instance_url,
                access_token=access_token,
                api_version=toolset_config.get("api_version", "59.0"),
                refresh_token=refresh_token,
            )

            logger.info("Built Salesforce client from toolset config")
            return cls.build_with_config(config)

        except Exception as e:
            logger.error(f"Failed to build Salesforce client from toolset config: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Salesforce."""
        try:
            config = await config_service.get_config("/services/connectors/salesforce/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Salesforce connector config: {e}")
            raise
