"""
Session JWT helpers for response-validation auth integration tests.

initAuth + password authenticate against ``/api/v1/userAccount``; shared by
userAccount route tests and orgAuthConfig admin routes.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    from helper.clients.auth_client import UserAccountClient
    from helper.pipeshub_client import PipeshubClient

__all__ = [
    "authenticate_password",
    "init_auth",
    "login_with_user",
    "obtain_session_access_token",
    "require_test_user_credentials",
    "session_headers",
]


def _account_client(client: "PipeshubClient | UserAccountClient") -> "UserAccountClient":
    from helper.clients.auth_client import UserAccountClient

    if isinstance(client, UserAccountClient):
        return client
    return UserAccountClient(client)


def session_headers(access_token: str) -> dict[str, str]:
    """Build headers for session-based JWT auth."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _test_user_credentials_from_env(
) -> tuple[str, str] | None:
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        return None
    return email, password


def require_test_user_credentials() -> tuple[str, str]:
    """Return (email, password) from env or raise AssertionError."""
    creds = _test_user_credentials_from_env()
    assert creds is not None, (
        "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD required "
        "for userAccount tests"
    )
    return creds


def obtain_session_access_token(
    client: "PipeshubClient | UserAccountClient",
    timeout: int | None = None,
) -> str:
    """
    Read env creds; pytest.skip if missing; initAuth + authenticate; return access only.
    """
    _ = timeout  # legacy; HTTP timeout comes from the client
    creds = _test_user_credentials_from_env()
    if creds is None:
        pytest.skip(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD required "
            "for orgAuthConfig tests (session-based JWT auth)"
        )
    email, password = creds
    access_token, _ = login_with_user(client, email, password)
    return access_token


def init_auth(
    client: "PipeshubClient | UserAccountClient",
    email: str,
    timeout: int | None = None,
) -> requests.Response:
    """POST /api/v1/userAccount/initAuth and return the raw response."""
    _ = timeout
    return _account_client(client).init_auth(email)


def authenticate_password(
    client: "PipeshubClient | UserAccountClient",
    session_token: str,
    email: str,
    password: str,
    timeout: int | None = None,
    *,
    extra_json: dict | None = None,
) -> requests.Response:
    """POST /api/v1/userAccount/authenticate and return the raw response."""
    _ = timeout
    return _account_client(client).authenticate(
        session_token, email, password, extra_json=extra_json
    )


def login_with_user(
    client: "PipeshubClient | UserAccountClient",
    email: str,
    password: str,
    timeout: int | None = None,
) -> tuple[str, str]:
    """initAuth + authenticate; return (accessToken, refreshToken).

    Use ``access_token, _ = login_with_user(...)`` when only the access token is needed.

    Raises AssertionError if any step fails.
    """
    _ = timeout
    account = _account_client(client)
    init_resp = account.init_auth(email)
    assert init_resp.status_code == 200, (
        f"initAuth failed: {init_resp.status_code}: {init_resp.text}"
    )
    session_token = init_resp.headers.get("x-session-token")
    assert session_token, "initAuth did not return x-session-token"

    auth_resp = account.authenticate(session_token, email, password)
    assert auth_resp.status_code == 200, (
        f"authenticate failed: {auth_resp.status_code}: {auth_resp.text}"
    )
    authenticate_response_json = auth_resp.json()
    access_token = authenticate_response_json.get("accessToken")
    refresh_token = authenticate_response_json.get("refreshToken")
    assert access_token, (
        f"authenticate did not return accessToken: "
        f"{list(authenticate_response_json.keys())}"
    )
    assert refresh_token, (
        f"authenticate did not return refreshToken: "
        f"{list(authenticate_response_json.keys())}"
    )
    return access_token, refresh_token
