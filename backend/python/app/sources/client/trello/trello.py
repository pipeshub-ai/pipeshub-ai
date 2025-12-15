"""Trello Client with OAuth Authentication.

This module provides OAuth-based authentication for the Trello API
using the py-trello SDK library.
"""

import logging
from typing import Dict, Optional

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
    """Standardized Trello API response wrapper."""

    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[object] = Field(None, description="Response data from Trello API")
    error: Optional[str] = Field(None, description="Error message if the call failed")
    message: Optional[str] = Field(None, description="Additional message information")

    class Config:
        """Pydantic configuration."""

        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"id": "507f1f77bcf86cd799439011", "name": "My Board"},
                "error": None,
                "message": None,
            }
        }

    def to_dict(self) -> Dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.model_dump_json()


class TrelloRESTClient:
    """Trello client via OAuth.

    This client wraps the py-trello Python library and provides
    authentication via OAuth (api_key, api_secret, token, token_secret).

    Args:
        api_key: API key from Trello Power-Ups admin
        api_secret: API secret from Trello Power-Ups admin
        oauth_token: OAuth token obtained after user authorization
        oauth_token_secret: OAuth token secret obtained after user authorization
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        oauth_token: str,
        oauth_token_secret: str,
    ) -> None:
        """Initialize the Trello client with OAuth credentials."""
        self._client: PyTrelloClient = PyTrelloClient(
            api_key=api_key,
            api_secret=api_secret,
            token=oauth_token,
            token_secret=oauth_token_secret,
        )

    def get_trello_client(self) -> PyTrelloClient:
        """Get the Trello client.

        Returns:
            PyTrelloClient instance
        """
        return self._client


class TrelloOAuthConfig(BaseModel):
    """Configuration for Trello client via OAuth.

    OAuth allows multi-user scenarios where each user authorizes
    the application and receives their own token pair.

    Args:
        api_key: API key from Trello Power-Ups admin console
        api_secret: API secret from Trello Power-Ups admin console
        oauth_token: OAuth token from user authorization flow
        oauth_token_secret: OAuth token secret from user authorization flow
    """

    api_key: SecretStr = Field(..., description="API key from Trello Power-Ups admin")
    api_secret: SecretStr = Field(
        ..., description="API secret from Trello Power-Ups admin"
    )
    oauth_token: SecretStr = Field(
        ..., description="OAuth token from user authorization"
    )
    oauth_token_secret: SecretStr = Field(
        ..., description="OAuth token secret from user authorization"
    )

    def create_client(self) -> "TrelloRESTClient":
        """Create a Trello client with OAuth authentication.

        Returns:
            TrelloRESTClient instance
        """
        return TrelloRESTClient(
            api_key=self.api_key.get_secret_value(),
            api_secret=self.api_secret.get_secret_value(),
            oauth_token=self.oauth_token.get_secret_value(),
            oauth_token_secret=self.oauth_token_secret.get_secret_value(),
        )

    def to_dict(self) -> Dict[str, object]:
        """Convert the configuration to a dictionary."""
        return self.model_dump()


class TrelloClient(IClient):
    """Builder class for Trello clients with OAuth authentication.

    This class provides a unified interface for creating Trello clients
    with OAuth authentication, enabling multi-user scenarios.

    Example:
        >>> config = TrelloOAuthConfig(
        ...     api_key="your_key",
        ...     api_secret="your_secret",
        ...     oauth_token="user_token",
        ...     oauth_token_secret="user_token_secret"
        ... )
        >>> client = TrelloClient.build_with_config(config)
    """

    def __init__(
        self,
        client: TrelloRESTClient,
    ) -> None:
        """Initialize with a Trello client object.

        Args:
            client: Trello REST client instance
        """
        self.client = client

    def get_client(self) -> TrelloRESTClient:
        """Return the underlying Trello client object.

        Returns:
            Trello REST client instance
        """
        return self.client

    def get_trello_client(self) -> PyTrelloClient:
        """Get the py-trello client (SDK instance).

        Returns:
            PyTrelloClient instance
        """
        return self.client.get_trello_client()

    @classmethod
    def build_with_config(
        cls,
        config: TrelloOAuthConfig,
    ) -> "TrelloClient":
        """Build TrelloClient with OAuth configuration.

        Args:
            config: Trello OAuth configuration instance (Pydantic model)

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
        """Build TrelloClient using configuration service.

        This method would typically:
        1. Use config_service to get environment-specific settings
        2. Fetch Trello OAuth credentials from configuration
        3. Return appropriate client based on available credentials

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            TrelloClient instance

        Raises:
            NotImplementedError: This method requires platform-specific implementation
        """
        # TODO: Implement - fetch OAuth config from services
        logger.warning("TrelloClient.build_from_services not yet implemented")
        raise NotImplementedError(
            "build_from_services requires implementation with actual services"
        )
