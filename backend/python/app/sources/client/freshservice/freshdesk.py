import base64
from typing import Any, Dict, Optional

from pydantic import BaseModel, field_validator  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class FreshserviceConfigurationError(Exception):
    """Custom exception for Freshservice configuration errors"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class FreshserviceResponse(BaseModel):
    """Standardized Freshservice API response wrapper"""
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


class FreshserviceRESTClientViaApiKey(HTTPClient):
    """Freshservice REST client via API key

    Freshservice uses Basic Authentication with API Key as username and 'X' as password

    Args:
        domain: The Freshservice domain (e.g., 'company.freshservice.com')
        api_key: The API key to use for authentication
    """

    def __init__(self, domain: str, api_key: str) -> None:
        # Freshservice uses Basic auth with API key as username, 'X' as password
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
        """Get the Freshservice domain"""
        return self.domain


class FreshserviceApiKeyConfig(BaseModel):
    """Configuration for Freshservice REST client via API Key

    Args:
        domain: The Freshservice domain (e.g., 'company.freshservice.com')
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

    def create_client(self) -> FreshserviceRESTClientViaApiKey:
        """Create Freshservice REST client"""
        return FreshserviceRESTClientViaApiKey(self.domain, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return {
            'domain': self.domain,
            'ssl': self.ssl,
            'has_api_key': bool(self.api_key)
        }


class FreshserviceClient(IClient):
    """Builder class for Freshservice clients"""

    def __init__(self, client: FreshserviceRESTClientViaApiKey) -> None:
        """Initialize with a Freshservice client object"""
        self.client = client

    def get_client(self) -> FreshserviceRESTClientViaApiKey:
        """Return the Freshservice REST client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    def get_domain(self) -> str:
        """Get the Freshservice domain"""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: FreshserviceApiKeyConfig,
    ) -> "FreshserviceClient":
        """Build FreshserviceClient with configuration

        Args:
            config: FreshserviceApiKeyConfig instance
        Returns:
            FreshserviceClient instance
        """
        return cls(config.create_client())

    @classmethod
    def build_with_api_key_config(cls, config: FreshserviceApiKeyConfig) -> "FreshserviceClient":
        """Build FreshserviceClient with API key configuration

        Args:
            config: FreshserviceApiKeyConfig instance

        Returns:
            FreshserviceClient: Configured client instance
        """
        return cls.build_with_config(config)

    @classmethod
    def build_with_api_key(
        cls,
        domain: str,
        api_key: str,
        ssl: bool = True
    ) -> "FreshserviceClient":
        """Build FreshserviceClient with API key directly

        Args:
            domain: The Freshservice domain (e.g., 'company.freshservice.com')
            api_key: The API key for authentication
            ssl: Whether to use SSL (default: True)

        Returns:
            FreshserviceClient: Configured client instance
        """
        config = FreshserviceApiKeyConfig(
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
        graph_db_service: IGraphService,
        org_id: str,
        user_id: str,
    ) -> "FreshserviceClient":
        """Build FreshserviceClient using configuration service and graphdb service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: GraphDB service instance
            org_id: Organization ID
            user_id: User ID

        Returns:
            FreshserviceClient: Configured client instance

        Raises:
            NotImplementedError: This method needs to be implemented
        """
        # TODO: Implement - fetch config from services
        # This would typically:
        # 1. Query graph_db_service for stored Freshservice credentials
        # 2. Use config_service to get environment-specific settings
        # 3. Return appropriate client based on available credentials
        raise NotImplementedError(
            "build_from_services is not implemented for FreshserviceClient"
        )
