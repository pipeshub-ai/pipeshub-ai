import aiohttp  # type: ignore


class HTTPClient:
    def __init__(self, token: str, token_type: str = "Bearer") -> None:
        self.headers = {
            "Authorization": f"{token_type} {token}",
        }
        self.session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session is created and available"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get(self, url: str, headers: dict = {}, **kwargs) -> dict:
        """Get a resource from the HTTP server
        Args:
            url: The URL of the resource to get
        Returns:
            A dictionary containing the response from the server
        """
        session = await self._ensure_session()
        headers = {**self.headers, **headers}
        async with session.request("GET", url, headers=headers, **kwargs) as response:
            return await response.json()

    async def post(self, url: str, data: dict, headers: dict = {}, **kwargs) -> dict:
        """Post a resource to the HTTP server
        Args:
            url: The URL of the resource to post
            data: The data to post to the server
        Returns:
            A dictionary containing the response from the server
        """
        session = await self._ensure_session()
        headers = {**self.headers, **headers}
        async with session.request("POST", url, headers=headers, json=data, **kwargs) as response:
            return await response.json()

    async def put(self, url: str, data: dict, headers: dict = {}, **kwargs) -> dict:
        """Put a resource to the HTTP server
        Args:
            url: The URL of the resource to put
            data: The data to put to the server
        Returns:
            A dictionary containing the response from the server
        """
        session = await self._ensure_session()
        headers = {**self.headers, **headers}
        async with session.request("PUT", url, headers=headers, json=data, **kwargs) as response:
            return await response.json()

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
