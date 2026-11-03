"""
Auth integration tests — tests 22-24, plus the user-token lockdown.

Storage scopes documents by org only, not by record permissions, so a user
token must never reach it: the user-facing routes are gone and the internal
ones accept only the storage service token.
"""

from __future__ import annotations
import os
import pytest
import requests

FAKE_DOC_ID = "000000000000000000000001"

# Every operation the storage router used to expose to user tokens.
FORMER_USER_ROUTES = [
    ("POST", "/upload"),
    ("POST", "/placeholder"),
    ("GET", f"/{FAKE_DOC_ID}"),
    ("DELETE", f"/{FAKE_DOC_ID}/"),
    ("GET", f"/{FAKE_DOC_ID}/download"),
    ("GET", f"/{FAKE_DOC_ID}/buffer"),
    ("PUT", f"/{FAKE_DOC_ID}/buffer"),
    ("POST", f"/{FAKE_DOC_ID}/uploadNextVersion"),
    ("POST", f"/{FAKE_DOC_ID}/rollBack"),
    ("POST", f"/{FAKE_DOC_ID}/directUpload"),
    ("GET", f"/{FAKE_DOC_ID}/isModified"),
]


def _base_url() -> str:
    return os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")


def _doc_url(doc_id: str = FAKE_DOC_ID) -> str:
    return f"{_base_url()}/api/v1/document/internal/{doc_id}"


# Test 22 — no Authorization header
@pytest.mark.integration
@pytest.mark.storage
def test_no_auth_header():
    resp = requests.get(_doc_url(), timeout=30)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# Test 23 — malformed / expired token
@pytest.mark.integration
@pytest.mark.storage
def test_malformed_token():
    resp = requests.get(
        _doc_url(),
        headers={"Authorization": "Bearer this.is.not.a.valid.token"},
        timeout=30,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


@pytest.mark.integration
@pytest.mark.storage
def test_expired_token():
    # A syntactically valid JWT whose signature is wrong / expired
    expired = (
        "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxfQ"
        ".invalidsignature"
    )
    resp = requests.get(_doc_url(), headers={"Authorization": expired}, timeout=30)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


@pytest.mark.integration
@pytest.mark.storage
def test_user_token_is_refused_by_internal_routes(pipeshub_client):
    resp = requests.get(_doc_url(), headers=pipeshub_client._headers(), timeout=30)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


@pytest.mark.integration
@pytest.mark.storage
@pytest.mark.parametrize(("method", "path"), FORMER_USER_ROUTES)
def test_former_user_routes_are_gone(pipeshub_client, method, path):
    # 404 rather than the dashboard's HTML shell, which unknown GETs fall back to.
    resp = requests.request(
        method,
        f"{_base_url()}/api/v1/document{path}",
        headers={**pipeshub_client._headers(), "Content-Type": "application/json"},
        data=None if method == "GET" else "{}",
        timeout=30,
    )
    assert resp.status_code == 404, f"{method} {path}: expected 404, got {resp.status_code}: {resp.text[:200]}"
    assert "<html" not in resp.text.lower()
