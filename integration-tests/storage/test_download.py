"""
Download tests — tests 4, 10, 14, 20, 28, 30, 37, 44.

Test 4  : GET /:documentId/download (no version) → 200, has signedUrl or stream
Test 10 : Upload b"hello" → download → content is accessible
Test 14 : Versioned: GET /download?version=0 returns v0 signed URL
Test 20 : GET /download?expirationTimeInSeconds=7200 → 200
Test 28 : GET /download for non-existent document → 404
Test 30 : GET /download?version=-1 → 400
Test 37 : GET /download?expirationTimeInSeconds=0 → 400
Test 44 : GET /download?version=999 on doc with only a few versions → 400
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")

NONEXISTENT_ID = "000000000000000000000001"


def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


def _has_download_payload(resp) -> bool:
    """Return True if response contains a signedUrl or raw binary content."""
    ct = resp.headers.get("Content-Type", "")
    if "application/json" in ct:
        body = resp.json()
        return bool(body.get("signedUrl"))
    # local storage returns a file stream
    return len(resp.content) > 0


# ---------------------------------------------------------------------------
# Test 4 — download smoke test
# ---------------------------------------------------------------------------

def test_download_document_smoke(sc: StorageClient):
    """Test 4: GET /download with no version → 200 and has signedUrl or stream."""
    resp = sc.upload(
        file_content=b"download smoke",
        file_name="dl_smoke.txt",
        document_name="Download Smoke",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    dl_resp = sc.download_document(doc_id)
    assert dl_resp.status_code == 200, f"Download failed ({dl_resp.status_code}): {dl_resp.text}"
    assert _has_download_payload(dl_resp), f"No download payload in response: {dl_resp.text}"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 10 — upload content then download
# ---------------------------------------------------------------------------

def test_upload_then_download_content(sc: StorageClient):
    """Test 10: Upload b'hello', download, assert we get a valid download handle."""
    resp = sc.upload(
        file_content=b"hello",
        file_name="hello.txt",
        document_name="Hello Download",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    dl_resp = sc.download_document(doc_id)
    assert dl_resp.status_code == 200, f"Download failed: {dl_resp.status_code}"
    assert _has_download_payload(dl_resp), "No download payload returned"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 14 — versioned download at version=0
# ---------------------------------------------------------------------------

class TestVersionedDownloadAtV0:
    """Upload v0 → upload v1 → GET /download?version=0 → 200."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_v0(self):
        resp = self._sc.upload(
            file_content=b"v0 download content",
            file_name="dl_ver.txt",
            document_name="Versioned Download",
            is_versioned=True,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(
            document_id=self.doc_id,
            file_content=b"v1 download content",
            file_name="dl_ver.txt",
        )
        assert resp.status_code == 200

    def test_03_download_at_version_0(self):
        """Test 14: GET /download?version=0 returns 200."""
        assert self.doc_id
        resp = self._sc.download_document(self.doc_id, version=0)
        assert resp.status_code == 200, (
            f"Download v0 failed ({resp.status_code}): {resp.text}"
        )
        assert _has_download_payload(resp), f"No download payload for v0: {resp.text}"

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ---------------------------------------------------------------------------
# Test 20 — custom expiration time
# ---------------------------------------------------------------------------

def test_download_with_custom_expiration(sc: StorageClient):
    """Test 20: GET /download?expirationTimeInSeconds=7200 → 200."""
    resp = sc.upload(
        file_content=b"expiry test content",
        file_name="expiry.txt",
        document_name="Expiry Test",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    dl_resp = sc.download_document(doc_id, expiration_seconds=7200)
    assert dl_resp.status_code == 200, (
        f"Download with expiry failed ({dl_resp.status_code}): {dl_resp.text}"
    )

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Not-found and validation errors
# ---------------------------------------------------------------------------

def test_download_nonexistent_document(sc: StorageClient):
    """Test 28: GET /download for non-existent document → 404."""
    resp = sc.download_document(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


def test_download_negative_version(sc: StorageClient):
    """Test 30: GET /download?version=-1 → 400."""
    resp = sc.download_document(NONEXISTENT_ID, version=-1)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_download_zero_expiration(sc: StorageClient):
    """Test 37: GET /download?expirationTimeInSeconds=0 → 400."""
    resp = sc.download_document(NONEXISTENT_ID, expiration_seconds=0)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_download_out_of_range_version(sc: StorageClient):
    """Test 44: GET /download?version=999 on doc with only a couple versions → 400."""
    resp = sc.upload(
        file_content=b"few versions",
        file_name="few_ver.txt",
        document_name="Few Versions",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    dl_resp = sc.download_document(doc_id, version=999)
    assert dl_resp.status_code == 400, (
        f"Expected 400 for out-of-range version, got {dl_resp.status_code}: {dl_resp.text}"
    )

    sc.delete_document(doc_id)
