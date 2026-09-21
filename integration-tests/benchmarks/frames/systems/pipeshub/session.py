"""Authenticated access to a PipesHub instance as a real user.

AGENTS.md: user-scoped chat and KB actions must not use OAuth
client_credentials, so `UserSession` reuses `PipeshubClient` (401 retry,
request plumbing) but obtains its token by password login.
"""

from __future__ import annotations

import threading
from typing import Any

import requests

import helper.pipeshub_client as _namespaced_client
import pipeshub_client
from benchmarks.frames.errors import FramesError
from benchmarks.frames.retry import http_retry, raise_for_transient
from helper.clients.auth_client import UserAccountClient

# Helpers raise from both module roots; catch sites must accept either class.
PIPESHUB_CLIENT_ERRORS: tuple[type[Exception], ...] = tuple(
    {pipeshub_client.PipeshubClientError, _namespaced_client.PipeshubClientError},
)


class UserSession(pipeshub_client.PipeshubClient):
    def __init__(self, base_url: str, *, email: str, password: str, timeout_seconds: int = 60) -> None:
        super().__init__(base_url=base_url, timeout_seconds=timeout_seconds)
        self._email = email
        self._password = password
        self._token_lock = threading.RLock()

    def _fetch_access_token(self) -> None:
        accounts = UserAccountClient(self)
        init = accounts.init_auth(self._email)
        session_token = init.headers.get("x-session-token")
        if init.status_code >= 400 or not session_token:
            raise pipeshub_client.PipeshubAuthError(f"initAuth failed: HTTP {init.status_code}")
        auth = accounts.authenticate(session_token, self._email, self._password)
        if auth.status_code >= 400:
            raise pipeshub_client.PipeshubAuthError(f"login failed: HTTP {auth.status_code}")
        token = (auth.json() or {}).get("accessToken")
        if not token:
            raise pipeshub_client.PipeshubAuthError("login response carried no accessToken")
        self._access_token = str(token)
        self._token_claims = None
        self._set_token_expiry_from_token_response({})

    def _ensure_access_token(self) -> None:
        with self._token_lock:
            super()._ensure_access_token()


class ConnectorApiError(FramesError):
    """A call to the Python connector service failed."""


class ConnectorApi:
    """The Python connector service (:8088), called with the same user token."""

    def __init__(self, session: UserSession, base_url: str, *, timeout_s: float = 60.0) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    @http_retry()
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = raise_for_transient(requests.get(
            f"{self._base_url}{path}", params=params,
            headers=self._session.auth_headers, timeout=self._timeout_s,
        ))
        if resp.status_code >= 400:
            raise ConnectorApiError(f"GET {path}: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json()
