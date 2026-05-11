"""
Fixtures for the enterprise_search integration tests.

Auth: log in with PIPESHUB_TEST_USER_EMAIL + PIPESHUB_TEST_USER_PASSWORD
(loaded by integration-tests/conftest.py from .env.local) and yield the
resulting JWT plus pre-built Authorization headers.

No KB seeding, no document upload, no scoped-token paths. The user runs
these tests against a local server that already has data indexed.

Raise ``PIPESHUB_TEST_TIMEOUT`` (seconds) if requests time out on slow dev hosts.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not url:
        pytest.fail("PIPESHUB_BASE_URL is not set in the environment")
    return url


@pytest.fixture(scope="session")
def timeout() -> int:
    return int(os.getenv("PIPESHUB_TEST_TIMEOUT", "180"))


@pytest.fixture(scope="session")
def session_access_token(base_url: str, timeout: int) -> str:
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        pytest.fail(
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
        json={
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        },
        timeout=timeout,
    )
    assert auth.status_code == 200, f"authenticate failed: {auth.status_code} {auth.text}"

    data: dict[str, Any] = auth.json()
    token = data.get("accessToken")
    assert token, f"authenticate response missing accessToken; keys={list(data.keys())}"
    return str(token)


@pytest.fixture(scope="session")
def session_auth_headers(session_access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {session_access_token}",
        "Content-Type": "application/json",
    }
