"""
Figma API Client Implementation

This module provides a comprehensive client for interacting with the Figma API,
supporting both Personal Access Token and OAuth 2.0 authentication methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import types
from typing import Any, Dict, Generic, List, Optional, TypedDict, TypeVar, Union, Unpack
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

T = TypeVar("T", bound=BaseModel)


class FigmaResponse(BaseModel, Generic[T]):
    """Standardized response wrapper for Figma API responses.

    Attributes:
        success: Whether the request was successful
        data: Response data if successful
        error: Error message if request failed
        status_code: HTTP status code of the response
        message: Optional additional message
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[T] = Field(None, description="Response data if successful")
    error: Optional[str] = Field(None, description="Error message if request failed")
    status_code: Optional[int] = Field(
        None, description="HTTP status code of the response"
    )
    message: Optional[str] = Field(None, description="Optional additional message")

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json()


class FigmaConfig(BaseModel):
    """Configuration for Figma API client."""

    access_token: str = Field(..., description="Figma personal access token")
    base_url: str = Field(
        "https://api.figma.com/v1", description="Base URL for Figma API"
    )
    timeout: float = Field(30.0, description="Request timeout in seconds")
    max_retries: int = Field(
        3, description="Maximum number of retries for failed requests"
    )
    retry_delay: float = Field(1.0, description="Delay between retries in seconds")
    ssl: bool = Field(True, description="Whether to use SSL for requests")

    def create_client(self) -> "FigmaClient":
        """Create a Figma client with this configuration."""
        from .figma import (  # Local import to avoid circular import
            FigmaClient,
            FigmaRESTClient,
        )

        rest_client = FigmaRESTClient(
            token=self.access_token, base_url=self.base_url, timeout=self.timeout
        )
        return FigmaClient(rest_client)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return self.model_dump()


class FigmaOAuthConfig(BaseModel):
    """Configuration for Figma REST client via OAuth 2.0"

    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The OAuth redirect URI
        scope: List of OAuth scopes to request
        base_url: Base URL for the Figma API (default: https://api.figma.com/v1)
        auth_url: OAuth authorization URL (default: https://www.figma.com/oauth)
        token_url: OAuth token URL (default: https://www.figma.com/api/oauth/token)
    """

    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str = Field(..., description="OAuth client secret")
    redirect_uri: str = Field(..., description="OAuth redirect URI")
    scope: List[str] = Field(
        default_factory=lambda: ["file_read", "file_metadata_read"],
        description="List of OAuth scopes",
    )
    base_url: str = Field(
        "https://api.figma.com/v1", description="Base URL for API requests"
    )
    auth_url: str = Field(
        "https://www.figma.com/oauth", description="OAuth authorization URL"
    )
    token_url: str = Field(
        "https://www.figma.com/api/oauth/token", description="OAuth token URL"
    )

    def create_client(self) -> "FigmaOAuthClient":
        """Create a Figma OAuth client with this configuration"""
        return FigmaOAuthClient(self)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return self.model_dump()


class FigmaOAuthClient(HTTPClient):
    """Figma REST client via OAuth 2.0 - handles OAuth flow internally"""

    def __init__(self, config: FigmaOAuthConfig) -> None:
        """Initialize Figma OAuth client with configuration

        Args:
            config: OAuth configuration
        """
        self.config = config
        self.token_data: Optional[Dict[str, Any]] = None
        self.base_url = config.base_url.rstrip("/")
        self.logger = logging.getLogger(__name__)
        self._client: Optional[httpx.AsyncClient] = None
        super().__init__("", "Bearer")

    async def __aenter__(self) -> "FigmaOAuthClient":
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[types.TracebackType],
    ) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Get the authorization URL for OAuth flow

        Args:
            state: Optional state parameter for CSRF protection

        Returns:
            Authorization URL to redirect the user to
        """
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scope),
            "response_type": "code",
        }
        if state:
            params["state"] = state

        return f"{self.config.auth_url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token

        Args:
            code: Authorization code from OAuth redirect

        Returns:
            Token data including access_token, refresh_token, and expiry
        """
        if not self._client or self._client.is_closed:
            raise RuntimeError(
                "HTTP client not initialized or closed. Use 'async with' context manager."
            )

        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "redirect_uri": self.config.redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        }

        try:
            response = await self._client.post(self.config.token_url, data=data)
            response.raise_for_status()
            self.token_data = response.json()
            self._update_token(self.token_data)
            return self.token_data
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Failed to exchange code for token: {e}")
            raise

    def _update_token(self, token_data: Dict[str, Any]) -> None:
        """Update the HTTP client with the latest token"""
        self.token = token_data["access_token"]
        self.token_expiry = time.time() + token_data.get("expires_in", 3600)
        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]

    async def refresh_access_token(self) -> Dict[str, Any]:
        """Refresh the access token using the refresh token

        Returns:
            New token data

        Raises:
            ValueError: If no refresh token is available
            RuntimeError: If client is not initialized
        """
        if not hasattr(self, "refresh_token") or not self.refresh_token:
            raise ValueError("No refresh token available")

        if not self._client or self._client.is_closed:
            raise RuntimeError(
                "HTTP client not initialized or closed. Use 'async with' context manager."
            )

        data = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            response = await self._client.post(self.config.token_url, data=data)
            response.raise_for_status()
            self.token_data = response.json()
            self._update_token(self.token_data)
            return self.token_data
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Failed to refresh access token: {e}")
            raise

    def get_base_url(self) -> str:
        """Get the base URL for API requests"""
        return self.base_url


class FigmaRESTClient(HTTPClient):
    """Base Figma REST client that extends HTTPClient with Figma-specific functionality.

    This client handles the base URL and authentication headers required by the Figma API.
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.figma.com/v1",
        token_type: str = "Bearer",
        **kwargs,
    ) -> None:
        """Initialize the Figma REST client.

        Args:
            token: Figma API access token
            base_url: Base URL for the Figma API (defaults to v1)
            token_type: Not used, kept for compatibility
            **kwargs: Additional arguments to pass to HTTPClient
        """
        super().__init__(token="", token_type="", **kwargs)

        self.base_url = base_url.rstrip("/")
        self.logger = logging.getLogger(__name__)

        # Clear default headers and set Figma-specific headers
        self.headers = {"X-Figma-Token": token, "Content-Type": "application/json"}

        self.token = token

    def get_base_url(self) -> str:
        """Get the base URL for API requests.

        Returns:
            The base URL for all API requests
        """
        return self.base_url

    class RequestKwargs(TypedDict, total=False):
        """Type definition for additional request parameters."""
        headers: Optional[Dict[str, str]]
        json: Optional[Dict[str, Any]]
        params: Optional[Dict[str, Any]]
        data: Optional[Union[Dict[str, Any], str, bytes]]
        timeout: Optional[float]
        ssl: Optional[bool]

    async def request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Unpack[RequestKwargs]
    ) -> Union[Dict[str, Any], List[Any]]:
        """Make an HTTP request to the Figma API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments including headers, params, json, etc.

        Returns:
            The parsed JSON response from the API

        Raises:
            HTTPError: If the request fails
        """

        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        headers = kwargs.pop("headers", {})
        if "X-Figma-Token" not in headers and hasattr(self, "token") and self.token:
            headers["X-Figma-Token"] = self.token

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        self.logger.debug(f"Making {method} request to: {self.base_url}{endpoint}")
        self.logger.debug(f"Headers: {headers}")

        req = HTTPRequest(
            method=method.upper(),
            url=f"{self.base_url}{endpoint}",
            headers=headers,
            query_params=kwargs.get("params"),
            body=kwargs.get("json"),
        )

        http_response = await self.execute(req, **kwargs)
        http_response.raise_for_status()

        try:
            return http_response.json()
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            self.logger.error(f"Failed to parse response: {e}", exc_info=True)
            raise


class FigmaRateLimiter:
    """Handles rate limiting for Figma API"""

    RATE_LIMIT_THRESHOLD = 5

    def __init__(self, safety_margin: float = 0.1) -> None:
        self.remaining = 30
        self.reset_time = 0
        self.safety_margin = safety_margin

    def update_from_headers(self, headers: Dict[str, str]) -> None:
        """Update rate limit info from response headers"""
        if "X-RateLimit-Remaining" in headers:
            self.remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Reset" in headers:
            self.reset_time = int(headers["X-RateLimit-Reset"])

    async def wait_if_needed(self) -> None:
        """Wait if rate limit is close to being reached"""
        now = time.time()
        if self.reset_time > now:
            wait_time = (self.reset_time - now) * (1 + self.safety_margin)
            await asyncio.sleep(wait_time)
        elif self.remaining < self.RATE_LIMIT_THRESHOLD:
            wait_time = 60 / 30
            await asyncio.sleep(wait_time)


class FigmaClient(IClient):
    """Figma API client

    This client provides a complete interface to the Figma API with support for:
    - Personal Access Token authentication
    - OAuth 2.0 authentication
    - Basic request/response handling
    - Error handling
    """

    def __init__(self, client: Union[FigmaRESTClient, FigmaOAuthClient]) -> None:
        """Initialize Figma client with a configured client

        Args:
            client: Configured Figma client (token or OAuth)
        """
        self.client = client
        self.rate_limiter = FigmaRateLimiter()
        self.logger = logging.getLogger(__name__)
        self._should_close_client = True

    def get_base_url(self) -> str:
        """Get the base URL for API requests"""
        if hasattr(self.client, "get_base_url"):
            return self.client.get_base_url()
        return "https://api.figma.com/v1"

    def get_client(self) -> Union[FigmaRESTClient, FigmaOAuthClient]:
        """Get the underlying HTTP client instance"""
        return self.client

    async def close(self) -> None:
        """
        Close the underlying HTTP client and release resources.
        """
        if hasattr(self.client, 'close'):
            await self.client.close()

    @classmethod
    def build_with_config(cls, config: FigmaConfig) -> "FigmaClient":
        """Build a FigmaClient with the given configuration

        Args:
            config: Figma configuration

        Returns:
            Configured FigmaClient instance
        """
        rest_client = FigmaRESTClient(
            token=config.access_token, base_url=config.base_url, timeout=config.timeout
        )
        return cls(rest_client)

    @classmethod
    def build_from_services(
        cls, logger: logging.Logger, config_service: ConfigurationService
    ) -> "FigmaClient":
        """Build a FigmaClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            Configured FigmaClient instance
        """
        config_data = cls._get_connector_config(logger, config_service)
        config = FigmaConfig(**config_data)
        rest_client = FigmaRESTClient(
            token=config.access_token, base_url=config.base_url, timeout=config.timeout
        )
        return cls(rest_client)

    @staticmethod
    def _get_connector_config(
        logger: logging.Logger, config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config from configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            Dictionary containing the configuration

        Raises:
            ValueError: If required configuration is missing or invalid
        """
        try:
            config = config_service.get_connector_config("figma")

            if not config:
                raise ValueError("No configuration found for Figma connector")

            access_token = config.get("access_token")
            if not access_token:
                raise ValueError(
                    "Missing required 'access_token' in Figma configuration"
                )

            base_url = config.get("base_url", "https://api.figma.com/v1")

            return {
                "access_token": access_token,
                "base_url": base_url,
                "timeout": float(config.get("timeout", 30.0)),
                "max_retries": int(config.get("max_retries", 3)),
                "retry_delay": float(config.get("retry_delay", 1.0)),
                "ssl": bool(config.get("ssl", True)),
            }

        except KeyError as e:
            error_msg = f"Missing required configuration key: {e}"
            logger.error(error_msg)
            raise ValueError(f"Invalid Figma configuration: {error_msg}") from e
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid configuration value: {e}"
            logger.error(error_msg)
            raise ValueError(f"Invalid Figma configuration: {error_msg}") from e

    async def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Make an HTTP request to the Figma API

        Args:
            endpoint: API endpoint (without base URL)
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            data: Request body data

        Returns:
            FigmaResponse containing the API response
        """

        endpoint = endpoint.lstrip("/")

        self.logger.debug(f"Making {method} request to endpoint: {endpoint}")
        self.logger.debug(f"Params: {params}")
        self.logger.debug(f"Data: {data}")

        try:
            response = await self.client.request(
                method=method,
                endpoint=endpoint,
                params=params,
                json=data if data else None,
            )

            return FigmaResponse[Dict[str, Any]](
                success=True,
                data=response,
                status_code=getattr(response, "status_code", 200),
            )

        except httpx.HTTPStatusError as e:
            self.logger.error(
                f"HTTP error {e.response.status_code} making request to {e.request.url}: {str(e)}",
                exc_info=True
            )
            raise
        except httpx.RequestError as e:
            self.logger.error(
                f"Request failed: {str(e)}",
                exc_info=True
            )
            raise
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(
                f"Failed to parse response: {str(e)}",
                exc_info=True
            )
            return FigmaResponse[Dict[str, Any]](
                success=False, error=str(e), status_code=getattr(e, "status_code", 500)
            )

    @classmethod
    def from_personal_access_token(
        cls, access_token: str, base_url: str = "https://api.figma.com/v1", **kwargs
    ) -> "FigmaClient":
        """Create a Figma client using a personal access token

        Args:
            access_token: Figma personal access token
            base_url: Base URL for the Figma API
            **kwargs: Additional arguments for FigmaConfig

        Returns:
            Configured FigmaClient instance
        """
        logger = logging.getLogger(__name__)
        logger.info(
            f"Initializing Figma client with token: {access_token[:4]}...{access_token[-4:] if access_token else ''}"
        )
        logger.debug(f"Base URL: {base_url}")
        logger.debug(f"Additional kwargs: {kwargs}")

        if not access_token:
            raise ValueError("Access token cannot be empty")

        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
            logger.debug(f"Adjusted base URL to: {base_url}")

        logger.debug(f"Creating FigmaRESTClient with token: {access_token[:4]}...")
        client = FigmaRESTClient(
            token=access_token, base_url=base_url, token_type="Bearer"
        )
        logger.debug("FigmaRESTClient created successfully")
        return cls(client)

    @classmethod
    def from_oauth(
        cls, client_id: str, client_secret: str, redirect_uri: str, **kwargs
    ) -> "FigmaClient":
        """Create a Figma client using OAuth 2.0

        Args:
            client_id: OAuth client ID
            client_secret: OAuth client secret
            redirect_uri: OAuth redirect URI
            **kwargs: Additional arguments for FigmaOAuthConfig

        Returns:
            Configured FigmaClient instance
        """
        config = FigmaOAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            **kwargs,
        )
        return cls(config.create_client())
