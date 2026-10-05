"""Client and constants for the strict OpenAPI audit of /api/v1/docs."""

from __future__ import annotations

import requests

from helper.http.api_client import APIClient

DOCS_BASE = "/api/v1/docs"
HEALTH_ROUTE = f"{DOCS_BASE}/health"
JSON_ROUTE = f"{DOCS_BASE}/json"
# Express registers the UI as router.get('*'), so every other GET under the mount lands here.
UI_ROUTE = DOCS_BASE

UNKNOWN_SUB_PATH = "/spec-audit/no-such-page"
UNIFIED_DOCS_KEYS = ("info", "categories", "modules", "endpoints", "schemas")


class ApiDocsClient(APIClient):
    """Client for /api/v1/docs; the router has no auth, so auth only decides whether a token is sent."""

    BASE = DOCS_BASE

    def health(self, *, auth: bool = True) -> requests.Response:
        return self.get("/health", auth=auth)

    def unified_json(self, *, auth: bool = True) -> requests.Response:
        return self.get("/json", auth=auth)

    def ui(self, sub_path: str = "", *, auth: bool = True) -> requests.Response:
        """GET the HTML UI; any sub_path other than /health and /json hits the wildcard."""
        return self.get(sub_path, auth=auth)
