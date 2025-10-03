import logging
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field  #type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient


class LinearGraphQLClientViaToken(GraphQLClient):
    """Linear GraphQL client via API token."""

    def __init__(self, token: str, timeout: int = 30) -> None:
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
        }
        super().__init__(
            endpoint="https://api.linear.app/graphql",
            headers=headers,
            timeout=timeout
        )
        self.token = token

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint


class LinearGraphQLClientViaOAuth(GraphQLClient):
    """Linear GraphQL client via OAuth token."""

    def __init__(self, oauth_token: str, timeout: int = 30) -> None:
        headers = {
            "Authorization": oauth_token,
            "Content-Type": "application/json",
        }
        super().__init__(
            endpoint="https://api.linear.app/graphql",
            headers=headers,
            timeout=timeout
        )
        self.oauth_token = oauth_token

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint

class LinearTokenConfig(BaseModel):
    """Configuration for Linear GraphQL client via API token.
    Args:
        token: Linear API token
        timeout: Request timeout in seconds
        endpoint: GraphQL endpoint (defaults to Linear's endpoint)
    """
    token: str = Field(..., description="Linear API token")
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)
    endpoint: str = Field(default="https://api.linear.app/graphql", description="GraphQL endpoint URL")

    def create_client(self) -> LinearGraphQLClientViaToken:
        """Create a Linear GraphQL client."""
        return LinearGraphQLClientViaToken(self.token, self.timeout)


class LinearOAuthConfig(BaseModel):
    """Configuration for Linear GraphQL client via OAuth token.
    Args:
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
        endpoint: GraphQL endpoint (defaults to Linear's endpoint)
    """
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)
    endpoint: str = Field(default="https://api.linear.app/graphql", description="GraphQL endpoint URL")

    def create_client(self) -> LinearGraphQLClientViaOAuth:
        """Create a Linear GraphQL client."""
        return LinearGraphQLClientViaOAuth(self.oauth_token, self.timeout)


class LinearClient(IClient):
    """Builder class for Linear GraphQL clients with different construction methods."""

    def __init__(
        self,
        client: Union[LinearGraphQLClientViaToken, LinearGraphQLClientViaOAuth],
    ) -> None:
        """Initialize with a Linear GraphQL client object."""
        self.client = client

    def get_client(self) -> Union[LinearGraphQLClientViaToken, LinearGraphQLClientViaOAuth]:
        """Return the Linear GraphQL client object."""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: Union[LinearTokenConfig, LinearOAuthConfig],
    ) -> "LinearClient":
        """Build LinearClient with configuration.

        Args:
            config: Linear configuration instance
        Returns:
            LinearClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        arango_service: Optional[IGraphService] = None,
    ) -> "LinearClient":
        """Build LinearClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: Graph database service instance (optional)
        Returns:
            LinearClient instance
        """
        try:
            # Get Linear configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Linear connector configuration")

            # Extract configuration values
            auth_type = config.get("auth_type", "token")  # token or oauth
            timeout = config.get("timeout", 30)

            # Create appropriate client based on auth type
            if auth_type == "oauth":
                oauth_token = config.get("oauth_token", "")
                if not oauth_token:
                    raise ValueError("OAuth token required for oauth auth type")
                client = LinearGraphQLClientViaOAuth(oauth_token, timeout)

            else:  # Default to token auth
                token = config.get("token", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = LinearGraphQLClientViaToken(token, timeout)

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Linear client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Linear."""
        try:
            config = await config_service.get_config("/services/connectors/linear/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Linear connector config: {e}")
            return {}

