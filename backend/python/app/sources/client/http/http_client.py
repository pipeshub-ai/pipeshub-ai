from typing import Optional

import httpx  # type: ignore

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy


class HTTPClient(IClient):
    """HTTP client with token auth and optional rate limiting / retries.

    Args:
        resilience: Shared policy from the connector's ``@ConnectorBuilder``
            metadata. When omitted the client behaves exactly as before — no
            transport, no retries.
    """

    def __init__(
        self,
        token: str,
        token_type: str = "Bearer",
        timeout: float = 30.0,
        follow_redirects: bool = True,
        resilience: Optional[ResiliencePolicy] = None
    ) -> None:
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.resilience = resilience
        self.client: Optional[httpx.AsyncClient] = None
        self._public_only = False

    def get_client(self) -> "HTTPClient":
        """Get the client"""
        return self

    def require_public_addresses(self) -> None:
        """Send requests only to public addresses, not private ones either (see `address_guard`).
        For an address a user chose that the deployment's network must not see, such as a
        personal action's; call before the first request."""
        if self.client is not None:
            raise RuntimeError("require_public_addresses() must be called before the first request")
        self._public_only = True

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure client is created and available"""
        if self.client is None:
            from app.sources.client.http.address_guard import guarded_async_client

            # getattr: a subclass that skips HTTPClient.__init__ has no flag, and isn't public-only.
            self.client = guarded_async_client(
                allow_private=False if getattr(self, "_public_only", False) else None,
                resilience=self.resilience,
                timeout=self.timeout,
                follow_redirects=self.follow_redirects,
                headers=self.headers,
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
