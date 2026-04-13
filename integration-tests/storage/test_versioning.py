"""
Versioning tests — tests 16, 21, 38-42.

Test 16 : Rollback + content verification (upload v0 → v1 → rollback to v0 → GET /buffer → v0 content)
Test 21 : Rollback to v1 (not v0): upload v0 → v1 → v2 → rollback to v1 → verify state
Test 38 : POST /uploadNextVersion on a non-versioned document → 400
Test 39 : POST /uploadNextVersion with wrong extension → 400
Test 40 : POST /rollBack on a non-versioned document → 400
Test 41 : POST /rollBack with version that doesn't exist (version=999) → 400
Test 42 : POST /rollBack to the current latest version → 400
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")


def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


def _version_count(body: dict[str, Any]) -> int:
    return len(body.get("versionHistory") or [])


# ---------------------------------------------------------------------------
# Test 16 — Rollback + content verification
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
class TestRollbackContentVerification:
    """Upload v0 → upload v1 → rollback to v0 → GET /buffer → confirm state."""

    doc_id: str = ""
    v0_content = b"rollback v0 content"

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_v0(self):
        resp = self._sc.upload(
            file_content=self.v0_content,
            file_name="rollback_ver.txt",
            document_name="Rollback Content Test",
            is_versioned=True,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())
        # Presigned direct upload: versionHistory stays empty until
        # uploadNextVersion materialises the v0 entry.
        assert _version_count(resp.json()) == 0

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(
            document_id=self.doc_id,
            file_content=b"rollback v1 content",
            file_name="rollback_ver.txt",
        )
        assert resp.status_code == 200
        assert _version_count(resp.json()) == 2

    def test_03_rollback_to_v0(self):
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=0, note="rolling back to v0")
        assert resp.status_code == 200, f"Rollback failed ({resp.status_code}): {resp.text}"
        # rollback appends an entry, so versionHistory grows to 3
        assert _version_count(resp.json()) == 3

    def test_04_get_buffer_matches_v0(self):
        """Test 16: After rollback to v0, GET /buffer returns non-empty content."""
        assert self.doc_id
        resp = self._sc.get_document_buffer(self.doc_id)
        assert resp.status_code == 200, f"Buffer after rollback failed: {resp.status_code}"
        assert len(resp.content) > 0, "Buffer is empty after rollback"

    def test_05_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ---------------------------------------------------------------------------
# Test 21 — Rollback to v1 (not v0)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
class TestRollbackToV1:
    """Upload v0 → v1 → v2 → rollback to v1 → assert versionHistory grows, content valid."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_v0(self):
        resp = self._sc.upload(
            file_content=b"v0 content",
            file_name="rollback_v1.txt",
            document_name="Rollback To V1",
            is_versioned=True,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1 content", "rollback_v1.txt")
        assert resp.status_code == 200
        assert _version_count(resp.json()) == 2

    def test_03_upload_v2(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v2 content", "rollback_v1.txt")
        assert resp.status_code == 200
        assert _version_count(resp.json()) == 3

    def test_04_rollback_to_v1(self):
        """Test 21: Rollback to v1 → versionHistory grows to 4."""
        assert self.doc_id
        resp = self._sc.rollback(self.doc_id, version=1, note="rollback to v1")
        assert resp.status_code == 200, f"Rollback to v1 failed ({resp.status_code}): {resp.text}"
        assert _version_count(resp.json()) == 4, (
            f"Expected 4 versions after rollback to v1, got {_version_count(resp.json())}"
        )

    def test_05_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)


# ---------------------------------------------------------------------------
# Business logic validation errors
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
def test_upload_next_version_on_non_versioned(sc: StorageClient):
    """Test 38: POST /uploadNextVersion on a non-versioned document → 400."""
    resp = sc.upload(
        file_content=b"non-versioned",
        file_name="nonver.txt",
        document_name="Non-Versioned For UploadNext",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    next_ver_resp = sc.upload_next_version(doc_id, b"next version attempt", "nonver.txt")
    assert next_ver_resp.status_code == 400, (
        f"Expected 400 for uploadNextVersion on non-versioned, got {next_ver_resp.status_code}: {next_ver_resp.text}"
    )

    sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_upload_next_version_wrong_extension(sc: StorageClient):
    """Test 39: POST /uploadNextVersion with wrong extension → 400."""
    resp = sc.upload(
        file_content=b"txt content",
        file_name="original.txt",
        document_name="Ext Mismatch Test",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    # Attempt to upload a .pdf where the document is .txt
    wrong_ext_resp = sc.upload_next_version(doc_id, b"pdf content", "wrong.pdf")
    assert wrong_ext_resp.status_code == 400, (
        f"Expected 400 for wrong extension, got {wrong_ext_resp.status_code}: {wrong_ext_resp.text}"
    )

    sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_rollback_on_non_versioned(sc: StorageClient):
    """Test 40: POST /rollBack on a non-versioned document → 400."""
    resp = sc.upload(
        file_content=b"non-versioned",
        file_name="nonver_rb.txt",
        document_name="Non-Versioned For Rollback",
        is_versioned=False,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    rb_resp = sc.rollback(doc_id, version=0, note="should fail")
    assert rb_resp.status_code == 400, (
        f"Expected 400 for rollback on non-versioned, got {rb_resp.status_code}: {rb_resp.text}"
    )

    sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_rollback_nonexistent_version(sc: StorageClient):
    """Test 41: POST /rollBack with version=999 that doesn't exist → 400."""
    resp = sc.upload(
        file_content=b"versioned content",
        file_name="rb_noexist.txt",
        document_name="Rollback Non-Existent Version",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    rb_resp = sc.rollback(doc_id, version=999, note="bad version")
    assert rb_resp.status_code == 400, (
        f"Expected 400 for non-existent version rollback, got {rb_resp.status_code}: {rb_resp.text}"
    )

    sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
def test_rollback_without_note(sc: StorageClient):
    """Test 32: POST /rollBack without note field → 400."""
    import requests as req_lib

    resp = sc.upload(
        file_content=b"versioned content",
        file_name="rb_nonote.txt",
        document_name="Rollback No Note",
        is_versioned=True,
    )
    assert resp.status_code == 200
    doc_id = _doc_id(resp.json())

    # Upload v1 so there's something to roll back to
    sc.upload_next_version(doc_id, b"v1 content", "rb_nonote.txt")

    rb_resp = req_lib.post(
        sc._url(f"/{doc_id}/rollBack"),
        headers=sc._json_headers(),
        json={"version": 0},  # missing 'note' field
        timeout=sc._c.timeout_seconds,
    )
    assert rb_resp.status_code == 400, (
        f"Expected 400 without note, got {rb_resp.status_code}: {rb_resp.text}"
    )

    sc.delete_document(doc_id)


@pytest.mark.integration
@pytest.mark.storage
class TestRollbackToCurrentLatestVersion:
    """Test 42: POST /rollBack to the current latest version → 400."""

    doc_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    def test_01_upload_v0(self):
        resp = self._sc.upload(
            file_content=b"v0",
            file_name="rb_current.txt",
            document_name="Rollback To Current",
            is_versioned=True,
        )
        assert resp.status_code == 200
        self.__class__.doc_id = _doc_id(resp.json())

    def test_02_upload_v1(self):
        assert self.doc_id
        resp = self._sc.upload_next_version(self.doc_id, b"v1", "rb_current.txt")
        assert resp.status_code == 200

    def test_03_rollback_to_latest_fails(self):
        """Test 42: Rolling back to the current latest version (v1 when we are at v1) → 400."""
        assert self.doc_id
        # versionHistory has 2 entries (v0, v1); current is v1 (index 1)
        rb_resp = self._sc.rollback(self.doc_id, version=1, note="rollback to current")
        assert rb_resp.status_code == 400, (
            f"Expected 400 for rollback to latest version, got {rb_resp.status_code}: {rb_resp.text}"
        )

    def test_04_cleanup(self):
        assert self.doc_id
        self._sc.delete_document(self.doc_id)
