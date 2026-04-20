"""
Storage integration test: upload -> version -> rollback lifecycle.

Steps (sequential):
  1. Upload a versioned document          -> versionHistory len = 0
     (presigned direct upload: the v0 entry is created lazily, on the
     first uploadNextVersion call, not on upload itself)
  2. Upload next version                  -> versionHistory len = 2  (v0 + v1)
  3. Upload next version again            -> versionHistory len = 3  (v2)
  4. Rollback to v0                       -> versionHistory len = 4  (rollback appended)
  5. Verify document state via GET
  6. Delete document (cleanup)

Run:
    cd integration-tests
    pytest storage/test_upload_rollback.py -v
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from storage_client import StorageClient

logger = logging.getLogger("storage-integration")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_id(body: dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id") or body.get("documentId"))


def _version_count(body: dict[str, Any]) -> int:
    return len(body.get("versionHistory") or [])


def _expected_initial_version_count(storage_vendor: str) -> int:
    """Local currently creates v0 eagerly; s3 keeps lazy v0 materialization."""
    return 1 if storage_vendor == "local" else 0


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.storage
class TestUploadAndRollback:
    """
    Sequential lifecycle: upload -> next version x2 -> rollback -> cleanup.
    State is stored as class attributes; pytest runs methods in definition order.
    """

    document_id: str = ""

    @pytest.fixture(autouse=True)
    def _bind(self, sc: StorageClient) -> None:
        self.__class__._sc = sc

    # ------------------------------------------------------------------ step 1
    def test_01_upload_versioned_document(self) -> None:
        """Upload a versioned .txt document; expect 200 and backend-specific initial version count."""
        resp = self._sc.upload(
            file_content=b"version zero content",
            file_name="test_doc.txt",
            document_name="Integration Test Doc",
            is_versioned=True,
        )

        assert resp.status_code == 200, f"Upload failed ({resp.status_code}): {resp.text}"

        body = resp.json()
        doc_id = _doc_id(body)
        assert doc_id and doc_id != "None", f"No documentId in response: {body}"
        self.__class__.document_id = doc_id

        assert body.get("isVersionedFile") is True
        expected_initial = _expected_initial_version_count(
            str(body.get("storageVendor", "")).lower()
        )
        assert _version_count(body) == expected_initial, (
            f"Expected {expected_initial} versions right after upload, got {_version_count(body)}"
        )
        logger.info("Created document %s", doc_id)

    # ------------------------------------------------------------------ step 2
    def test_02_upload_next_version(self) -> None:
        """Upload v1; versionHistory should grow to 2."""
        assert self.document_id, "document_id not set — did test_01 pass?"

        resp = self._sc.upload_next_version(
            document_id=self.document_id,
            file_content=b"version one content",
            file_name="test_doc.txt",
            current_version_note="initial baseline",
            next_version_note="first update",
        )

        assert resp.status_code == 200, f"uploadNextVersion failed ({resp.status_code}): {resp.text}"
        assert _version_count(resp.json()) == 2, (
            f"Expected 2 versions, got {_version_count(resp.json())}"
        )

    # ------------------------------------------------------------------ step 3
    def test_03_upload_second_next_version(self) -> None:
        """Upload v2; versionHistory should grow to 3."""
        assert self.document_id, "document_id not set — did test_01 pass?"

        resp = self._sc.upload_next_version(
            document_id=self.document_id,
            file_content=b"version two content",
            file_name="test_doc.txt",
            current_version_note="first update",
            next_version_note="second update",
        )

        assert resp.status_code == 200, f"uploadNextVersion (v2) failed ({resp.status_code}): {resp.text}"
        assert _version_count(resp.json()) == 3, (
            f"Expected 3 versions, got {_version_count(resp.json())}"
        )

    # ------------------------------------------------------------------ step 4
    def test_04_rollback_to_v0(self) -> None:
        """Rollback to v0; rollback appends a new entry so versionHistory len = 4."""
        assert self.document_id, "document_id not set — did test_01 pass?"

        resp = self._sc.rollback(
            document_id=self.document_id,
            version=0,
            note="rolling back to original",
        )

        assert resp.status_code == 200, f"rollBack failed ({resp.status_code}): {resp.text}"
        assert _version_count(resp.json()) == 4, (
            f"Expected 4 versions after rollback, got {_version_count(resp.json())}"
        )

    # ------------------------------------------------------------------ step 5
    def test_05_verify_document_state(self) -> None:
        """GET document — verify it exists and version count is still 4."""
        assert self.document_id, "document_id not set — did test_01 pass?"

        resp = self._sc.get_document(self.document_id)
        assert resp.status_code == 200, f"getDocument failed ({resp.status_code}): {resp.text}"

        body = resp.json()
        assert _doc_id(body) == self.document_id
        assert body.get("isVersionedFile") is True
        assert _version_count(body) == 4, (
            f"Expected 4 versions in final state, got {_version_count(body)}"
        )

    # ------------------------------------------------------------------ step 6
    def test_06_delete_document(self) -> None:
        """Soft-delete; expect 200 and isDeleted=true."""
        assert self.document_id, "document_id not set — did test_01 pass?"

        resp = self._sc.delete_document(self.document_id)
        assert resp.status_code == 200, f"deleteDocument failed ({resp.status_code}): {resp.text}"
        assert resp.json().get("isDeleted") is True, f"Expected isDeleted=true: {resp.json()}"
        logger.info("Document %s deleted", self.document_id)
