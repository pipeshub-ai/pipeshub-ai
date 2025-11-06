"""Gong API Client

This module provides a client for interacting with the Gong API.
Gong is a revenue intelligence platform that captures and analyzes sales conversations.
"""

import asyncio
from typing import Any
from urllib.parse import urljoin

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from app.sources.client.iclient import IClient


class GongClient(IClient):
    """Async client for Gong API.

    Gong API Documentation: https://us-66463.app.gong.io/settings/api/documentation
    """

    BASE_URL = "https://api.gong.io/v2/"

    def __init__(
        self,
        access_key: str,
        access_key_secret: str,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """Initialize Gong client.

        Args:
            access_key: Gong API access key
            access_key_secret: Gong API access key secret
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds

        """
        self.access_key = access_key
        self.access_key_secret = access_key_secret
        self.timeout = ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._session: ClientSession | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_session(self) -> ClientSession:
        """Ensure aiohttp session is created."""
        if self._session is None or self._session.closed:
            # Create basic auth header
            auth = aiohttp.BasicAuth(self.access_key, self.access_key_secret)

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "PipesHub-Gong-Client/1.0",
            }

            self._session = ClientSession(
                auth=auth,
                headers=headers,
                timeout=self.timeout,
                connector=aiohttp.TCPConnector(limit=100, limit_per_host=30),
            )

        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_client(self) -> ClientSession:
        """Get the underlying HTTP client session (required by IClient interface)."""
        return await self._ensure_session()

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Make HTTP request to Gong API with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (relative to base URL)
            params: Query parameters
            data: Request body data
            **kwargs: Additional arguments for aiohttp request

        Returns:
            Response data as dictionary

        Raises:
            aiohttp.ClientError: For HTTP errors
            json.JSONDecodeError: For invalid JSON responses

        """
        session = await self._ensure_session()
        url = urljoin(self.BASE_URL, endpoint.lstrip("/"))

        for attempt in range(self.max_retries + 1):
            try:
                async with session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    **kwargs,
                ) as response:
                    # Handle rate limiting
                    if response.status == 429:
                        if attempt < self.max_retries:
                            retry_after = int(response.headers.get("Retry-After", self.retry_delay))
                            await asyncio.sleep(retry_after)
                            continue

                    # Raise for HTTP errors
                    response.raise_for_status()

                    # Parse JSON response
                    response_data = await response.json()
                    return response_data

            except aiohttp.ClientError as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                raise e

        raise aiohttp.ClientError(f"Max retries ({self.max_retries}) exceeded for {method} {url}")

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make GET request."""
        return await self._make_request("GET", endpoint, params=params)

    async def post(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make POST request."""
        return await self._make_request("POST", endpoint, data=data)

    async def put(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make PUT request."""
        return await self._make_request("PUT", endpoint, data=data)

    async def delete(self, endpoint: str) -> dict[str, Any]:
        """Make DELETE request."""
        return await self._make_request("DELETE", endpoint)

    # Gong-specific API methods

    async def test_connection(self) -> bool:
        """Test API connection and credentials.

        Returns:
            True if connection is successful, False otherwise

        """
        try:
            await self.get("users")
            return True
        except Exception:
            return False

    async def get_users(
        self,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Get list of users.

        Args:
            cursor: Pagination cursor
            limit: Number of results per page (max 100)

        Returns:
            Users data with pagination info

        """
        params = {"limit": min(limit, 100)}
        if cursor:
            params["cursor"] = cursor

        return await self.get("users", params=params)

    async def get_calls(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Get list of calls.

        Args:
            from_date: Start date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
            to_date: End date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
            cursor: Pagination cursor
            limit: Number of results per page (max 100)
            workspace_id: Specific workspace ID

        Returns:
            Calls data with pagination info

        """
        params = {"limit": min(limit, 100)}

        if from_date:
            params["fromDateTime"] = from_date
        if to_date:
            params["toDateTime"] = to_date
        if cursor:
            params["cursor"] = cursor
        if workspace_id:
            params["workspaceId"] = workspace_id

        return await self.get("calls", params=params)

    async def get_call_details(self, call_id: str) -> dict[str, Any]:
        """Get detailed information about a specific call.

        Args:
            call_id: Unique call identifier

        Returns:
            Detailed call information

        """
        return await self.get(f"calls/{call_id}")

    async def get_call_transcript(self, call_id: str) -> dict[str, Any]:
        """Get transcript for a specific call.

        Args:
            call_id: Unique call identifier

        Returns:
            Call transcript data

        """
        return await self.get(f"calls/{call_id}/transcript")

    async def get_workspaces(self) -> dict[str, Any]:
        """Get list of workspaces.

        Returns:
            Workspaces data

        """
        return await self.get("workspaces")

    async def get_deals(
        self,
        cursor: str | None = None,
        limit: int = 100,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Get list of deals.

        Args:
            cursor: Pagination cursor
            limit: Number of results per page (max 100)
            workspace_id: Specific workspace ID

        Returns:
            Deals data with pagination info

        """
        params = {"limit": min(limit, 100)}

        if cursor:
            params["cursor"] = cursor
        if workspace_id:
            params["workspaceId"] = workspace_id

        return await self.get("deals", params=params)

    async def get_meetings(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Get list of meetings.

        Args:
            from_date: Start date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
            to_date: End date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
            cursor: Pagination cursor
            limit: Number of results per page (max 100)
            workspace_id: Specific workspace ID

        Returns:
            Meetings data with pagination info

        """
        params = {"limit": min(limit, 100)}

        if from_date:
            params["fromDateTime"] = from_date
        if to_date:
            params["toDateTime"] = to_date
        if cursor:
            params["cursor"] = cursor
        if workspace_id:
            params["workspaceId"] = workspace_id

        return await self.get("meetings", params=params)

    async def get_all_users(self) -> list[dict[str, Any]]:
        """Get all users using pagination.

        Returns:
            List of all users

        """
        all_users = []
        cursor = None

        while True:
            response = await self.get_users(cursor=cursor, limit=100)
            users = response.get("users", [])
            all_users.extend(users)

            # Check if there are more pages
            records = response.get("records", {})
            cursor = records.get("cursor")
            if not cursor:
                break

        return all_users

    async def get_all_calls(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get all calls using pagination.

        Args:
            from_date: Start date (ISO format)
            to_date: End date (ISO format)
            workspace_id: Specific workspace ID

        Returns:
            List of all calls

        """
        all_calls = []
        cursor = None

        while True:
            response = await self.get_calls(
                from_date=from_date,
                to_date=to_date,
                cursor=cursor,
                limit=100,
                workspace_id=workspace_id,
            )
            calls = response.get("calls", [])
            all_calls.extend(calls)

            # Check if there are more pages
            records = response.get("records", {})
            cursor = records.get("cursor")
            if not cursor:
                break

        return all_calls


# Utility functions for common operations

async def create_gong_client(access_key: str, access_key_secret: str) -> GongClient:
    """Create and return a configured Gong client.

    Args:
        access_key: Gong API access key
        access_key_secret: Gong API access key secret

    Returns:
        Configured GongClient instance

    """
    return GongClient(access_key, access_key_secret)


async def test_gong_credentials(access_key: str, access_key_secret: str) -> bool:
    """Test Gong API credentials.

    Args:
        access_key: Gong API access key
        access_key_secret: Gong API access key secret

    Returns:
        True if credentials are valid, False otherwise

    """
    async with GongClient(access_key, access_key_secret) as client:
        return await client.test_connection()
