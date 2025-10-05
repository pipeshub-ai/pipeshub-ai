from typing import Optional

import httpx  # type: ignore

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient


class HTTPClient(IClient):
    def __init__(
        self,
        token: str,
        token_type: str = "Bearer",
        timeout: float = 30.0,
        follow_redirects: bool = True
    ) -> None:
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.client: Optional[httpx.AsyncClient] = None

    def get_client(self) -> "HTTPClient":
        """Get the client"""
        return self

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure client is created and available"""
        if self.client is None:
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
