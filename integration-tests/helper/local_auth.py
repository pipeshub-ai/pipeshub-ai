"""
Obtain OAuth client credentials from the local Pipeshub backend for integration tests.

When PIPESHUB_TEST_ENV=local and CLIENT_ID/CLIENT_SECRET are not set, tests can call
obtain_local_oauth_credentials(base_url) to log in with a test user (org admin),
create an OAuth app with client_credentials grant, and return (client_id, client_secret).
"""

import os
from typing import Tuple

import requests


def obtain_local_oauth_credentials(base_url: str, timeout: int = 30) -> Tuple[str, str]:
    """
    Log in to the backend, create an OAuth app with client_credentials, return (client_id, client_secret).

    Requires PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD in the environment.
    The user must be an org admin so that POST /api/v1/oauth-clients succeeds.

    Raises:
        RuntimeError: If env vars are missing or any backend call fails.
    """
    base_url = base_url.rstrip("/")
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD must be set in .env.local "
            "to obtain OAuth credentials automatically (user must be an org admin)."
        )

    session_token = _init_auth(base_url, email, timeout)
    access_token = _authenticate(base_url, session_token, email, password, timeout)
    client_id, client_secret = _create_oauth_app(base_url, access_token, timeout)
    return client_id, client_secret


def _init_auth(base_url: str, email: str, timeout: int) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"initAuth failed: HTTP {resp.status_code}")
    session_token = resp.headers.get("x-session-token")
    if not session_token:
        raise RuntimeError(
            "initAuth did not return x-session-token header - check backend response"
        )
    return session_token


def _authenticate(
    base_url: str,
    session_token: str,
    email: str,
    password: str,
    timeout: int,
) -> str:
    resp = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"authenticate failed: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("authenticate returned non-JSON response")
    access_token = data.get("accessToken")
    if not access_token:
        raise RuntimeError(
            f"authenticate did not return accessToken: {list(data.keys())}"
        )
    return access_token


def _create_oauth_app(base_url: str, access_token: str, timeout: int) -> Tuple[str, str]:
    resp = requests.post(
        f"{base_url}/api/v1/oauth-clients",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "name": "Integration Test Client",
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": [
                "connector:read",
                "connector:write",
                "connector:sync",
                "connector:delete",
            ],
        },
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"create OAuth app failed: HTTP {resp.status_code} (user may not be org admin)"
        )
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("oauth-clients returned non-JSON response")
    app = data.get("app") or {}
    client_id = app.get("clientId")
    client_secret = app.get("clientSecret")
    if not client_id or not client_secret:
        raise RuntimeError(
            f"oauth-clients response missing clientId/clientSecret: {list(app.keys())}"
        )
    return client_id, client_secret
