"""
Conftest for response-validation integration tests.

Provides session-scoped fixtures used across all response-validation modules:

  base_url             — PIPESHUB_BASE_URL from env
  timeout              — PIPESHUB_TEST_TIMEOUT from env (default 60s)
  session_access_token — JWT obtained via initAuth → authenticate
  session_auth_headers — ready-to-use Authorization headers dict

Auth uses email + password (session JWT).  Set in .env.local:
  PIPESHUB_TEST_USER_EMAIL
  PIPESHUB_TEST_USER_PASSWORD
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests


def _get_base_url() -> str:
    url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not url:
        pytest.skip("PIPESHUB_BASE_URL is not set")
    return url


def _get_timeout() -> int:
    return int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))


@pytest.fixture(scope="session")
def base_url() -> str:
    return _get_base_url()


@pytest.fixture(scope="session")
def timeout() -> int:
    return _get_timeout()


@pytest.fixture(scope="session")
def session_access_token(base_url: str, timeout: int) -> str:
    """Log in with email + password and return the JWT access token."""
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        pytest.skip(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD must be set"
        )

    init = requests.post(
        f"{base_url}/api/v1/userAccount/initAuth",
        json={"email": email},
        timeout=timeout,
    )
    assert init.status_code == 200, f"initAuth failed: {init.status_code} {init.text}"
    session_token = init.headers.get("x-session-token")
    assert session_token, "initAuth did not return x-session-token header"

    auth = requests.post(
        f"{base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session_token},
        json={"method": "password", "credentials": {"password": password}, "email": email},
        timeout=timeout,
    )
    assert auth.status_code == 200, f"authenticate failed: {auth.status_code} {auth.text}"

    data: dict[str, Any] = auth.json()
    token = data.get("accessToken")
    assert token, f"No accessToken in response. Keys: {list(data.keys())}"
    return str(token)


@pytest.fixture(scope="session")
def session_auth_headers(session_access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {session_access_token}",
        "Content-Type": "application/json",
    }
