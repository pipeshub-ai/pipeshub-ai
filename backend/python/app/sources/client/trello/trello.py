import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, SecretStr

try:
    from trello import TrelloClient as PyTrelloClient
except ImportError:
    raise ImportError(
        "py-trello is not installed. Please install it with `pip install py-trello`"
    )

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class TrelloResponse(BaseModel):
    """Standardized Trello API response wrapper"""

    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[Any] = Field(
        None, description="Response data from Trello API"
    )
    error: Optional[str] = Field(None, description="Error message if the call failed")
    message: Optional[str] = Field(None, description="Additional message information")

    class Config:
        """Pydantic configuration"""

        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"id": "507f1f77bcf86cd799439011", "name": "My Board"},
                "error": None,
                "message": None,
            }
        }

    def to_dict(self) -> Dict[str, object]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class TrelloRESTClient:
    """Trello client via API Key and Token

    This client wraps the py-trello Python library and provides
    authentication via API Key and Token.

    Args:
        api_key: API key from Trello Power-Ups admin
        api_token: Token generated via Trello authorization
    """

    def __init__(
        self,
        api_key: str,
        api_token: str,
    ) -> None:
        self._client: PyTrelloClient = PyTrelloClient(
            api_key=api_key,
            token=api_token,
        )

    def get_trello_client(self) -> PyTrelloClient:
        """Get the Trello client

        Returns:
            PyTrelloClient instance
        """
        return self._client


class TrelloApiKeyConfig(BaseModel):
    """Configuration for Trello client via API Key and Token

    Args:
        api_key: API key from Trello Power-Ups admin console
        api_token: Token generated via Trello authorization flow
    """

    api_key: SecretStr = Field(..., description="API key from Trello Power-Ups admin")
    api_token: SecretStr = Field(..., description="Token from Trello authorization")

    def create_client(self) -> "TrelloRESTClient":
        """Create a Trello client with API key and token authentication

        Returns:
            TrelloRESTClient instance
        """
        return TrelloRESTClient(
            api_key=self.api_key.get_secret_value(),
            api_token=self.api_token.get_secret_value(),
        )

    def to_dict(self) -> Dict[str, object]:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class TrelloClient(IClient):
    """Builder class for Trello clients with different construction methods

    This class provides a unified interface for creating Trello clients
    with API key and token authentication.
    """

    def __init__(
        self,
        client: TrelloRESTClient,
    ) -> None:
        """Initialize with a Trello client object

        Args:
            client: Trello REST client instance
        """
        self.client = client

    def get_client(self) -> TrelloRESTClient:
        """Return the underlying Trello client object

        Returns:
            Trello REST client instance
        """
        return self.client

    def get_trello_client(self) -> PyTrelloClient:
        """Get the py-trello client (SDK instance)

        Returns:
            PyTrelloClient instance
        """
        return self.client.get_trello_client()

    @classmethod
    def build_with_config(
        cls,
        config: TrelloApiKeyConfig,
    ) -> "TrelloClient":
        """Build TrelloClient with configuration

        Args:
            config: Trello configuration instance (Pydantic model)

        Returns:
            TrelloClient instance
        """
        client = config.create_client()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "TrelloClient":
        """Build TrelloClient using configuration service

        This method would typically:
        1. Use config_service to get environment-specific settings
        2. Fetch Trello API credentials from configuration
        3. Return appropriate client based on available credentials

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            TrelloClient instance

        Raises:
            NotImplementedError: This method requires platform-specific implementation
        """
        # TODO: Implement - fetch config from services
        logger.warning("TrelloClient.build_from_services not yet implemented")
        raise NotImplementedError(
            "build_from_services requires implementation with actual services"
        )
