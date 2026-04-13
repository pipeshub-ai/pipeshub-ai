"""
Auth integration tests — tests 22-24.

Applies to all storage routes; we use GET /:documentId as the canary.
"""

from __future__ import annotations
import os
import pytest
import requests

FAKE_DOC_ID = "000000000000000000000001"


def _base_url() -> str:
    return os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")


def _doc_url(doc_id: str = FAKE_DOC_ID) -> str:
    return f"{_base_url()}/api/v1/document/{doc_id}"


# Test 22 — no Authorization header
def test_no_auth_header():
    resp = requests.get(_doc_url(), timeout=30)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# Test 23 — malformed / expired token
def test_malformed_token():
    resp = requests.get(
        _doc_url(),
        headers={"Authorization": "Bearer this.is.not.a.valid.token"},
        timeout=30,
    )
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_expired_token():
    # A syntactically valid JWT whose signature is wrong / expired
    expired = (
        "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxfQ"
        ".invalidsignature"
    )
    resp = requests.get(_doc_url(), headers={"Authorization": expired}, timeout=30)
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
