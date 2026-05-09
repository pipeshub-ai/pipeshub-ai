"""
Conftest for enterprise_search integration tests.

Provides session-scoped fixtures used across all enterprise_search modules:

  base_url             — PIPESHUB_BASE_URL from env
  timeout              — PIPESHUB_TEST_TIMEOUT from env (default 60s)
  session_access_token — JWT obtained via initAuth → authenticate
  session_auth_headers — ready-to-use Authorization headers dict

  _seed_kb_doc_from_url (autouse) — once per session, creates a KB, uploads one doc
    via URL (all enterprise_search tests share this single file), then deletes the
    KB on teardown.

Auth uses email + password (session JWT).  Set in .env.local:
  PIPESHUB_TEST_USER_EMAIL
  PIPESHUB_TEST_USER_PASSWORD
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

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


# Seed doc shared across all enterprise_search tests. To swap the file, just
# update SEED_DOC_PATH (relative path inside the integration-test repo).
SEED_DOC_REPO_RAW_BASE = (
    "https://raw.githubusercontent.com/pipeshub-ai/integration-test/main/"
)
SEED_DOC_PATH = (
    "sample-data/entities/enterprise-search/"
    "Asana Disaster Recovery Summary Report (2023-08).pdf"
)
SEED_DOC_URL = SEED_DOC_REPO_RAW_BASE + quote(SEED_DOC_PATH)
SEED_DOC_FILENAME = SEED_DOC_PATH.rsplit("/", 1)[-1]


@pytest.fixture(scope="session", autouse=True)
def _seed_kb_doc_from_url(
    base_url: str, session_auth_headers: dict[str, str], timeout: int
):
    """Create a KB + upload one doc via URL for the whole session; delete KB on teardown."""
    r = requests.post(
        f"{base_url}/api/v1/knowledgeBase/",
        headers=session_auth_headers,
        json={"kbName": f"it-search-{uuid.uuid4().hex[:8]}"},
        timeout=timeout,
    )
    assert r.status_code == 200, f"create KB: {r.status_code} {r.text}"
    data = r.json()
    kb_id = (
        data.get("id")
        or data.get("kbId")
        or (data.get("data") or {}).get("id")
        or (data.get("data") or {}).get("kbId")
    )
    assert kb_id, f"no KB id in {data!r}"

    try:
        up = requests.post(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/upload-from-url",
            headers=session_auth_headers,
            json={"url": SEED_DOC_URL, "fileName": SEED_DOC_FILENAME},
            timeout=timeout,
        )
        assert up.status_code == 200, f"upload-from-url: {up.status_code} {up.text}"
        yield
    finally:
        # Best-effort teardown — don't fail the session on cleanup hiccups.
        try:
            requests.delete(
                f"{base_url}/api/v1/knowledgeBase/{kb_id}",
                headers=session_auth_headers,
                timeout=timeout,
            )
        except requests.RequestException:
            pass
