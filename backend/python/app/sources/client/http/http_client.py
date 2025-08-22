import aiohttp  # type: ignore

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient


class HTTPClient(IClient):
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        self.base_url = base_url
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.session = None

    def get_client(self) -> 'HTTPClient':
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
        url = f"{self.base_url}{request.url}"
        headers = request.headers
        query_params = request.query_params
        body = request.body
        method = request.method
        session = await self._ensure_session()
        headers = {**self.headers, **headers}
        async with session.request(method, url, headers=headers, params=query_params, json=body, **kwargs) as response:
            return HTTPResponse(response.content, response) # type: ignore

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
