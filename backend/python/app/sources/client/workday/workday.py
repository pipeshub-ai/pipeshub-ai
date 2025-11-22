from typing import Any, Dict, Optional

import httpx

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


class WorkdayClient(HTTPClient):
    """Minimal Workday REST API client using OAuth 2.0 Bearer token auth.

    Authentication flow:
    1. Provide ``client_id``, ``client_secret``, ``refresh_token``, and ``token_endpoint``
       from Workday Integration System User (ISU) OAuth client.
    2. An *access token* is fetched during initialisation (unless one is supplied
       explicitly).
    3. Call :py:meth:`refresh_access_token` to renew the access token when it
       expires; the base-class ``headers`` mapping is updated in-place so that
       subsequent calls automatically use the fresh token.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_endpoint: str,
        access_token: Optional[str] = None,
        token_type: str = "Bearer",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._token_endpoint = token_endpoint

        if access_token is None:
            access_token, token_type = self._obtain_access_token_sync()

        super().__init__(access_token, token_type, timeout=timeout)
        # Default all requests to JSON unless overridden at call site.
        self.headers.setdefault("Content-Type", "application/json")

    # OAuth helpers
    def _obtain_access_token_sync(self) -> tuple[str, str]:
        """Exchange ``refresh_token`` for an ``access_token`` synchronously."""
        payload: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        response = httpx.post(
            self._token_endpoint,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        access_token: str = data["access_token"]
        token_type: str = data.get("token_type", "Bearer")
        return access_token, token_type

    async def refresh_access_token(self) -> None:
        """Refresh the OAuth token and update the Authorization header."""
        access_token, token_type = self._obtain_access_token_sync()
        self.headers["Authorization"] = f"{token_type} {access_token}"

    # Convenience API wrappers 
    async def get_workers(self, **query_params: Any) -> HTTPResponse:
        """Retrieve workers (employees / contingent workers) list."""
        rel_path = "/v1/workers"
        url = f"{self.base_url}{rel_path}"
        request = HTTPRequest(
            url=url,
            method="GET",
            query_params={k: str(v) for k, v in query_params.items()},
        )
        return await self.execute(request)

    # Misc
    def get_base_url(self) -> str: 
        """Return the configured Workday API base URL."""
        return self.base_url
