"""Search API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class SearchClient(APIClient):
    """Client for /api/v1/search endpoints."""

    BASE = "/api/v1/search"

    def _request_options(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split HTTP request options from JSON/query payload kwargs."""
        options: dict[str, Any] = {"auth": kwargs.pop("auth", True)}
        if "headers" in kwargs:
            options["headers"] = kwargs.pop("headers")
        if "timeout" in kwargs:
            options["timeout"] = kwargs.pop("timeout")
        if "params" in kwargs:
            options["params"] = kwargs.pop("params")
        return options, kwargs

    def create_search(self, **kwargs: Any) -> requests.Response:
        """Execute a search (POST /)."""
        options, payload = self._request_options(kwargs)
        return self.post("/", json=payload, **options)

    def search(self, query: str, **kwargs: Any) -> requests.Response:
        """Execute a search query (POST /)."""
        return self.create_search(query=query, **kwargs)

    def search_semantic(self, query: str, **kwargs: Any) -> requests.Response:
        """Execute a semantic search query (POST /semantic)."""
        options, payload = self._request_options({"query": query, **kwargs})
        return self.post("/semantic", json=payload, **options)

    def search_hybrid(self, query: str, **kwargs: Any) -> requests.Response:
        """Execute a hybrid search query (POST /hybrid)."""
        options, payload = self._request_options({"query": query, **kwargs})
        return self.post("/hybrid", json=payload, **options)

    def list_history(self, **kwargs: Any) -> requests.Response:
        """List search history (GET /)."""
        options, params = self._request_options(kwargs)
        if params:
            options["params"] = params
        return self.get("/", **options)

    def get_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Get a search by id (GET /{searchId})."""
        return self.get(f"/{search_id}", **kwargs)

    def share_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Share a search (PATCH /{searchId}/share)."""
        options, payload = self._request_options(kwargs)
        return self.patch(f"/{search_id}/share", json=payload, **options)

    def unshare_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Unshare a search (PATCH /{searchId}/unshare)."""
        options, payload = self._request_options(kwargs)
        return self.patch(f"/{search_id}/unshare", json=payload, **options)

    def archive_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Archive a search (PATCH /{searchId}/archive)."""
        return self.patch(f"/{search_id}/archive", **kwargs)

    def unarchive_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Unarchive a search (PATCH /{searchId}/unarchive)."""
        return self.patch(f"/{search_id}/unarchive", **kwargs)

    def delete_search(self, search_id: str, **kwargs: Any) -> requests.Response:
        """Delete a search by id (DELETE /{searchId})."""
        return self.delete(f"/{search_id}", **kwargs)

    def delete_history(self, **kwargs: Any) -> requests.Response:
        """Delete all search history (DELETE /)."""
        return self.delete("/", **kwargs)
