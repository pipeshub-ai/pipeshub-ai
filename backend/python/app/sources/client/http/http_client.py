import logging
from typing import Optional

import httpx  # type: ignore
from aiolimiter import AsyncLimiter

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.resilient_transport import ResilientHTTPTransport
from app.sources.client.iclient import IClient


class HTTPClient(IClient):
    """
    HTTP client with authentication and optional resilience features.

    Features:
    - Automatic Authorization header injection
    - Optional retry logic with exponential backoff
    - Automatic rate limiting when retries are enabled (default: 50 req/s)

    Important: If max_retries > 0 but no rate_limiter is provided, a default rate limiter
    of 50 requests/second is automatically created to prevent retry storms.

    For retries WITHOUT rate limiting, use ResilientHTTPTransport directly with rate_limiter=None.

    Args:
        token: Authentication token
        token_type: Token type for Authorization header (default: "Bearer")
        timeout: Request timeout in seconds (default: 30.0)
        follow_redirects: Whether to follow HTTP redirects (default: True)
        rate_limiter: Optional AsyncLimiter. If None and max_retries > 0, defaults to 50 req/s
        max_retries: Number of retry attempts (default: 0 = disabled)
        base_delay: Initial delay for exponential backoff in seconds (default: 1.0)
        max_delay: Maximum delay cap in seconds (default: 32.0)
        logger: Optional logger instance
    """
    def __init__(
        self,
        token: str,
        token_type: str = "Bearer",
        timeout: float = 30.0,
        follow_redirects: bool = True,
        # Optional resilience configuration
        rate_limiter: Optional[AsyncLimiter] = None,
        max_retries: int = 0,  # 0 = disabled (backward compatible)
        base_delay: float = 1.0,
        max_delay: float = 32.0,
        logger: Optional[logging.Logger] = None
    ) -> None:
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.rate_limiter = rate_limiter
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.logger = logger or logging.getLogger(__name__)
        self.client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> "HTTPClient":
        """Get the client"""
        return self

    async def _ensure_client(self) -> httpx.AsyncClient:
        """
        Ensure client is created and available.
        Creates a resilient client if rate_limiter or max_retries is configured,
        otherwise creates a plain client (backward compatible).
        """
        if self.client is None:
            # If resilience config provided, use resilient transport
            if self.rate_limiter is not None or self.max_retries > 0:
                transport = ResilientHTTPTransport(
                    rate_limiter=self.rate_limiter,
                    max_retries=self.max_retries,
                    base_delay=self.base_delay,
                    max_delay=self.max_delay,
                    logger=self.logger
                )
                self.client = httpx.AsyncClient(
                    transport=transport,
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects
                )
            else:
                # Backward compatible: plain client if no resilience config
                self.client = httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=self.follow_redirects
                )
        return self.client

    async def execute(self, request: HTTPRequest, **kwargs) -> HTTPResponse:
        """Execute an HTTP request
        Args:
            request: The HTTP request to execute
            kwargs: Additional keyword arguments to pass to the request
        Returns:
            A HTTPResponse object containing the response from the server
        """
        url = f"{request.url.format(**request.path_params)}"
        client = await self._ensure_client()

        # Merge client headers with request headers (request headers take precedence)
        merged_headers = {**self.headers, **request.headers}
        request_kwargs = {
            "params": request.query_params,
            "headers": merged_headers,
            **kwargs
        }

        if isinstance(request.body, dict):
            # Check if Content-Type indicates form data
            content_type = request.headers.get("Content-Type", "").lower()
            if "application/x-www-form-urlencoded" in content_type:
                # Send as form data
                request_kwargs["data"] = request.body
            else:
                # Send as JSON (default behavior)
                request_kwargs["json"] = request.body
        elif isinstance(request.body, bytes):
            request_kwargs["content"] = request.body

        response = await client.request(request.method, url, **request_kwargs)
        return HTTPResponse(response)

    async def close(self) -> None:
        """Close the client"""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def __aenter__(self) -> "HTTPClient":
        """Async context manager entry"""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.close()
