from typing import Any, Dict, Optional

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class BookStackResponse(BaseModel):
    """Standardized BookStack API response wrapper"""
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


class BookStackRESTClientViaToken(HTTPClient):
    """BookStack REST client via Token ID and Token Secret
    BookStack uses a custom token authentication format:
    Authorization: Token <token_id>:<token_secret>
        base_url: The base URL of the BookStack instance
        token_id: The token ID from BookStack
        token_secret: The token secret from BookStack
    """

    def __init__(self, base_url: str, token_id: str, token_secret: str) -> None:
        # Combine token_id and token_secret into BookStack's format
        token = f"{token_id}:{token_secret}"
        # Initialize with the combined token and "Token" as the auth type
        super().__init__(token, "Token")
        self.base_url = base_url.rstrip('/')
        self.token_id = token_id
        self.token_secret = token_secret

        # Add BookStack-specific headers
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class BookStackTokenConfig(BaseModel):
    """Configuration for BookStack REST client via Token
    Args:
        base_url: The base URL of the BookStack instance
        token_id: The token ID from BookStack
        token_secret: The token secret from BookStack
        ssl: Whether to use SSL (default: True)
    """
    base_url: str
    token_id: str
    token_secret: str
    ssl: bool = True

    def create_client(self) -> BookStackRESTClientViaToken:
        """Create a BookStack client"""
        return BookStackRESTClientViaToken(self.base_url, self.token_id, self.token_secret)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class BookStackClient(IClient):
    """Builder class for BookStack clients with different construction methods"""

    def __init__(self, client: BookStackRESTClientViaToken) -> None:
        """Initialize with a BookStack client object"""
        self.client = client

    def get_client(self) -> BookStackRESTClientViaToken:
        """Return the BookStack client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(cls, config: BookStackTokenConfig) -> "BookStackClient":
        """Build BookStackClient with configuration
        Args:
            config: BookStackTokenConfig instance
        Returns:
            BookStackClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "BookStackClient":
        """Build BookStackClient using configuration service and graph database service
            config_service: Configuration service instance
            graph_db_service: Graph database service instance
        Returns:
            BookStackClient instance
        """
        # TODO: Implement - fetch config from services
        # This would typically:
        # 1. Query graph_db_service for stored BookStack credentials
        # 2. Use config_service to get environment-specific settings
        # 3. Return appropriate client based on available credentials

        raise NotImplementedError("build_from_services is not yet implemented")
