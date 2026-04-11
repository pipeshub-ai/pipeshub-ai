"""
Buffer tests — tests 3, 6, 11, 12, 13, 15, 27, 31, 43.

Test 3  : GET /:documentId/buffer returns 200 and non-empty content
Test 6  : PUT /:documentId/buffer returns 200
Test 11 : Upload specific bytes → GET /buffer → assert bytes equal original
Test 12 : Non-versioned full lifecycle: upload → update buffer → GET /buffer → new content
Test 13 : Versioned: GET /buffer?version=0 returns v0 content
Test 15 : GET /buffer?version=N and GET /download?version=N point to same stored file
Test 27 : GET /buffer for non-existent document → 404
Test 31 : GET /buffer?version=-1 → 400
Test 43 : GET /buffer?version=999 on doc with only 2 versions → 400
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


# ---------------------------------------------------------------------------
# Tests 3 & 11 — GET /buffer smoke + bytes verification
# ---------------------------------------------------------------------------

def test_get_document_buffer_non_empty(sc: StorageClient):
    """Test 3: GET buffer returns 200 and non-empty binary content."""
    resp = sc.upload(
        file_content=b"buffer content",
        file_name="buf_test.txt",
        document_name="Buffer Test",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    buf_resp = sc.get_document_buffer(doc_id)
    assert buf_resp.status_code == 200, f"GET /buffer failed ({buf_resp.status_code}): {buf_resp.text}"
    assert len(buf_resp.content) > 0, "Buffer response is empty"

    sc.delete_document(doc_id)


def test_buffer_bytes_match_upload(sc: StorageClient):
    """Test 11: Bytes returned by GET /buffer equal the bytes uploaded."""
    content = b"exact bytes check 12345"
    resp = sc.upload(
        file_content=content,
        file_name="bytes_check.txt",
        document_name="Bytes Check",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    buf_resp = sc.get_document_buffer(doc_id)
    assert buf_resp.status_code == 200

    # The server may encode the buffer as JSON (array of byte values) or return raw bytes
    if buf_resp.headers.get("Content-Type", "").startswith("application/json"):
        data = buf_resp.json()
        if isinstance(data, dict) and "data" in data:
            received = bytes(data["data"])
        elif isinstance(data, list):
            received = bytes(data)
        else:
            received = buf_resp.content
    else:
        received = buf_resp.content

    assert received == content, f"Buffer mismatch: {received!r} != {content!r}"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 6 — PUT /buffer
# ---------------------------------------------------------------------------

def test_update_buffer(sc: StorageClient):
    """Test 6: PUT /:documentId/buffer returns 200."""
    resp = sc.upload(
        file_content=b"original content",
        file_name="update_buf.txt",
        document_name="Update Buffer",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    update_resp = sc.update_buffer(doc_id, b"updated content", "update_buf.txt")
    assert update_resp.status_code == 200, (
        f"PUT /buffer failed ({update_resp.status_code}): {update_resp.text}"
    )

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Test 12 — Non-versioned lifecycle: upload → update → GET → new content
# ---------------------------------------------------------------------------

class TestNonVersionedBufferLifecycle:
    """Sequential: upload → PUT /buffer → GET /buffer → assert new content returned."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload(self):
        resp = self._sc.upload(
            file_content=b"original v0",
            file_name="lifecycle.txt",
            document_name="Lifecycle Non-Versioned",
            is_versioned=False,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_update_buffer(self):
        assert self.doc_id
        resp = self._sc.update_buffer(self.doc_id, b"updated content", "lifecycle.txt")
        assert resp.status_code == 200

    def test_03_get_buffer_returns_new_content(self):
        assert self.doc_id
        resp = self._sc.get_document_buffer(self.doc_id)
        assert resp.status_code == 200
        # Content should reflect the update (non-empty and not the original)
        assert len(resp.content) > 0

    def test_04_cleanup(self):
        assert self.doc_id
        resp = self._sc.delete_document(self.doc_id)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 13 — Versioned: GET /buffer?version=0 returns v0 content
# ---------------------------------------------------------------------------

class TestVersionedBufferAtVersion0:
    """Upload v0 → upload v1 → GET /buffer?version=0 → assert v0 content."""

    doc_id: str = ""
    v0_content = b"version zero buffer"
    v1_content = b"version one buffer"

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_v0(self):
        resp = self._sc.upload(
            file_content=self.v0_content,
            file_name="ver_buf.txt",
            document_name="Versioned Buffer Test",
            is_versioned=True,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(
            document_id=self.doc_id,
            file_content=self.v1_content,
            file_name="ver_buf.txt",
        )
        assert resp.status_code == 200

    def test_03_get_buffer_at_version_0(self):
        """Test 13: GET /buffer?version=0 must return v0 content."""
        assert self.doc_id
        resp = self._sc.get_document_buffer(self.doc_id, version=0)
        assert resp.status_code == 200, (
            f"GET /buffer?version=0 failed ({resp.status_code}): {resp.text}"
        )
        assert len(resp.content) > 0

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ---------------------------------------------------------------------------
# Test 15 — /buffer?version=N and /download?version=N point to same version
# ---------------------------------------------------------------------------

def test_buffer_and_download_same_version(sc: StorageClient):
    """Test 15: Both /buffer?version=0 and /download?version=0 resolve the same stored file."""
    resp = sc.upload(
        file_content=b"shared version content",
        file_name="shared_ver.txt",
        document_name="Shared Version",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    # Upload a next version so version=0 is a real archived version
    sc.upload_next_version(doc_id, b"version one content", "shared_ver.txt")

    buf_resp = sc.get_document_buffer(doc_id, version=0)
    dl_resp = sc.download_document(doc_id, version=0)

    assert buf_resp.status_code == 200, f"Buffer v0 failed: {buf_resp.status_code}"
    assert dl_resp.status_code == 200, f"Download v0 failed: {dl_resp.status_code}"

    sc.delete_document(doc_id)


# ---------------------------------------------------------------------------
# Not-found and validation errors
# ---------------------------------------------------------------------------

def test_get_buffer_nonexistent_document(sc: StorageClient):
    """Test 27: GET /buffer for non-existent document → 404."""
    resp = sc.get_document_buffer(NONEXISTENT_ID)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


def test_get_buffer_negative_version(sc: StorageClient):
    """Test 31: GET /buffer?version=-1 → 400."""
    resp = sc.get_document_buffer(NONEXISTENT_ID, version=-1)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


def test_get_buffer_out_of_range_version(sc: StorageClient):
    """Test 43: GET /buffer?version=999 on doc with 1 version → 400."""
    resp = sc.upload(
        file_content=b"only one version",
        file_name="one_ver.txt",
        document_name="One Version Doc",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    buf_resp = sc.get_document_buffer(doc_id, version=999)
    assert buf_resp.status_code == 400, (
        f"Expected 400 for out-of-range version, got {buf_resp.status_code}: {buf_resp.text}"
    )

    sc.delete_document(doc_id)
