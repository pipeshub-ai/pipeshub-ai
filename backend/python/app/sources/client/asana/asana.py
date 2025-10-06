import logging
from typing import Any, Dict, Generator, Optional, Union

from pydantic import BaseModel, Field

try:
    import asana
except ImportError:
    raise ImportError(
        "asana is not installed. Please install it with `pip install asana`"
    )

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.iclient import IClient


class AsanaResponse(BaseModel):
    """Standardized Asana API response wrapper"""

    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[Union[Dict[str, Any], Generator]] = Field(
        None, description="Response data from Asana API (dict or generator)"
    )
    error: Optional[str] = Field(None, description="Error message if the call failed")
    message: Optional[str] = Field(None, description="Additional message information")

    class Config:
        """Pydantic configuration"""

        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"gid": "123456789", "name": "Example Task"},
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


class AsanaClientViaToken:
    """Asana client via Personal Access Token (PAT)

    This client wraps the official Asana Python SDK and provides
    authentication via Personal Access Token.

    Args:
        access_token: Personal access token from Asana developer console
        return_page_iterator: Whether to return page iterator for list endpoints (default: True)
    """

    def __init__(
        self,
        access_token: str,
        return_page_iterator: bool = True,
    ) -> None:
        self.access_token = access_token
        self.return_page_iterator = return_page_iterator
        self._configuration: Optional[asana.Configuration] = None
        self._api_client: Optional[asana.ApiClient] = None

    def create_client(self) -> asana.ApiClient:
        """Create and configure the Asana API client

        Returns:
            Configured asana.ApiClient instance
        """
        # Create configuration
        configuration = asana.Configuration()
        configuration.access_token = self.access_token
        configuration.return_page_iterator = self.return_page_iterator

        self._configuration = configuration

        # Create API client
        self._api_client = asana.ApiClient(configuration)

        return self._api_client

    def get_api_client(self) -> asana.ApiClient:
        """Get the Asana API client

        Returns:
            asana.ApiClient instance

        Raises:
            RuntimeError: If client not initialized
        """
        if self._api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._api_client

    def get_configuration(self) -> asana.Configuration:
        """Get the Asana configuration

        Returns:
            asana.Configuration instance

        Raises:
            RuntimeError: If configuration not initialized
        """
        if self._configuration is None:
            raise RuntimeError(
                "Configuration not initialized. Call create_client() first."
            )
        return self._configuration


class AsanaTokenConfig(BaseModel):
    """Configuration for Asana client via Personal Access Token

    Args:
        access_token: Personal access token from Asana
        return_page_iterator: Whether to return page iterator for list endpoints
    """

    access_token: str = Field(..., description="Personal access token from Asana")
    return_page_iterator: bool = Field(
        default=True, description="Whether to return page iterator for list endpoints"
    )

    def create_client(self) -> AsanaClientViaToken:
        """Create an Asana client with token authentication

        Returns:
            AsanaClientViaToken instance
        """
        return AsanaClientViaToken(
            access_token=self.access_token,
            return_page_iterator=self.return_page_iterator,
        )

    def to_dict(self) -> Dict[str, object]:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class AsanaClient(IClient):
    """Builder class for Asana clients with different construction methods

    This class provides a unified interface for creating Asana clients
    with different authentication methods (PAT or OAuth).
    """

    def __init__(
        self,
        client: AsanaClientViaToken,
    ) -> None:
        """Initialize with an Asana client object

        Args:
            client: Asana REST client instance
        """
        self.client = client

    def get_client(self) -> AsanaClientViaToken:
        """Return the underlying Asana client object

        Returns:
            Asana REST client instance
        """
        return self.client

    def get_api_client(self) -> asana.ApiClient:
        """Get the Asana API client (SDK instance)

        Returns:
            asana.ApiClient instance
        """
        return self.client.get_api_client()

    @classmethod
    def build_with_config(
        cls,
        config: AsanaTokenConfig,
    ) -> "AsanaClient":
        """Build AsanaClient with configuration

        Args:
            config: Asana configuration instance (Pydantic model)

        Returns:
            AsanaClient instance
        """
        client = config.create_client()
        client.create_client()  # Initialize the SDK client
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "AsanaClient":
        """Build AsanaClient using configuration service and graph database service

        This method would typically:
        1. Query graph_db_service for stored Asana credentials
        2. Use config_service to get environment-specific settings
        3. Return appropriate client based on available credentials (token)

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: Graph database service instance

        Returns:
            AsanaClient instance

        Raises:
            NotImplementedError: This method requires platform-specific implementation
        """
        # TODO: Implement - fetch config from services
        logger.warning("AsanaClient.build_from_services not yet implemented")
        raise NotImplementedError(
            "build_from_services requires implementation with actual services"
        )
