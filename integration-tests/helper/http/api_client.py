"""Base API client class for domain-scoped HTTP operations.

Provides a clean abstraction over HTTPClientProtocol with
convenience methods for each HTTP verb.
"""

from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from helper.http.protocol import HTTPClientProtocol


class APIClient:
    """Base class for domain-scoped API clients.

    Subclasses should set the BASE class attribute to the API path prefix.

    Example:
        class TeamsClient(APIClient):
            BASE = "/api/v1/teams"

            def create_team(self, name: str) -> requests.Response:
                return self.post("/", json={"name": name})
    """

    BASE: str = ""

    def __init__(self, client: "HTTPClientProtocol") -> None:
        """Initialize with an HTTP client.

        Args:
            client: HTTP client implementing HTTPClientProtocol
        """
        self._client = client

    def _path(self, path: str) -> str:
        """Build full path by prepending BASE."""
        if not path:
            return self.BASE
        if path.startswith("/"):
            return f"{self.BASE}{path}"
        return f"{self.BASE}/{path}"

    def get(
        self, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Execute GET request."""
        return self._client.request("GET", self._path(path), auth=auth, **kwargs)

    def post(
        self, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Execute POST request."""
        return self._client.request("POST", self._path(path), auth=auth, **kwargs)

    def put(
        self, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Execute PUT request."""
        return self._client.request("PUT", self._path(path), auth=auth, **kwargs)

    def delete(
        self, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Execute DELETE request."""
        return self._client.request("DELETE", self._path(path), auth=auth, **kwargs)

    def patch(
        self, path: str = "", *, auth: bool = True, **kwargs: Any
    ) -> requests.Response:
        """Execute PATCH request."""
        return self._client.request("PATCH", self._path(path), auth=auth, **kwargs)
