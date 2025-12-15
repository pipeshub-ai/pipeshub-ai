"""Trello Client with OAuth Authentication using direct HTTP calls.

This module provides OAuth-based authentication for the Trello API
using direct HTTP requests instead of third-party SDKs.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


@dataclass
class TrelloResponse:
    """Standardized Trello API response wrapper."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class TrelloRESTClient(HTTPClient):
    """Trello REST client via OAuth using direct HTTP calls.

    This client uses the Trello REST API directly with OAuth credentials.
    No third-party SDK is used.

    Args:
        api_key: API key from Trello Power-Ups admin
        api_secret: API secret from Trello Power-Ups admin
        oauth_token: OAuth token obtained after user authorization
    """

    TRELLO_API_BASE_URL = "https://api.trello.com/1"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        oauth_token: str,
    ) -> None:
        """Initialize the Trello client with OAuth credentials."""
        # Initialize HTTPClient with OAuth token
        super().__init__(oauth_token, "OAuth")

        self.api_key = api_key
        self.api_secret = api_secret
        self.oauth_token = oauth_token

        # Update headers for Trello API
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def get_base_url(self) -> str:
        """Get the Trello API base URL."""
        return self.TRELLO_API_BASE_URL

    def _build_auth_params(self) -> Dict[str, str]:
        """Build authentication query parameters."""
        return {
            "key": self.api_key,
            "token": self.oauth_token,
        }

    async def make_request(
        self,
        method: str,
        endpoint: str,
        query_params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> TrelloResponse:
        """Make an authenticated request to the Trello API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (e.g., "/members/me")
            query_params: Optional query parameters
            body: Optional request body

        Returns:
            TrelloResponse with success status and data or error
        """
        url = f"{self.TRELLO_API_BASE_URL}{endpoint}"

        # Merge auth params with query params
        params = self._build_auth_params()
        if query_params:
            params.update(query_params)

        request = HTTPRequest(
            url=url,
            method=method,
            query_params=params,
            headers={"Content-Type": "application/json"},
            body=body,
        )

        try:
            response = await self.execute(request)

            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                return TrelloResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text}",
                )

            return TrelloResponse(
                success=True,
                data=response.json(),
            )
        except Exception as e:
            return TrelloResponse(
                success=False,
                error=str(e),
            )


@dataclass
class TrelloOAuthConfig:
    """Configuration for Trello client via OAuth.

    OAuth allows multi-user scenarios where each user authorizes
    the application and receives their own token.

    Args:
        api_key: API key from Trello Power-Ups admin console
        api_secret: API secret from Trello Power-Ups admin console
        oauth_token: OAuth token from user authorization flow
    """

    api_key: str
    api_secret: str
    oauth_token: str

    def create_client(self) -> "TrelloRESTClient":
        """Create a Trello client with OAuth authentication.

        Returns:
            TrelloRESTClient instance
        """
        return TrelloRESTClient(
            api_key=self.api_key,
            api_secret=self.api_secret,
            oauth_token=self.oauth_token,
        )

    def to_dict(self) -> Dict[str, str]:
        """Convert the configuration to a dictionary."""
        return asdict(self)


class TrelloClient(IClient):
    """Builder class for Trello clients with OAuth authentication.

    This class provides a unified interface for creating Trello clients
    with OAuth authentication, enabling multi-user scenarios.

    Example:
        >>> config = TrelloOAuthConfig(
        ...     api_key="your_key",
        ...     api_secret="your_secret",
        ...     oauth_token="user_token",
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

    @classmethod
    def build_with_config(
        cls,
        config: TrelloOAuthConfig,
    ) -> "TrelloClient":
        """Build TrelloClient with OAuth configuration.

        Args:
            config: Trello OAuth configuration instance

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
        try:
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Trello connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found")

            credentials_config = config.get("credentials", {}) or {}

            auth_type = auth_config.get("authType", "OAUTH")

            if auth_type == "OAUTH":
                api_key = credentials_config.get("api_key", "")
                api_secret = credentials_config.get("api_secret", "")
                oauth_token = credentials_config.get("oauth_token", "")

                if not api_key or not api_secret or not oauth_token:
                    raise ValueError("API key, secret, and OAuth token required")

                client = TrelloRESTClient(
                    api_key=api_key,
                    api_secret=api_secret,
                    oauth_token=oauth_token,
                )
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Trello client from services: {e!s}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger, config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config for Trello."""
        try:
            config = await config_service.get_config(
                "/services/connectors/trello/config"
            )
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Trello connector config: {e}")
            return {}
