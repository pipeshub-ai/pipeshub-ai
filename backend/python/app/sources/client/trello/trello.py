"""Trello Client with OAuth Authentication using direct HTTP calls.

This module provides OAuth-based authentication for the Trello API
using direct HTTP requests instead of third-party SDKs.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
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


class TrelloRESTClient:
    """Trello REST client using API Key + Token authentication.

    This client uses the Trello REST API directly with API Key and Token.
    No third-party SDK is used.

    Args:
        api_key: API key from Trello Power-Ups admin
        token: Token generated via Trello authorization
    """

    TRELLO_API_BASE_URL = "https://api.trello.com/1"

    def __init__(
        self,
        api_key: str,
        token: str,
    ) -> None:
        """Initialize the Trello client with API Key + Token."""
        # Initialize HTTPClient base (without auth header - Trello uses query params)
        self.api_key = api_key
        self.token = token
        self.timeout = 30.0
        self.follow_redirects = True
        self.client = None  # httpx client

        # Set headers (no Authorization header - auth is in query params)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_base_url(self) -> str:
        """Get the Trello API base URL."""
        return self.TRELLO_API_BASE_URL

    async def _ensure_client(self) -> object:
        """Ensure client is created and available."""
        import httpx

        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=self.follow_redirects
            )
        return self.client

    def _build_auth_params(self) -> Dict[str, str]:
        """Build authentication query parameters."""
        return {
            "key": self.api_key,
            "token": self.token,
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

        try:
            # Use httpx directly to avoid Pydantic alias issues with HTTPRequest
            client = await self._ensure_client()

            kwargs: Dict[str, Any] = {
                "params": params,
                "headers": self.headers,
            }
            if body:
                kwargs["json"] = body

            response = await client.request(method, url, **kwargs)

            if response.status_code >= HttpStatusCode.BAD_REQUEST.value:
                return TrelloResponse(
                    success=False,
                    error=f"HTTP {response.status_code}: {response.text}",
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
class TrelloTokenConfig:
    """Configuration for Trello client via API Key + Token.

    Simple authentication using API Key and Token from Trello.

    Args:
        api_key: API key from Trello Power-Ups admin console
        token: Token generated via Trello authorization
    """

    api_key: str
    token: str

    def create_client(self) -> "TrelloRESTClient":
        """Create a Trello client with API Key + Token authentication.

        Returns:
            TrelloRESTClient instance
        """
        return TrelloRESTClient(
            api_key=self.api_key,
            token=self.token,
        )

    def to_dict(self) -> Dict[str, str]:
        """Convert the configuration to a dictionary."""
        return asdict(self)


class TrelloClient(IClient):
    """Builder class for Trello clients with API Key + Token authentication.

    This class provides a unified interface for creating Trello clients
    with simple API Key + Token authentication.

    Example:
        >>> config = TrelloTokenConfig(
        ...     api_key="your_key",
        ...     token="your_token",
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
        config: TrelloTokenConfig,
    ) -> "TrelloClient":
        """Build TrelloClient with API Key + Token configuration.

        Args:
            config: Trello Token configuration instance

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

            auth_type = auth_config.get("authType", "API_TOKEN")

            if auth_type == "API_TOKEN":
                api_key = credentials_config.get("api_key", "")
                token = credentials_config.get("token", "")

                if not api_key or not token:
                    raise ValueError("API key and token required")

                client = TrelloRESTClient(
                    api_key=api_key,
                    token=token,
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
