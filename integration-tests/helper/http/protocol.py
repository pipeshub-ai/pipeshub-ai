"""HTTP Client Protocol for integration tests.

Defines the interface that HTTP clients must implement to be used
with domain-scoped API clients (TeamsClient, UsersClient, etc.).
"""

from typing import Any, Protocol

import requests


class HTTPClientProtocol(Protocol):
    """Protocol for HTTP clients used in integration tests.

    Any class implementing this protocol can be used as a backing client
    for APIClient subclasses. PipeshubClient implements this protocol.
    """

    @property
    def base_url(self) -> str:
        """Base URL of the API server."""
        ...

    @property
    def timeout_seconds(self) -> float:
        """Default timeout for HTTP requests in seconds."""
        ...

    @property
    def auth_headers(self) -> dict[str, str]:
        """Authorization headers with valid access token."""
        ...

    def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Execute an HTTP request and return the raw Response.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            path: URL path (will be joined with base_url)
            auth: If True, include authorization headers
            **kwargs: Additional arguments passed to requests.request()

        Returns:
            Raw requests.Response object (does NOT raise on 4xx/5xx)
        """
        ...
