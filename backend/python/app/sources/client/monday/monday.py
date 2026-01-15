"""Monday.com client implementation.

This module provides clients for interacting with Monday.com's API using either:
1. Official Monday.com Python SDK (monday-api-python-sdk) - Recommended
2. Direct GraphQL API access

Monday.com API Reference: https://developer.monday.com/api-reference/reference/about-the-api-reference
Official SDK: https://github.com/mondaycom/monday-api-python-sdk
"""

import logging
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient

# Try to import official Monday SDK
try:
    from monday_sdk import MondayClient as OfficialMondayClient  # type: ignore
    MONDAY_SDK_AVAILABLE = True
except ImportError:
    MONDAY_SDK_AVAILABLE = False
    OfficialMondayClient = None  # type: ignore


class MondayGraphQLClientViaToken(GraphQLClient):
    """Monday.com GraphQL client via API token.

    Uses Monday.com's API token authentication where the token is passed
    directly in the Authorization header.

    API Documentation: https://developer.monday.com/api-reference/docs/authentication
    """

    MONDAY_GRAPHQL_ENDPOINT = "https://api.monday.com/v2"

    def __init__(
        self,
        token: str,
        timeout: int = 30,
        api_version: Optional[str] = None,
    ) -> None:
        """Initialize Monday.com GraphQL client with API token.

        Args:
            token: Monday.com API token
            timeout: Request timeout in seconds
            api_version: Optional API version (e.g., "2025-04", "2025-07")
        """
        headers: Dict[str, str] = {
            "Authorization": token,
            "Content-Type": "application/json",
        }
        if api_version:
            headers["API-Version"] = api_version

        super().__init__(
            endpoint=self.MONDAY_GRAPHQL_ENDPOINT,
            headers=headers,
            timeout=timeout
        )
        self.token = token
        self.api_version = api_version

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint


class MondayGraphQLClientViaOAuth(GraphQLClient):
    """Monday.com GraphQL client via OAuth token.

    Uses Monday.com's OAuth2 authentication flow where an access token
    is obtained through the OAuth process.

    OAuth Documentation: https://developer.monday.com/apps/docs/oauth
    """

    MONDAY_GRAPHQL_ENDPOINT = "https://api.monday.com/v2"

    def __init__(
        self,
        oauth_token: str,
        timeout: int = 30,
        api_version: Optional[str] = None,
    ) -> None:
        """Initialize Monday.com GraphQL client with OAuth token.

        Args:
            oauth_token: OAuth access token obtained through Monday.com OAuth flow
            timeout: Request timeout in seconds
            api_version: Optional API version (e.g., "2025-04", "2025-07")
        """
        headers: Dict[str, str] = {
            "Authorization": oauth_token,
            "Content-Type": "application/json",
        }
        if api_version:
            headers["API-Version"] = api_version

        super().__init__(
            endpoint=self.MONDAY_GRAPHQL_ENDPOINT,
            headers=headers,
            timeout=timeout
        )
        self.oauth_token = oauth_token
        self.api_version = api_version

    def get_endpoint(self) -> str:
        """Get the GraphQL endpoint."""
        return self.endpoint


class MondayTokenConfig(BaseModel):
    """Configuration for Monday.com client via API token.

    Args:
        token: Monday.com API token
        timeout: Request timeout in seconds
        api_version: Optional API version string
        use_official_sdk: Whether to use official Monday SDK (default: True if available)
    """
    token: str = Field(..., description="Monday.com API token")
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)
    api_version: Optional[str] = Field(
        default=None,
        description="API version (e.g., '2025-04', '2025-07')"
    )
    use_official_sdk: bool = Field(
        default=True,
        description="Use official Monday SDK if available"
    )

    def create_graphql_client(self) -> MondayGraphQLClientViaToken:
        """Create a Monday.com GraphQL client with token authentication."""
        return MondayGraphQLClientViaToken(
            token=self.token,
            timeout=self.timeout,
            api_version=self.api_version,
        )

    def create_sdk_client(self) -> Any:
        """Create an official Monday SDK client."""
        if not MONDAY_SDK_AVAILABLE:
            raise ImportError(
                "Official Monday SDK not installed. "
                "Install with: pip install monday-api-python-sdk"
            )
        return OfficialMondayClient(token=self.token)  # pyright: ignore[reportOptionalCall]


class MondayOAuthConfig(BaseModel):
    """Configuration for Monday.com client via OAuth token.

    OAuth flow endpoints:
    - Authorize: https://auth.monday.com/oauth2/authorize
    - Token: https://auth.monday.com/oauth2/token

    Args:
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
        api_version: Optional API version string
        use_official_sdk: Whether to use official Monday SDK (default: True if available)
    """
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: int = Field(default=30, description="Request timeout in seconds", gt=0)
    api_version: Optional[str] = Field(
        default=None,
        description="API version (e.g., '2025-04', '2025-07')"
    )
    use_official_sdk: bool = Field(
        default=True,
        description="Use official Monday SDK if available"
    )

    def create_graphql_client(self) -> MondayGraphQLClientViaOAuth:
        """Create a Monday.com GraphQL client with OAuth authentication."""
        return MondayGraphQLClientViaOAuth(
            oauth_token=self.oauth_token,
            timeout=self.timeout,
            api_version=self.api_version,
        )

    def create_sdk_client(self) -> Any:
        """Create an official Monday SDK client."""
        if not MONDAY_SDK_AVAILABLE:
            raise ImportError(
                "Official Monday SDK not installed. "
                "Install with: pip install monday-api-python-sdk"
            )
        return OfficialMondayClient(token=self.oauth_token)  # pyright: ignore[reportOptionalCall]


class MondayClient(IClient):
    """Builder class for Monday.com clients with different construction methods.

    This class provides a unified interface for creating Monday.com clients
    using either API token or OAuth authentication. It supports both:
    - Official Monday SDK (monday-api-python-sdk) - Recommended for most use cases
    - Direct GraphQL API access - For advanced async operations

    Example usage with token:
        config = MondayTokenConfig(token="your_api_token")
        client = MondayClient.build_with_config(config)

        # Using official SDK
        sdk = client.get_sdk_client()
        items = sdk.boards.fetch_all_items_by_board_id(board_id="123")

        # Using GraphQL client for async operations
        graphql = client.get_graphql_client()
        response = await graphql.execute(query="{ me { name } }")

    Example usage with OAuth:
        config = MondayOAuthConfig(oauth_token="your_oauth_token")
        client = MondayClient.build_with_config(config)
    """

    def __init__(
        self,
        graphql_client: Union[MondayGraphQLClientViaToken, MondayGraphQLClientViaOAuth],
        sdk_client: Optional[Any] = None,
        token: Optional[str] = None,
    ) -> None:
        """Initialize with Monday.com clients.

        Args:
            graphql_client: GraphQL client for async operations
            sdk_client: Official Monday SDK client (optional)
            token: API token for creating SDK client on demand
        """
        self._graphql_client = graphql_client
        self._sdk_client = sdk_client
        self._token = token

    def get_client(self) -> Union[MondayGraphQLClientViaToken, MondayGraphQLClientViaOAuth]:
        """Return the Monday.com GraphQL client object (for backwards compatibility)."""
        return self._graphql_client

    def get_graphql_client(self) -> Union[MondayGraphQLClientViaToken, MondayGraphQLClientViaOAuth]:
        """Return the Monday.com GraphQL client for async operations."""
        return self._graphql_client

    def get_sdk_client(self) -> Any:
        """Return the official Monday SDK client.

        Returns:
            Official MondayClient from monday-api-python-sdk

        Raises:
            ImportError: If official SDK is not installed
        """
        if self._sdk_client is not None:
            return self._sdk_client

        if not MONDAY_SDK_AVAILABLE:
            raise ImportError(
                "Official Monday SDK not installed. "
                "Install with: pip install monday-api-python-sdk"
            )

        if self._token:
            self._sdk_client = OfficialMondayClient(token=self._token)  # pyright: ignore[reportOptionalCall]
            return self._sdk_client

        raise ValueError("No token available to create SDK client")

    def is_sdk_available(self) -> bool:
        """Check if official Monday SDK is available."""
        return MONDAY_SDK_AVAILABLE

    @classmethod
    def build_with_config(
        cls,
        config: Union[MondayTokenConfig, MondayOAuthConfig],
    ) -> "MondayClient":
        """Build MondayClient with configuration.

        Args:
            config: Monday.com configuration instance (token or OAuth)

        Returns:
            MondayClient instance
        """
        graphql_client = config.create_graphql_client()

        sdk_client = None
        token = None

        if isinstance(config, MondayTokenConfig):
            token = config.token
        elif isinstance(config, MondayOAuthConfig):
            token = config.oauth_token

        if config.use_official_sdk and MONDAY_SDK_AVAILABLE:
            try:
                sdk_client = config.create_sdk_client()
            except Exception:
                pass  # Fall back to GraphQL only

        return cls(
            graphql_client=graphql_client,
            sdk_client=sdk_client,
            token=token,
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "MondayClient":
        """Build MondayClient using configuration service.

        This method retrieves Monday.com connector configuration from
        the configuration service (etcd) and creates the appropriate client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            MondayClient instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            if not config:
                raise ValueError("Failed to get Monday.com connector configuration")

            auth_config_raw = config.get("auth", {})
            auth_config: Dict[str, Any] = auth_config_raw if isinstance(auth_config_raw, dict) else {}
            auth_type: str = str(auth_config.get("authType", "API_TOKEN"))
            timeout: int = int(config.get("timeout", 30))
            api_version: Optional[str] = config.get("apiVersion")

            if auth_type == "OAUTH":
                credentials_raw = config.get("credentials", {})
                credentials_config: Dict[str, Any] = credentials_raw if isinstance(credentials_raw, dict) else {}
                oauth_token: str = str(credentials_config.get("access_token", ""))
                if not oauth_token:
                    raise ValueError("OAuth token required for OAuth auth type")

                graphql_client = MondayGraphQLClientViaOAuth(
                    oauth_token=oauth_token,
                    timeout=timeout,
                    api_version=api_version,
                )
                token = oauth_token

            elif auth_type == "API_TOKEN":
                token = str(auth_config.get("apiToken", ""))
                if not token:
                    raise ValueError("API token required for token auth type")

                graphql_client = MondayGraphQLClientViaToken(
                    token=token,
                    timeout=timeout,
                    api_version=api_version,
                )

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            sdk_client = None
            if MONDAY_SDK_AVAILABLE:
                try:
                    sdk_client = OfficialMondayClient(token=token)  # pyright: ignore[reportOptionalCall]
                except Exception:
                    pass

            return cls(
                graphql_client=graphql_client,
                sdk_client=sdk_client,
                token=token,
            )

        except Exception as e:
            logger.error(f"Failed to build Monday.com client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Monday.com.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID

        Returns:
            Configuration dictionary

        Raises:
            ValueError: If configuration cannot be retrieved
        """
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Monday.com connector configuration "
                    f"for instance {connector_instance_id}"
                )
            if not isinstance(config, dict):
                raise ValueError(
                    f"Invalid Monday.com connector configuration format "
                    f"for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Monday.com connector config: {e}")
            raise ValueError(
                f"Failed to get Monday.com connector configuration "
                f"for instance {connector_instance_id}"
            )


# Response wrapper for consistent API responses
class MondayResponse(BaseModel):
    """Standard response wrapper for Monday.com API calls."""
    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Response data")
    error: Optional[str] = Field(default=None, description="Error code if failed")
    message: Optional[str] = Field(default=None, description="Error message if failed")

    class Config:
        """Pydantic configuration."""
        extra = "allow"
