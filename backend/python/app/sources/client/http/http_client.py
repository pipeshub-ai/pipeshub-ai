import aiohttp  # type: ignore

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient


class HTTPClient(IClient):
    def __init__(self, token: str, token_type: str = "Bearer") -> None:
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.session = None

    def get_client(self) -> "HTTPClient":
        """Get the client"""
        return self

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session is created and available"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def execute(self, request: HTTPRequest, **kwargs) -> HTTPResponse:
        """Get a resource from the HTTP server
        Args:
            request: The HTTP request to execute
            kwargs: Additional keyword arguments to pass to the request
        Returns:
            A HTTPResponse object containing the response from the server
        """
        url = f"{request.url.format(**request.path_params)}"
        session = await self._ensure_session()
        headers = {**self.headers, **request.headers}

        request_kwargs = {"params": request.query_params, "headers": headers, **kwargs}

        if isinstance(request.body, dict):
            # Check if Content-Type indicates form data
            content_type = headers.get("Content-Type", "").lower()
            if "application/x-www-form-urlencoded" in content_type:
                # Send as form data
                request_kwargs["data"] = request.body
            else:
                # Send as JSON (default behavior)
                request_kwargs["json"] = request.body
        elif isinstance(request.body, bytes):
            request_kwargs["data"] = request.body

        async with session.request(request.method, url, **request_kwargs) as response:
            response_bytes = await response.read()
            return HTTPResponse(response_bytes, response)

    async def close(self) -> None:
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self) -> "HTTPClient":
        """Async context manager entry"""
        session = await self._ensure_session()
        assert session is not None
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.close()
        assert self.session is None
