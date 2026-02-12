from __future__ import annotations

from typing import Optional

from app.sources.client.http.http_client import HTTPClient

# Default headers for crawling published Google Sites over HTTP.
# Kept close to the HTTP client so they can be shared between the
# datasource and any other Google Sites HTTP consumers.
GOOGLE_SITES_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class GoogleSitesRESTClient(HTTPClient):
    """
    Lightweight HTTP client for published Google Sites.

    This client is intentionally simple: it reuses the shared HTTPClient
    infrastructure (httpx-based) but does not perform authentication,
    since URL-based crawling of published sites typically relies on
    public HTTP access.

    Used as the underlying client wrapped by GoogleClient (see other Google
    connectors: Drive, Gmail, etc.) via GoogleClient.build_with_client().
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        follow_redirects: bool = True,
    ) -> None:
        # Initialize base HTTPClient with an empty token and token_type,
        # since we rely on anonymous, public HTTP access for published sites.
        super().__init__(token="", token_type="", timeout=timeout, follow_redirects=follow_redirects)
        self.base_url = (base_url or "").rstrip("/")

        # Override default headers (no Authorization header).
        self.headers = dict(GOOGLE_SITES_DEFAULT_HEADERS)

    def get_base_url(self) -> str:
        """Return the configured base URL (may be empty for absolute URLs)."""
        return self.base_url

