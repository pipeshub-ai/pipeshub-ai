from typing import Any, Dict, Optional

import aiohttp  # type: ignore

from app.sources.client.graphql.response import GraphQLResponse


class GraphQLClient:
    """Generic GraphQL client for making GraphQL requests."""

    def __init__(
        self,
        endpoint: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30
    ) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}
        self.timeout = timeout
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession: #type: ignore
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    async def execute(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None
    ) -> GraphQLResponse:
        """Execute a GraphQL query."""
        payload = {
            "query": query,
            "variables": variables or {},
        }
        if operation_name:
            payload["operationName"] = operation_name

        try:
            session = await self._get_session()
            async with session.post(
                self.endpoint,
                json=payload,
                headers=self.headers
            ) as response:
                response_data = await response.json()
                return GraphQLResponse.from_response(response_data)
        except aiohttp.ClientError as e:
            return GraphQLResponse(
                success=False,
                message=f"Request failed: {str(e)}"
            )

    async def close(self) -> None:
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
