"""HTTP client that calls the API as the user in person, with a login session JWT.

``PipeshubClient`` authenticates with an OAuth client-credentials token. Some
routes refuse any OAuth access token or personal access token and accept only
the session JWT a password login returns (``requireSessionAuth``, #3626):
OAuth client management, the consent step and personal access tokens. Tests
for those routes back their domain clients with this instead.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests

# Part of the 403 message a session-only route answers a bearer token with.
SESSION_REQUIRED_MESSAGE = "requires an interactive user session"


class SessionClient:
    """Implements ``HTTPClientProtocol`` with a user session JWT.

    ``login`` returns a fresh session JWT. It is called on first use and again
    once if a request comes back 401, so a token that expires during a long run
    is replaced rather than failing every later test.
    """

    def __init__(
        self,
        base_url: str,
        login: Callable[[], str],
        timeout_seconds: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._login = login
        self._token: str | None = None

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = self._login()
        return self._token

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        """Send one request; raw Response back, never raises on 4xx/5xx."""
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout_seconds)
        extra_headers = kwargs.pop("headers", None) or {}

        for attempt in range(2):
            headers = dict(self.auth_headers) if auth else {}
            if auth and kwargs.get("files"):
                headers.pop("Content-Type", None)
            resp = requests.request(method, url, headers={**headers, **extra_headers}, **kwargs)
            if resp.status_code == 401 and auth and attempt == 0:
                self._token = None
                continue
            return resp
        return resp
