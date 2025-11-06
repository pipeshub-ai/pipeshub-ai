from typing import Any

import aiohttp  # type: ignore

from app.sources.client.graphql.response import GraphQLResponse


class GraphQLClient:
    """Generic GraphQL client for making GraphQL requests."""

    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> None:
        self.endpoint = endpoint
        self.headers = headers or {}
        self.timeout = timeout
        # Intentionally avoid a long-lived session to prevent cross-event-loop issues
        # (especially on Windows with ProactorEventLoop and SSL transports).

    # Note: We intentionally do not cache ClientSession instances. Creating a
    # short-lived session per request keeps session lifecycle bound to the
    # current event loop and avoids closing a session from a different loop.

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> GraphQLResponse:
        """Execute a GraphQL query."""
        payload = {
            "query": query,
            "variables": variables or {},
        }
        if operation_name:
            payload["operationName"] = operation_name

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.endpoint,
                    json=payload,
                    headers=self.headers,
                ) as response:
                    response_data = await response.json()
                    return GraphQLResponse.from_response(response_data)
        except aiohttp.ClientError as e:
            return GraphQLResponse(
                success=False,
                message=f"Request failed: {e!s}",
            )

    async def close(self) -> None:
        """No-op close: sessions are short-lived per request and auto-closed."""
        return

    async def __aenter__(self) -> "GraphQLClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
