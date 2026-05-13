"""
Fixtures for the enterprise_search integration tests.

Auth: log in with PIPESHUB_TEST_USER_EMAIL + PIPESHUB_TEST_USER_PASSWORD
(loaded by integration-tests/conftest.py from .env.local) and yield the
resulting JWT plus pre-built Authorization headers.

Once per session, ``seeded_kb_id`` creates a KB and uploads one PDF via
the ``/upload-from-url`` endpoint, then deletes the KB on teardown.

Raise ``PIPESHUB_TEST_TIMEOUT`` (seconds) if requests time out on slow dev hosts.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import requests


_INDEXING_TERMINAL_OK = {"COMPLETED"}
_INDEXING_TERMINAL_FAIL = {
    "FAILED",
    "FILE_TYPE_NOT_SUPPORTED",
    "AUTO_INDEX_OFF",
    "EMPTY",
    "ENABLE_MULTIMODAL_MODELS",
}


def _wait_for_kb_indexing(
    base_url: str,
    kb_id: str,
    headers: dict[str, str],
    timeout: int,
    max_wait_s: int,
    poll_interval_s: float,
) -> None:
    """Poll KB records until the seeded file reaches a terminal indexing status."""
    deadline = time.monotonic() + max_wait_s
    last_status = "<none>"
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{base_url}/api/v1/knowledgeBase/{kb_id}/records",
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code == 200:
            records = resp.json().get("records") or []
            if records:
                statuses = [r.get("indexingStatus") for r in records]
                last_status = ",".join(s or "<none>" for s in statuses)
                if any(s in _INDEXING_TERMINAL_OK for s in statuses):
                    return
                if statuses and all(
                    s in (_INDEXING_TERMINAL_OK | _INDEXING_TERMINAL_FAIL)
                    for s in statuses
                ):
                    pytest.fail(
                        f"seeded doc reached non-COMPLETED terminal status: {last_status}"
                    )
        time.sleep(poll_interval_s)
    pytest.fail(
        f"seeded doc did not finish indexing within {max_wait_s}s "
        f"(last status: {last_status})"
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not url:
        pytest.fail("PIPESHUB_BASE_URL is not set in the environment")
    return url


@pytest.fixture(scope="session")
def timeout() -> int:
    return int(os.getenv("PIPESHUB_TEST_TIMEOUT", "60"))


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


@pytest.fixture(scope="session")
def seeded_kb_id(
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

        max_wait_s = int(os.getenv("PIPESHUB_TEST_INDEX_WAIT", "300"))
        poll_interval_s = float(os.getenv("PIPESHUB_TEST_INDEX_POLL", "3"))
        _wait_for_kb_indexing(
            base_url=base_url,
            kb_id=kb_id,
            headers=session_auth_headers,
            timeout=timeout,
            max_wait_s=max_wait_s,
            poll_interval_s=poll_interval_s,
        )
        yield kb_id
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
