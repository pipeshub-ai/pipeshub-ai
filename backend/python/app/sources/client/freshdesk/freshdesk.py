import base64
from typing import Any, Dict, Optional

from pydantic import BaseModel, field_validator  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class FreshDeskConfigurationError(Exception):
    """Custom exception for FreshDesk configuration errors"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class FreshDeskResponse(BaseModel):
    """Standardized FreshDesk API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class FreshDeskRESTClientViaApiKey(HTTPClient):
    """FreshDesk REST client via API key

    FreshDesk uses Basic Authentication with API Key as username and 'X' as password

    Args:
        domain: The FreshDesk domain (e.g., 'company.FreshDesk.com')
        api_key: The API key to use for authentication
    """

    def __init__(self, domain: str, api_key: str) -> None:
        # FreshDesk uses Basic auth with API key as username, 'X' as password
        # Encode as base64 for Basic auth
        credentials = f"{api_key}:X"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize HTTPClient with Basic token
        super().__init__(encoded_credentials, "Basic")
        self.domain = domain
        self.base_url = f"https://{domain}/api/v2"
        self.api_key = api_key

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_domain(self) -> str:
        """Get the FreshDesk domain"""
        return self.domain


class FreshDeskApiKeyConfig(BaseModel):
    """Configuration for FreshDesk REST client via API Key

    Args:
        domain: The FreshDesk domain (e.g., 'company.FreshDesk.com')
        api_key: The API key for authentication
        ssl: Whether to use SSL (default: True)
    """
    domain: str
    api_key: str
    ssl: bool = True

    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Validate domain field"""
        if not v or not v.strip():
            raise ValueError("domain cannot be empty or None")

        # Validate domain format - should not include protocol
        if v.startswith(('http://', 'https://')):
            raise ValueError("domain should not include protocol (http:// or https://)")

        return v

    @field_validator('api_key')
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate api_key field"""
        if not v or not v.strip():
            raise ValueError("api_key cannot be empty or None")

        return v

    def create_client(self) -> FreshDeskRESTClientViaApiKey:
        """Create FreshDesk REST client"""
        return FreshDeskRESTClientViaApiKey(self.domain, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            'domain': self.domain,
            'ssl': self.ssl,
            'has_api_key': bool(self.api_key)
        }


class FreshDeskClient(IClient):
    """Builder class for FreshDesk clients"""

    def __init__(self, client: FreshDeskRESTClientViaApiKey) -> None:
        """Initialize with a FreshDesk client object"""
        self.client = client

    def get_client(self) -> FreshDeskRESTClientViaApiKey:
        """Return the FreshDesk REST client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    def get_domain(self) -> str:
        """Get the FreshDesk domain"""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: FreshDeskApiKeyConfig,
    ) -> "FreshDeskClient":
        """Build FreshDeskClient with configuration

        Args:
            config: FreshDeskApiKeyConfig instance
        Returns:
            FreshDeskClient instance
        """
        return cls(config.create_client())

    @classmethod
    def build_with_api_key_config(cls, config: FreshDeskApiKeyConfig) -> "FreshDeskClient":
        """Build FreshDeskClient with API key configuration

        Args:
            config: FreshDeskApiKeyConfig instance

        Returns:
            FreshDeskClient: Configured client instance
        """
        return cls.build_with_config(config)

    @classmethod
    def build_with_api_key(
        cls,
        domain: str,
        api_key: str,
        ssl: bool = True
    ) -> "FreshDeskClient":
        """Build FreshDeskClient with API key directly

        Args:
            domain: The FreshDesk domain (e.g., 'company.FreshDesk.com')
            api_key: The API key for authentication
            ssl: Whether to use SSL (default: True)

        Returns:
            FreshDeskClient: Configured client instance
        """
        config = FreshDeskApiKeyConfig(
            domain=domain,
            api_key=api_key,
            ssl=ssl
        )
        return cls.build_with_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
    ) -> "FreshDeskClient":
        """Build FreshDeskClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            FreshDeskClient: Configured client instance

        Raises:
            NotImplementedError: This method needs to be implemented
        """
        config = await cls._get_connector_config(logger, config_service)
        if not config:
            raise ValueError("Failed to get FreshDesk connector configuration")
        auth_type = config.get("authType", "API_KEY")  # API_KEY or OAUTH
        auth_config = config.get("auth", {})
        if auth_type == "API_KEY":
            api_key = auth_config.get("apiKey", "")
            domain = auth_config.get("domain", "")
            if not api_key:
                raise ValueError("API key required for API key auth type")
            client = FreshDeskApiKeyConfig(domain=domain, api_key=api_key).create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for FreshDesk."""
        try:
            config = await config_service.get_config("/services/connectors/freshdesk/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get FreshDesk connector config: {e}")
            return {}
